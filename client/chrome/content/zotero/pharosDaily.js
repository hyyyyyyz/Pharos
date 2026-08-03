/*
	***** BEGIN LICENSE BLOCK *****

	Copyright © 2026 Pharos Contributors
	                 https://pharos.selab.top

	This file is part of Pharos, which is derived from Zotero.

	Pharos is free software: you can redistribute it and/or modify
	it under the terms of the GNU Affero General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	Pharos is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU Affero General Public License for more details.

	You should have received a copy of the GNU Affero General Public License
	along with Pharos.  If not, see <http://www.gnu.org/licenses/>.

	***** END LICENSE BLOCK *****
*/

/**
 * The daily arXiv digest.
 *
 * A panel in the main window, not a window of its own. zoteroPane.js's
 * openPharosDaily() sets the rail's module, and elements/pharosRail.js points
 * <browser id="pharos-view-daily"> at this document. It is still kept out of the
 * item tree -- the digest is a list of papers you do NOT have yet, and mixing
 * them in would blur the line between "my library" and "today's candidates" --
 * but that is a different statement from "its own window", and the difference is
 * load-bearing rather than cosmetic.
 *
 * That browser is given a `src` ONCE and is thereafter only hidden and re-shown,
 * so this document does not unload until the main window does, and every
 * module-level variable below lives as long as the application. Anything cached
 * here that the rest of the session can invalidate has to say how it finds out:
 * _signedOut is derived from hasCredentials() on every render, _inLibrary is
 * re-checked by the item observer registered in _init(), and onShown() is what
 * wakes a panel that started signed out. A cache added without one of those is a
 * bug that first appears in the second hour of a session, which is the hardest
 * kind to attribute.
 *
 * Three panes, matching the web client's module one for one -- a date rail, the
 * day's papers, and everything the model produced about the selected one. The
 * rule the whole file obeys is the same as the web's: FETCHING and READING are
 * separate stages with separate failure modes. arXiv metadata always arrives;
 * the Chinese reading, the highlights and the scores exist only once a provider
 * is configured and has actually run. So no path below ever invents a summary
 * or a score, and "no papers" is never printed as one sentence -- a day nobody
 * swept, a day that swept eighty papers and matched none of yours, and an
 * account with no directions at all are three different problems with three
 * different fixes, and each one names its own.
 *
 * What is deliberately NOT the web's:
 *
 *   - 导入文库 saves a real Zotero `preprint` with the PDF and a child note
 *     through Zotero.Pharos.Daily.saveToLibrary(). It does not call the
 *     backend's /import endpoint, which files a row in the WEB library.
 *   - 已在文库 is answered by a local archiveID search, not by
 *     `imported_paper_id`, which names a row in that same web library.
 *   - Links open in the system browser through Zotero.launchURL.
 *   - The card keeps the desktop's author line and its four-line summary clamp.
 *   - 数据目录 uses FilePicker and a path in a pref. The web's directory handle,
 *     its IndexedDB handle store, its permission re-grant dance and its JSON
 *     fallback are all browser scaffolding and none of it is ported; see
 *     Zotero.Pharos.Daily.Vault.
 */

var { FilePicker } = ChromeUtils.importESModule('chrome://zotero/content/modules/filePicker.mjs');

var Zotero_Pharos_Daily = new function () {
	let _resolveInit;
	let _pollTimer = null;
	let _polls = 0;

	/**
	 * Poll cadence while a sweep is running.
	 *
	 * Matches the web client. The old 5s made the end of a sweep feel laggy: the
	 * work finishes, and the window sits on stale counters for up to five
	 * seconds before noticing.
	 */
	const POLL_MS = 1500;

	/**
	 * A stuck sweep must not poll forever. 400 ticks at 1.5s is ten minutes, the
	 * same ceiling the old 5s/120 loop had -- a run row orphaned by a backend
	 * restart would otherwise keep this window asking until it is closed.
	 */
	const MAX_POLLS = 400;

	/**
	 * How often, in poll ticks, the rail and the list are refetched during a
	 * sweep. The status call is cheap and decides when the sweep ended; the
	 * other two rebuild the whole list, so they run at a third of the cadence.
	 *
	 * Three, not two: _tick() increments _polls BEFORE testing it, so this is
	 * the divisor of the tick counter and not a count of skipped ticks. At 2 the
	 * list refetched on every second tick -- half the cadence, 50% more full
	 * rebuilds than the line above promises the next person tuning this.
	 */
	const LIST_EVERY = 3;

	/** A run error or a read error can be a whole traceback; a line is what fits. */
	const ERR_MAX = 160;

	/** The four highlight blocks, in the order the reading prompt defines them. */
	const HIGHLIGHT_KEYS = ['contribution', 'innovation', 'method', 'results'];

	/**
	 * 推荐 last: it is the weighted conclusion, so it reads as one.
	 *
	 * 相关 is computed from THIS account's keywords and 推荐 is re-weighted from
	 * the other four using it, so both move when a direction is edited. The
	 * per-row tooltips are what keep a reader from assuming all five describe
	 * the paper.
	 */
	const SCORE_KEYS = ['relevance', 'recency', 'popularity', 'quality', 'recommendation'];

	/** The preferences pane that owns the directions editor. */
	const DIRECTIONS_PANE = 'zotero-subpane-pharos-daily';

	/** Bumped on every day fetch so a slow response for a date the user has
	 *  already left cannot overwrite the one they are looking at. */
	let _loadToken = 0;

	/** The static elements, resolved once in _init(). */
	let _el = {};

	/* ------------------------------------------------------------ view state */

	/** The last SUCCESSFUL status. Kept across a failed poll: a request that did
	 *  not complete is not evidence that the operator has no model configured. */
	let _status = null;
	let _statusReady = false;

	/** The date of a sweep running right now, from the last successful status.
	 *  Cleared when a poll fails, so a server this window can no longer see does
	 *  not leave the 更新 button disabled indefinitely. */
	let _sweeping = null;

	/** True between POSTing a refresh and the status that first observes it. */
	let _refreshPending = false;
	let _refreshError = null;

	/** The account's digest switch, from Directions.getConfig(). A config that
	 *  could not be read is not a config that is off. */
	let _digestEnabled = true;
	let _configReady = false;

	let _dates = [];
	let _datesState = 'loading'; // loading | ready | error
	let _day = null;
	let _dayState = 'loading'; // loading | ready | error
	let _dayError = null;

	/** Whether that failure was a connection that never completed, as opposed to
	 *  something the server answered. Only the former is worth telling someone
	 *  to go and start the service. */
	let _dayUnreachable = false;
	let _papers = [];

	/** The date the user is on. Survives a refresh -- see load() and _tick(). */
	let _activeDate = null;
	let _sort = 'score';
	let _domain = null;
	let _selectedID = null;

	/**
	 * The in-flight and failed attempts, keyed by paper id. Never module-wide.
	 *
	 * A read is allowed 180s (Daily.READ_TIMEOUT), which is a long time to ask
	 * someone to sit on one card and not click anything. Held in one flag and
	 * one message the way these were, selecting another paper mid-request left
	 * the reader looking at B's card labelled 解读中… and disabled with nothing
	 * happening to B -- and when A's request finally failed, at A's error
	 * printed under B as if it were B's own. A specific, plausible, false
	 * sentence is the worst thing this panel can put on screen.
	 */
	let _reading = new Set();
	let _readErrors = new Map();
	let _importing = new Set();
	let _importErrors = new Map();

	/**
	 * paper id -> { token, item }: whether the paper is already in MY library,
	 * and what the library looked like when that was answered.
	 *
	 * Populated lazily by findInLibrary(), which never throws, so a failed
	 * lookup reads as "not in the library" rather than taking the panel down.
	 *
	 * The token is what keeps the answer honest. findInLibrary() deliberately
	 * does not match a trashed item -- someone who binned it should be offered
	 * the import again -- but this document is loaded once and never reloaded,
	 * so a memo with no invalidation goes on saying 已在文库 for the rest of the
	 * application session, with the button disabled and no way back short of a
	 * restart. _libraryToken is bumped by the item observer registered in
	 * _init(); the previous answer is KEPT while the re-check runs, so an
	 * unrelated item edit does not flash the button back to 导入文库 and out
	 * again.
	 */
	let _inLibrary = new Map();
	let _libraryToken = 0;
	let _notifierID = null;

	/** The item events that can change findInLibrary()'s answer: an import made
	 *  anywhere else, an archiveID edited or an item restored from the trash,
	 *  and the two ways an item leaves. */
	const LIBRARY_EVENTS = ['add', 'modify', 'trash', 'delete'];

	/** What _renderDetail() last drew, and for which paper. See _detailKey(). */
	let _detailDrawn = null;
	let _detailDrawnID = null;

	/** What _renderRail() last drew. Same reason as _detailDrawn: rebuilding an
	 *  unchanged subtree destroys focus and selection inside it. */
	let _railDrawn = null;

	/** Set by destroy(). Everything below that resumes after an await checks it:
	 *  the awaits outlive the document, and the timer they re-arm outlives the
	 *  window. */
	let _destroyed = false;

	/**
	 * Whether the account is signed out, as of the last render.
	 *
	 * Derived, never latched. This document is loaded once and thereafter only
	 * hidden and re-shown by the rail, so a boolean set to true at startup would
	 * survive a subsequent sign-in for the life of the application -- the module
	 * would sit on its signed-out panel with the refresh button disabled and no
	 * way back short of restarting. `Zotero.Pharos.API` clears the token on a 401
	 * (api.js `setToken(null)` before it throws SignedOutError), so
	 * `hasCredentials()` already answers this question correctly at any moment,
	 * including mid-session sign-out. `_refreshAuth` re-reads it.
	 */
	let _signedOut = false;

	/** Whether the initial load sequence ever completed. False after a startup
	 *  that stopped at the credentials check, which is what onShown() resumes. */
	let _loaded = false;

	/* ------------------------------------------------------- 数据目录 state */

	/** The connected directory's absolute path, or null. Read from the pref once
	 *  in _initVault() -- the pref is this window's own and nothing else writes
	 *  it, so there is nothing to re-check the way _signedOut is re-checked. */
	let _vaultPath = null;

	/** The manifest currently in that directory, as this window last saw it.
	 *  Passed back into Vault.write() so a snapshot inherits the directory's own
	 *  vault_id and created_at instead of minting a new identity each save. */
	let _vaultManifest = null;

	/**
	 * off | ok | attention.
	 *
	 * Auto-saving happens on 'ok' and only on 'ok'. 'attention' is every reason
	 * to stop writing without disconnecting: the path is gone, the manifest
	 * there is not the one we wrote, there is no manifest at all, or it would
	 * not parse. Each of those is a question only the user can answer, and
	 * writing anyway answers it for them by overwriting.
	 */
	let _vaultState = 'off';

	/** Which 'attention' state, as the Fluent id and arguments that explain it. */
	let _vaultWarn = null;
	let _vaultWarnArgs = null;

	/** The one-line status under the path, and the last failure, if any. */
	let _vaultMessage = '';
	let _vaultError = null;

	/** A user-initiated operation is running: the sheet's buttons are disabled
	 *  and the toolbar button says so. Distinct from _vaultSaving, which also
	 *  covers the silent save after a sweep and is purely a reentrancy guard. */
	let _vaultBusy = false;
	let _vaultSaving = false;

	/** Whether the sheet is open. */
	let _vaultOpen = false;

	/** What _renderVault() last drew. See _vaultKey(). */
	let _vaultDrawn = null;

	/**
	 * Settles once init() has finished, however it finished.
	 *
	 * Created here, when the script loads, rather than assigned from the onload
	 * handler: a caller that opens this window gets control back before onload
	 * has run, so an `initialized` that only exists afterwards is `undefined`
	 * exactly when someone needs to wait on it -- and `await undefined` succeeds
	 * silently, which made a test pass alone and fail on a busy machine.
	 */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});

	/* ---------------------------------------------------------------- strings */

	/**
	 * A Fluent string with no arguments.
	 *
	 * Wrapped rather than called bare because Zotero.getString() THROWS in en-US
	 * for an id it cannot resolve, and one missing id inside a render would take
	 * the whole window down to a blank pane with nothing on screen to say why.
	 * The id itself is at least a legible placeholder, and the throw is logged.
	 */
	function _str(id) {
		try {
			return Zotero.getString(id);
		}
		catch (e) {
			Zotero.logError(e);
			return id;
		}
	}

	/**
	 * A Fluent string with arguments.
	 *
	 * NOT Zotero.getString(id, params): handed a params argument that routes to
	 * the .properties bundle, where no pharos-* id exists. It throws in en-US
	 * and silently returns the id in zh-CN. Fluent's own formatter is what reads
	 * pharos.ftl.
	 */
	function _fmt(id, args) {
		let value = Zotero.ftl.formatValueSync(id, args);
		// An id Fluent cannot resolve comes back null rather than throwing, and
		// an empty string is a legitimate value, so this cannot be a truthiness
		// test.
		return value === null || value === undefined ? id : value;
	}

	/**
	 * Whether a failure was a connection that never completed, as opposed to
	 * something the server answered.
	 *
	 * Asked of the exception rather than of the sentence _errorText() produced
	 * for it. The string comparison this replaces made control flow depend on
	 * the value of a LOCALIZED display string -- so a translator rewording one
	 * message silently turned "the service is not running" into "the service
	 * answered with an error", with nothing anywhere to say so.
	 */
	function _isUnreachable(e) {
		return e instanceof Zotero.HTTP.TimeoutException || !e || !e.message || e.status === 0;
	}

	/**
	 * What to show the user for a failed request.
	 *
	 * The server's own `detail` wherever there is one -- Zotero.Pharos.API
	 * unwraps it -- because rewriting it would mean guessing which failure
	 * occurred. A connection that never completed is the exception: its message
	 * names the URL and "status code 0", which is not something anyone can act
	 * on.
	 *
	 * Through _str(), not Zotero.getString(): this runs inside catch blocks, and
	 * getString() throws in en-US for an id it cannot resolve. A throw here
	 * escapes the handler that called it, so load() would leave _dayState at
	 * 'loading' and the module would sit on 载入中… forever -- the precise
	 * failure _str() exists to prevent, reached through the one path that is
	 * already coping with an error.
	 */
	function _errorText(e) {
		if (_isUnreachable(e)) {
			return _str('pharos-error-unreachable');
		}
		return e.message;
	}

	function _clip(text) {
		let s = String(text || '');
		return s.length > ERR_MAX ? s.slice(0, ERR_MAX) + '…' : s;
	}

	/* ------------------------------------------------------------ DOM helpers */

	// document.createElement() produces an HTML element in this document even
	// though the default namespace is XUL. Nothing in this module is XUL: a XUL
	// button renders its `label` attribute and ignores textContent, which is a
	// blank button for every string in the table.
	function _make(tag, className, text) {
		let el = document.createElement(tag);
		if (className) {
			el.className = className;
		}
		if (text !== null && text !== undefined) {
			el.textContent = text;
		}
		return el;
	}

	function _div(className, text) {
		return _make('div', className, text);
	}

	function _span(className, text) {
		return _make('span', className, text);
	}

	function _btn(className, text) {
		return _make('button', className, text);
	}

	/* --------------------------------------------------------------- derived */

	/** One decimal, matching the scale the reading prompt scores on. */
	function _fmtScore(v) {
		return typeof v == 'number' ? v.toFixed(1) : _str('pharos-daily-none');
	}

	/**
	 * Visual weight for a recommendation score.
	 *
	 * The rubric is emphatic that scores must discriminate, and that only pays
	 * off if the difference is visible at a glance: a 9 gets a solid accent
	 * chip, a 7 a soft one, a 5 stays quiet, and an unread paper gets a dashed
	 * outline rather than anything that could be mistaken for a low score.
	 */
	function _scoreTier(v) {
		if (typeof v != 'number') {
			return 'is-none';
		}
		if (v >= 8.5) {
			return 'is-high';
		}
		if (v >= 7) {
			return 'is-mid';
		}
		return 'is-low';
	}

	/** Sort keys are nullable -- an unread paper has no score -- and a missing
	 *  one sinks to the bottom in either mode rather than masquerading as zero. */
	function _compare(a, b, sort) {
		if (sort == 'score') {
			let x = typeof a.score_recommendation == 'number' ? a.score_recommendation : null;
			let y = typeof b.score_recommendation == 'number' ? b.score_recommendation : null;
			if (x === null || y === null) {
				if (x !== y) {
					return x === null ? 1 : -1;
				}
			}
			else if (x !== y) {
				return y - x;
			}
		}
		// Newest first, both as the 时间 mode and as the 推荐分 tie-break.
		let at = a.published_at || '';
		let bt = b.published_at || '';
		if (at !== bt) {
			return at < bt ? 1 : -1;
		}
		return (a.arxiv_id || '') < (b.arxiv_id || '') ? 1 : -1;
	}

	function _directions() {
		return _status ? _status.directions : [];
	}

	/**
	 * The filter chips: the caller's own directions that actually matched today.
	 *
	 * Ordered by the user's declared priority rather than by the order papers
	 * happen to arrive in, so the chip row matches the list in settings. Still
	 * intersected with what is present, because a chip that filters to zero
	 * papers is a dead control.
	 */
	function _domains() {
		let present = [];
		for (let paper of _papers) {
			if (paper.matched_domain && !present.includes(paper.matched_domain)) {
				present.push(paper.matched_domain);
			}
		}
		let ranked = _directions().filter(d => present.includes(d));
		// Matched but no longer listed -- a direction renamed or disabled since
		// this day was rendered. Shown rather than dropped: the papers under it
		// are on screen and need a chip that reaches them.
		for (let d of present) {
			if (!ranked.includes(d)) {
				ranked.push(d);
			}
		}
		return ranked;
	}

	/** Derived, never stored: editing directions can retire the chip the user
	 *  last clicked, and a stale selection would silently filter the whole day
	 *  away. Falling back to 全部 makes "a chip is on but matches nothing"
	 *  unrepresentable. */
	function _activeDomain() {
		return _domain && _domains().includes(_domain) ? _domain : null;
	}

	function _visible() {
		let active = _activeDomain();
		let list = active === null ? _papers : _papers.filter(p => p.matched_domain == active);
		return list.slice().sort((a, b) => _compare(a, b, _sort));
	}

	/** Selected from the whole day, not from the filtered list: narrowing the
	 *  filter must not blank the detail panel out from under the reader. */
	function _selected() {
		return _papers.find(p => p.id == _selectedID) || null;
	}

	/** The install has swept at least once, which is what makes an empty rail
	 *  the filter's doing rather than a digest that has never run. */
	function _everSwept() {
		return _statusReady && _status.last_run !== null;
	}

	function _railEmpty() {
		return _datesState == 'ready' && !_dates.length;
	}

	function _noDirections() {
		return _statusReady && !_status.directions.length;
	}

	function _digestOff() {
		return _configReady && !_digestEnabled;
	}

	function _bannerShown() {
		return _statusReady && !_status.llm_configured;
	}

	/** A sweep is in flight -- the only condition under which anything polls,
	 *  and the only thing the 更新 button is disabled on. Derived from the
	 *  server's own state, so a sweep started in the web client or on another
	 *  machine disables it here too. */
	function _busy() {
		return _refreshPending || _sweeping !== null;
	}

	function _providerText() {
		let provider = _status && _status.provider;
		if (!provider || !provider.configured || !provider.name) {
			return _str('pharos-daily-provider-none');
		}
		return _fmt('pharos-daily-provider', { name: provider.name, model: provider.model });
	}

	/**
	 * The live sweep counters.
	 *
	 * From the caller's OWN counts, never from last_run.fetched/read_done: those
	 * columns are written once, at _finish_run, and read zero for the entire
	 * time anyone would want to watch them.
	 */
	function _progressText() {
		let summary = null;
		let today = _status && _status.today;
		if (today && (!today.date || today.date == _sweeping)) {
			summary = today;
		}
		else {
			summary = _dates.find(d => d.date == _sweeping) || null;
		}
		let total = summary && summary.total || 0;
		let read = summary && summary.read || 0;
		let failed = summary && summary.failed || 0;
		let text = _fmt('pharos-daily-sweep-progress', { total, read });
		if (failed > 0) {
			// Self-contained fragment; the leading separator is part of its value.
			text += _fmt('pharos-daily-sweep-failed', { failed });
		}
		return text;
	}

	/** Kept from the desktop's own row: arXiv author lists run to dozens of
	 *  names, and the first four carry the attribution without the list becoming
	 *  the tallest thing on screen. */
	function _authorText(authors) {
		if (!authors || !authors.length) {
			return '';
		}
		return authors.slice(0, 4).join(', ') + (authors.length > 4 ? ' et al.' : '');
	}

	/* ------------------------------------------------------------- lifecycle */

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		_el = {
			rail: document.getElementById('pharos-dv-dates'),
			barDate: document.getElementById('pharos-dv-bar-date'),
			filters: document.getElementById('pharos-dv-filters'),
			sortScore: document.getElementById('pharos-dv-sort-score'),
			sortTime: document.getElementById('pharos-dv-sort-time'),
			count: document.getElementById('pharos-dv-count'),
			model: document.getElementById('pharos-dv-model'),
			refresh: document.getElementById('pharos-daily-refresh'),
			refreshIcon: document.getElementById('pharos-dv-refresh-ic'),
			refreshLabel: document.getElementById('pharos-dv-refresh-label'),
			note: document.getElementById('pharos-daily-status'),
			banner: document.getElementById('pharos-dv-banner'),
			bannerText: document.getElementById('pharos-dv-banner-text'),
			list: document.getElementById('pharos-dv-list'),
			detail: document.getElementById('pharos-dv-detail'),
			vault: document.getElementById('pharos-dv-vault'),
			vaultLabel: document.getElementById('pharos-dv-vault-label'),
			vaultDot: document.getElementById('pharos-dv-vault-dot'),
			vaultLayer: document.getElementById('pharos-dv-vault-layer'),
			vaultSheet: document.getElementById('pharos-dv-vault-sheet'),
			vaultClose: document.getElementById('pharos-dv-vault-close'),
			vaultPath: document.getElementById('pharos-dv-vault-path'),
			vaultMsg: document.getElementById('pharos-dv-vault-msg'),
			vaultWarn: document.getElementById('pharos-dv-vault-warn'),
			vaultError: document.getElementById('pharos-dv-vault-error'),
			vaultActions: document.getElementById('pharos-dv-vault-actions'),
			vaultDisconnect: document.getElementById('pharos-dv-vault-disconnect'),
		};

		_el.sortScore.addEventListener('click', () => this.setSort('score'));
		_el.sortTime.addEventListener('click', () => this.setSort('time'));
		// `click` only. This is an html:button, which has no `command` event;
		// the XUL button it replaced needed both and rendered a blank label.
		_el.refresh.addEventListener('click', () => this.refresh());

		_el.vault.addEventListener('click', () => this.openVault());
		_el.vaultClose.addEventListener('click', () => this.closeVault());
		_el.vaultDisconnect.addEventListener('click', () => this.disconnectVault());
		// Dismissed by clicking the backdrop, never by clicking the sheet: the
		// sheet carries three paragraphs of prose, and a stray click while
		// selecting one of them must not close the thing being read.
		_el.vaultLayer.addEventListener('mousedown', (event) => {
			if (event.target === _el.vaultLayer) {
				this.closeVault();
			}
		});
		_el.vaultSheet.addEventListener('keydown', (event) => {
			if (event.key == 'Escape') {
				this.closeVault();
			}
		});

		// The one thing this module caches about the local library is whether a
		// paper is already in it, and the library is edited from everywhere else
		// in the application. Registered before the credentials check so a
		// module that starts signed out and is woken by onShown() still has it.
		_notifierID = Zotero.Notifier.registerObserver({
			notify: (event, type) => {
				if (type != 'item' || !LIBRARY_EVENTS.includes(event)) {
					return;
				}
				// Not a clear(): dropping the answers would flash every 已在文库
				// button back to 导入文库 until the re-check landed, including
				// the 'add' this module's own import fires. The token is what
				// makes the kept answer provisional.
				_libraryToken++;
				this._renderDetail();
			}
		}, ['item'], 'pharosDaily');

		// Before the credentials check, and awaited: the connection lives on the
		// local disk, so it is answerable while signed out, and every render
		// below draws the toolbar button from it. Left to run in the background
		// the button would show 未连接 for the first frames of a session that
		// has a directory, which is the one thing it must never say wrongly.
		await this._initVault();

		if (!Zotero.Pharos.API.hasCredentials()) {
			_signedOut = true;
			this._render();
			return;
		}

		this._render();
		await this._loadAll();
	};

	this.destroy = function () {
		// Otherwise a sweep observed here keeps polling after the window is gone.
		//
		// The flag is the part that actually covers that. _tick() nulls
		// _pollTimer on its first line and then awaits three requests, so for
		// most of its life there is no timer to clear -- and the continuation
		// would re-arm one through _syncPoll() and render into a torn-down
		// document, both of which fail silently because nothing is left to
		// notice.
		_destroyed = true;
		if (_pollTimer) {
			clearTimeout(_pollTimer);
			_pollTimer = null;
		}
		if (_notifierID) {
			Zotero.Notifier.unregisterObserver(_notifierID);
			_notifierID = null;
		}
	};

	/* ----------------------------------------------------------------- loads */

	this.loadConfig = async function () {
		try {
			let config = await Zotero.Pharos.Directions.getConfig();
			_digestEnabled = !config || config.enabled !== false;
			_configReady = true;
		}
		catch (e) {
			// A config that could not be read is not a config that is off, and
			// this is the only state that can hide the whole module.
			Zotero.logError(e);
			this._noteSignedOut(e);
			_digestEnabled = true;
		}
	};

	/**
	 * @return {Promise<Boolean>} whether the status is now current
	 */
	this.loadStatus = async function () {
		try {
			_status = await Zotero.Pharos.Daily.getStatus();
			_statusReady = true;
			_sweeping = _status.sweeping;
			return true;
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			// The status itself is KEPT -- llm_configured must not flip to false
			// on the strength of a request that never completed. What is dropped
			// is the sweep, which this window can no longer see and must not go
			// on claiming, because that would leave 更新 disabled for good.
			_sweeping = null;
			return false;
		}
	};

	this.loadDates = async function () {
		try {
			_dates = await Zotero.Pharos.Daily.getDates();
			_datesState = 'ready';
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			_dates = [];
			_datesState = 'error';
		}
	};

	/**
	 * @param {String} date - YYYY-MM-DD
	 * @param {Object} [options]
	 * @param {Boolean} [options.quiet] - skip the loading state, for a reload
	 *     during a sweep that would otherwise flash "载入中…" every few seconds
	 */
	this.load = async function (date, { quiet } = {}) {
		_activeDate = date || Zotero.Pharos.Daily.today();
		let token = ++_loadToken;
		if (!quiet) {
			_dayState = 'loading';
			_dayError = null;
			_dayUnreachable = false;
			this._render();
		}
		try {
			let day = await Zotero.Pharos.Daily.getDay(_activeDate);
			if (token !== _loadToken) {
				return;
			}
			_day = day;
			_papers = day && Array.isArray(day.papers) ? day.papers : [];
			_dayState = 'ready';
			_dayError = null;
			_dayUnreachable = false;
		}
		catch (e) {
			if (token !== _loadToken) {
				return;
			}
			Zotero.logError(e);
			this._noteSignedOut(e);
			_day = null;
			_papers = [];
			_dayState = 'error';
			_dayError = _errorText(e);
			_dayUnreachable = _isUnreachable(e);
		}
		// A reload can retire the selected paper; the detail panel must not go
		// on showing a row that is no longer in the day.
		if (_selectedID && !_papers.some(p => p.id == _selectedID)) {
			_selectedID = null;
		}
		this._render();
	};

	/** Signing out mid-session turns every panel into the same message rather
	 *  than four different ways of saying the request failed. */
	this._noteSignedOut = function (e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			_signedOut = true;
		}
	};

	/* --------------------------------------------------------------- actions */

	this.setSort = function (sort) {
		if (_sort == sort) {
			return;
		}
		_sort = sort;
		this._renderToolbar();
		this._renderList();
	};

	this.setDomain = function (domain) {
		// Clicking the chip that is already on turns it off, back to 全部.
		_domain = _domain == domain ? null : (domain || null);
		this._renderToolbar();
		this._renderList();
	};

	this.selectDate = function (date) {
		if (date == _activeDate) {
			return;
		}
		_refreshError = null;
		this.load(date);
	};

	this.selectPaper = function (id) {
		if (_selectedID == id) {
			return;
		}
		_selectedID = id;
		// Nothing is cleared here any more: an attempt belongs to the paper it
		// was made on, and _reading/_readErrors/_importing/_importErrors are
		// keyed by paper id, so leaving this one takes its state with it and
		// coming back finds it again.
		//
		// Classes only, so the list does not rebuild and lose its scroll offset.
		for (let card of _el.list.querySelectorAll('.pharos-dv-card')) {
			let on = card.dataset.paperId == id;
			card.classList.toggle('is-selected', on);
			card.setAttribute('aria-pressed', on ? 'true' : 'false');
		}
		this._renderDetail();
	};

	this.openDirections = function () {
		Zotero.Utilities.Internal.openPreferences(DIRECTIONS_PANE);
	};

	this.refresh = async function () {
		if (_signedOut || _busy()) {
			return;
		}
		_refreshError = null;
		_refreshPending = true;
		this._render();
		try {
			let run = await Zotero.Pharos.Daily.refresh();
			// The run row is already persisted, so the sweep can be shown as
			// live straight away instead of after a poll interval. Its counters
			// cannot be: they stay zero until the sweep finishes.
			_sweeping = run && run.date ? run.date : Zotero.Pharos.Daily.today();
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			if (e.status === 409) {
				// A sweep IS running -- started in the web client, on another
				// machine, or by the scheduler. The server's own prose is
				// English and names a date; this is the sentence for it.
				_refreshError = _str('pharos-daily-refresh-busy');
				// Pick that sweep up rather than ignoring it: its end is what
				// this window is waiting for either way.
				await this.loadStatus();
			}
			else {
				_refreshError = _fmt('pharos-daily-refresh-failed',
					{ error: _clip(_errorText(e)) });
			}
		}
		finally {
			_refreshPending = false;
		}
		this._syncPoll();
		this._render();
	};

	/**
	 * Have the model read one paper now.
	 *
	 * Three failures that have to stay apart: a 200 whose read_status is "error"
	 * is the provider failing and the row being written, and it RESOLVES; a 503
	 * means nothing was attempted and the fix is configuration; anything else is
	 * this request.
	 */
	this.read = async function (paper) {
		// Per paper, so a double click on one card is still refused while a read
		// running on another does not lock the button the reader is looking at.
		if (_reading.has(paper.id)) {
			return;
		}
		_reading.add(paper.id);
		_readErrors.delete(paper.id);
		this._renderDetail();
		try {
			let updated = await Zotero.Pharos.Daily.readPaper(paper.id);
			if (updated && updated.id) {
				// Replaced wholesale rather than merged: what comes back is
				// scored against THIS caller's directions, so mixing it with the
				// old row would mix two rubrics.
				let index = _papers.findIndex(p => p.id == updated.id);
				if (index >= 0) {
					_papers[index] = updated;
				}
			}
			// The rail's pending badge for this date just changed.
			await this.loadDates();
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			if (e.status === 503) {
				// Nothing was attempted and nothing was written: the fix is
				// configuration, not another click.
				_readErrors.set(paper.id, _str('pharos-daily-read-unavailable'));
			}
			else {
				_readErrors.set(paper.id, _errorText(e));
			}
		}
		finally {
			_reading.delete(paper.id);
		}
		this._render();
	};

	/**
	 * Put the paper into the LOCAL Zotero library.
	 *
	 * Not POST /api/daily/papers/{id}/import, which files it in the web client's
	 * library. The word on the button changed to match the web; the behaviour
	 * did not, and the desktop's version is the better one -- a real preprint
	 * with authors, archiveID, the PDF and the model's reading as a child note.
	 */
	this.importToLibrary = async function (paper) {
		// Keyed like read(): saving downloads the PDF, so this is not instant
		// either, and a second paper must not wear the first one's spinner.
		if (_importing.has(paper.id)) {
			return;
		}
		_importing.add(paper.id);
		_importErrors.delete(paper.id);
		this._renderDetail();
		try {
			let item = await Zotero.Pharos.Daily.saveToLibrary(paper);
			_inLibrary.set(paper.id, { token: _libraryToken, item });
		}
		catch (e) {
			Zotero.logError(e);
			_importErrors.set(paper.id,
				_fmt('pharos-daily-import-failed', { error: _clip(_errorText(e)) }));
		}
		finally {
			_importing.delete(paper.id);
		}
		this._renderDetail();
	};

	/* -------------------------------------------------------------- 数据目录 */

	/** Papers a manifest accounts for, without opening a single day file. */
	function _vaultPapers(manifest) {
		return Zotero.Pharos.Daily.Vault.paperCount(manifest);
	}

	function _vaultDays(manifest) {
		return manifest && Array.isArray(manifest.days) ? manifest.days.length : 0;
	}

	/** Stop auto-saving and say which of the four reasons it is. */
	function _vaultAttention(id, args) {
		_vaultState = 'attention';
		_vaultWarn = id;
		_vaultWarnArgs = args || null;
	}

	function _vaultOK(manifest) {
		_vaultManifest = manifest;
		_vaultState = 'ok';
		_vaultWarn = null;
		_vaultWarnArgs = null;
	}

	/**
	 * Adopt the remembered connection, if there is one.
	 *
	 * Nothing is written here, ever. Opening the module must not modify a
	 * directory -- the user has not asked for anything yet, and the first thing
	 * a startup save would do on a machine restored from a backup is overwrite
	 * the good copy with an empty account.
	 */
	this._initVault = async function () {
		let Vault = Zotero.Pharos.Daily.Vault;
		let connection = Vault.getConnection();
		if (!connection) {
			_vaultState = 'off';
			_vaultMessage = _str('pharos-daily-vault-idle');
			return;
		}
		_vaultPath = connection.path;
		await this._probeVault(connection.vaultID);
	};

	/**
	 * Decide, from what is on disk right now, whether the directory is ours to
	 * write to.
	 *
	 * @param {String|null} rememberedID - the vault_id we last wrote there
	 */
	this._probeVault = async function (rememberedID) {
		let Vault = Zotero.Pharos.Daily.Vault;
		_vaultManifest = null;
		try {
			if (!(await Vault.directoryExists(_vaultPath))) {
				// The commonest desktop failure by far, and one the web cannot
				// have: an external disk that is not plugged in, or a sync
				// folder the user moved. Not an error and not a disconnection --
				// the directory is very likely to come back.
				_vaultAttention('pharos-daily-vault-warn-missing', { path: _vaultPath });
				_vaultMessage = _str('pharos-daily-vault-paused');
				return;
			}
			let manifest = await Vault.readManifest(_vaultPath);
			if (manifest === null) {
				_vaultAttention('pharos-daily-vault-warn-empty', null);
				_vaultMessage = _str('pharos-daily-vault-paused');
				return;
			}
			_vaultManifest = manifest;
			if (manifest.vault_id !== rememberedID) {
				// A path can be reused, replaced, or synced over by another
				// machine. Overwriting a manifest whose id is not the one we
				// wrote destroys someone's backup to save ours.
				_vaultAttention('pharos-daily-vault-warn-changed', null);
				_vaultMessage = _str('pharos-daily-vault-paused');
				return;
			}
			_vaultOK(manifest);
			_vaultMessage = _fmt('pharos-daily-vault-connected', {
				days: _vaultDays(manifest),
				papers: _vaultPapers(manifest),
			});
		}
		catch (e) {
			// A manifest that will not parse, a file the digest does not match,
			// a permission problem. All of them mean the same thing about
			// writing: not until someone has looked.
			Zotero.logError(e);
			_vaultAttention('pharos-daily-vault-warn-broken', { error: _clip(e.message) });
			_vaultMessage = _str('pharos-daily-vault-paused');
		}
	};

	this.openVault = function () {
		_vaultOpen = true;
		this._renderVault();
		// Focus moves into the sheet so Escape reaches its keydown handler and
		// so a keyboard user is not left tabbing through the list behind it.
		_el.vaultSheet.focus();
	};

	this.closeVault = function () {
		if (!_vaultOpen) {
			return;
		}
		_vaultOpen = false;
		_vaultError = null;
		this._renderVault();
		_el.vault.focus();
	};

	/**
	 * Pick a directory and connect it.
	 *
	 * A directory that already holds a Vault is NOT written to here. Choosing a
	 * folder is how someone restores onto a new machine, and that is exactly the
	 * moment when the account is empty and the directory is not -- so the
	 * question is asked rather than answered by writing.
	 */
	this.chooseVault = async function () {
		if (_vaultBusy) {
			return;
		}
		let Vault = Zotero.Pharos.Daily.Vault;
		_vaultBusy = true;
		_vaultError = null;
		this._renderVault();
		try {
			let fp = new FilePicker();
			if (_vaultPath) {
				fp.displayDirectory = _vaultPath;
			}
			fp.init(window, _str('pharos-daily-vault-picker'), fp.modeGetFolder);
			fp.appendFilters(fp.filterAll);
			if ((await fp.show()) != fp.returnOK) {
				return;
			}
			let path = PathUtils.normalize(fp.file);
			_vaultPath = path;
			_vaultManifest = null;

			let existing = await Vault.readManifest(path);
			if (existing !== null) {
				_vaultManifest = existing;
				_vaultAttention('pharos-daily-vault-warn-existing', {
					days: _vaultDays(existing),
					papers: _vaultPapers(existing),
				});
				_vaultMessage = _str('pharos-daily-vault-paused');
				// Remembered with NO id: the directory is connected, but nothing
				// in it is ours yet, so a restart must arrive back in exactly
				// this state rather than deciding the manifest is trusted.
				Vault.remember(path, '');
				return;
			}
			let archive = await Vault.exportArchive();
			let manifest = await Vault.write(path, archive, null);
			_vaultOK(manifest);
			_vaultMessage = _fmt('pharos-daily-vault-created', {
				days: _vaultDays(manifest),
				papers: _vaultPapers(manifest),
			});
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			_vaultError = _fmt('pharos-daily-vault-failed', { error: _clip(_errorText(e)) });
		}
		finally {
			_vaultBusy = false;
		}
		this._renderVaultButton();
		this._renderVault();
	};

	/**
	 * Write the account's current digest into the connected directory.
	 *
	 * @param {Object} [options]
	 * @param {Boolean} [options.silent] - the automatic save after a sweep. It
	 *     reports through the toolbar button and the sheet, and never opens
	 *     anything or steals focus.
	 * @param {Boolean} [options.adopt] - write even though the directory is in
	 *     the attention state, taking over the manifest already there. Only ever
	 *     set from the 用当前账户覆盖 button, which confirms first.
	 */
	this.saveVault = async function ({ silent = false, adopt = false } = {}) {
		let Vault = Zotero.Pharos.Daily.Vault;
		if (!_vaultPath || _signedOut || _vaultSaving) {
			return;
		}
		if (!adopt && _vaultState != 'ok') {
			return;
		}
		_vaultSaving = true;
		if (!silent) {
			_vaultBusy = true;
			_vaultError = null;
		}
		this._renderVaultButton();
		this._renderVault();
		try {
			let archive = await Vault.exportArchive();
			// _vaultManifest, not null: the directory keeps its own vault_id and
			// created_at across every save, including the adopting one, so its
			// identity is a property of the DIRECTORY and not of whichever
			// account happened to write it last.
			let manifest = await Vault.write(_vaultPath, archive, _vaultManifest);
			if (_destroyed) {
				return;
			}
			_vaultOK(manifest);
			_vaultError = null;
			_vaultMessage = _fmt('pharos-daily-vault-saved', {
				days: _vaultDays(manifest),
				papers: _vaultPapers(manifest),
			});
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			// Reported on the silent path too. A backup that quietly stopped
			// working is worse than one that was never configured, and the
			// toolbar button is the one place a reader would notice.
			_vaultError = _fmt('pharos-daily-vault-failed', { error: _clip(_errorText(e)) });
			_vaultMessage = _str('pharos-daily-vault-unsaved');
		}
		finally {
			_vaultSaving = false;
			_vaultBusy = false;
		}
		this._renderVaultButton();
		this._renderVault();
	};

	/**
	 * Read the directory back and merge it into the account.
	 *
	 * Every file is verified before a single byte is sent -- see Vault.read().
	 * The profile is REPLACED rather than merged, which is the destructive half
	 * of this and the reason for the confirmation.
	 */
	this.restoreVault = async function () {
		let Vault = Zotero.Pharos.Daily.Vault;
		if (!_vaultPath || _vaultBusy) {
			return;
		}
		_vaultBusy = true;
		_vaultError = null;
		this._renderVault();
		try {
			let archive = await Vault.read(_vaultPath);
			if (!this.confirmVaultRestore(archive)) {
				return;
			}
			let result = await Vault.importArchive(archive, true);
			// The whole digest just changed underneath the panel.
			await this.loadConfig();
			await this.loadStatus();
			await this.loadDates();
			await this.load(_dates.length ? _dates[0].date : Zotero.Pharos.Daily.today(),
				{ quiet: true });
			// The directory is now the account's own history, so adopt its id
			// and resume automatic saving.
			let manifest = await Vault.readManifest(_vaultPath);
			if (manifest !== null) {
				Vault.remember(_vaultPath, manifest.vault_id);
				_vaultOK(manifest);
			}
			_vaultMessage = _fmt('pharos-daily-vault-restored', {
				added: result.papers_added || 0,
				updated: result.papers_updated || 0,
				unchanged: result.papers_unchanged || 0,
			});
		}
		catch (e) {
			Zotero.logError(e);
			this._noteSignedOut(e);
			_vaultError = _fmt('pharos-daily-vault-failed', { error: _clip(_errorText(e)) });
		}
		finally {
			_vaultBusy = false;
		}
		this._render();
	};

	/** Take over a directory whose contents are not ours, after saying so. */
	this.overwriteVault = async function () {
		if (!_vaultPath || _vaultBusy || !this.confirmVaultOverwrite()) {
			return;
		}
		await this.saveVault({ adopt: true });
	};

	this.disconnectVault = function () {
		Zotero.Pharos.Daily.Vault.forget();
		_vaultPath = null;
		_vaultManifest = null;
		_vaultState = 'off';
		_vaultWarn = null;
		_vaultWarnArgs = null;
		_vaultError = null;
		_vaultMessage = _str('pharos-daily-vault-disconnected');
		this._renderVaultButton();
		this._renderVault();
	};

	/**
	 * Two confirmations, both of which destroy something.
	 *
	 * Own methods rather than inline calls so a test can replace them; a modal
	 * from Services.prompt has no way to answer itself.
	 */
	this.confirmVaultRestore = function (archive) {
		let papers = archive.days.reduce(
			(total, day) => total + (Array.isArray(day.papers) ? day.papers.length : 0), 0);
		return _confirm(
			_str('pharos-daily-vault-restore-title'),
			_fmt('pharos-daily-vault-restore-body',
				{ days: archive.days.length, papers }),
			_str('pharos-daily-vault-restore-ok')
		);
	};

	this.confirmVaultOverwrite = function () {
		return _confirm(
			_str('pharos-daily-vault-overwrite-title'),
			_fmt('pharos-daily-vault-overwrite-body', {
				days: _vaultDays(_vaultManifest),
				papers: _vaultPapers(_vaultManifest),
			}),
			_str('pharos-daily-vault-overwrite-ok')
		);
	};

	function _confirm(title, body, okLabel) {
		let ps = Services.prompt;
		let flags = ps.BUTTON_POS_0 * ps.BUTTON_TITLE_IS_STRING
			+ ps.BUTTON_POS_1 * ps.BUTTON_TITLE_CANCEL;
		return ps.confirmEx(window, title, body, flags, okLabel, null, null, null, {}) === 0;
	}

	/* ---------------------------------------------------------------- polling */

	this._syncPoll = function () {
		if (_destroyed) {
			return;
		}
		if (_busy() && !_pollTimer) {
			_polls = 0;
			_pollTimer = setTimeout(() => this._tick(), POLL_MS);
		}
		else if (!_busy() && _pollTimer) {
			clearTimeout(_pollTimer);
			_pollTimer = null;
		}
	};

	this._tick = async function () {
		_pollTimer = null;
		_polls++;
		let wasSweeping = _sweeping;

		// `sweeping` is the sweeper's own in-memory state and is the only
		// authoritative "a sweep is running". last_run.status reads "running"
		// forever for a row orphaned by a backend restart, and polling on that
		// polls forever.
		let ok = await this.loadStatus();
		// The window can have gone during that request, and destroy() had no
		// timer to cancel because the first line above cleared it. Checked here
		// as well as in _render()/_syncPoll() so the two remaining requests are
		// not sent at all.
		if (_destroyed) {
			return;
		}
		if (!ok) {
			// _sweeping is already cleared, so the button comes back rather than
			// staying disabled behind a server this window cannot reach.
			this._render();
			return;
		}

		let ended = wasSweeping && !_sweeping;
		if (ended) {
			_refreshError = null;
		}
		if (ended || _polls % LIST_EVERY === 0) {
			await this.loadDates();
			// The user's own date, NOT today: someone browsing Monday's digest
			// who triggers a sweep must not be thrown forward to today.
			await this.load(_activeDate, { quiet: true });
		}
		if (_polls >= MAX_POLLS) {
			_sweeping = null;
		}

		this._syncPoll();
		this._render();

		if (ended) {
			// The one moment the digest changes wholesale, and the only place
			// this window can be sure the new day is already loaded. Deliberately
			// after _render(): the export and the write take seconds, and doing
			// them first would leave 更新 disabled and the counters stale for the
			// whole of it, which reads as a sweep that has not finished.
			//
			// No timer and no periodic full snapshot, unlike the web's. A page
			// that can be closed at any moment has to keep re-saving to be sure
			// it saved at all; this window is here until the application quits,
			// and the sweep is where new data comes from. Direction edits happen
			// in the preferences pane and are picked up by the next sweep, or by
			// 立即保存.
			await this.saveVault({ silent: true });
		}
	};

	/**
	 * The initial data load, factored out so onShown() can run it later.
	 *
	 * Sequential rather than parallel: the empty-state triage reads all three,
	 * and a screen assembled from two of them flashes the wrong diagnosis on the
	 * way to the right one.
	 */
	this._loadAll = async function () {
		await this.loadConfig();
		await this.loadStatus();
		await this.loadDates();
		// No explicit selection yet -> the newest digest, which is what someone
		// opening this window in the morning wants.
		await this.load(_dates.length ? _dates[0].date : Zotero.Pharos.Daily.today());
		_loaded = true;

		// A sweep may already have been running before this window opened.
		this._syncPoll();
		this._render();
	};

	/** Re-read the credentials. Returns true if the answer changed. */
	this._refreshAuth = function () {
		let now = !Zotero.Pharos.API.hasCredentials();
		let changed = now != _signedOut;
		_signedOut = now;
		return changed;
	};

	/**
	 * The rail is about to show this panel again.
	 *
	 * The browser holding this document gets a `src` once and is thereafter only
	 * hidden and re-shown, so nothing else would ever tell a module that started
	 * signed out that it no longer is. Exposed as `PharosView` so the rail does
	 * not need to know which module it is talking to.
	 */
	this.onShown = function () {
		let changed = this._refreshAuth();
		if (!_signedOut && !_loaded) {
			// Deliberately not awaited: the rail is mid-render and must not wait
			// on the network to finish showing the panel.
			this._loadAll().catch(e => Zotero.logError(e));
			return;
		}
		if (changed) {
			this._render();
		}
	};

	/* ---------------------------------------------------------------- render */

	this._render = function () {
		// Every await in this file settles into a document that may already be
		// gone -- load(), read() and importToLibrary() all render on the way
		// out. replaceChildren() on a torn-down node throws nothing and shows
		// nobody anything, so the guard has to be here rather than at each call.
		if (_destroyed) {
			return;
		}
		// Cheap, and it is the only thing that keeps a mid-session sign-out or
		// sign-in from leaving every panel describing a state that ended.
		this._refreshAuth();
		this._renderToolbar();
		this._renderVaultButton();
		this._renderVault();
		this._renderNote();
		this._renderBanner();
		this._renderRail();
		this._renderList();
		this._renderDetail();
	};

	/**
	 * The toolbar's 数据目录 button.
	 *
	 * Three things at once, in one 25px control: whether a directory is
	 * connected (the dot), whether it needs a decision (the warning tint and a
	 * different word), and whether a save is running right now. The tooltip
	 * carries the same line the sheet shows, so the state is readable without
	 * opening anything.
	 */
	this._renderVaultButton = function () {
		// Every vault operation awaits the network and the disk, and both of
		// those settle into a document that may already be gone.
		if (_destroyed || !_el.vault) {
			return;
		}
		let attention = _vaultState == 'attention';
		let label;
		if (_vaultSaving) {
			label = 'pharos-daily-vault-saving';
		}
		else if (attention) {
			label = 'pharos-daily-vault-attention';
		}
		else {
			label = 'pharos-daily-vault';
		}
		_el.vaultLabel.textContent = _str(label);
		_el.vault.classList.toggle('is-warn', attention);
		_el.vault.classList.toggle('is-on', _vaultState == 'ok');
		// Only for a directory that is actually being written to. A dot on the
		// attention state would say "connected and fine" about the one state
		// that is neither.
		_el.vaultDot.hidden = _vaultState != 'ok';
		_el.vault.title = _vaultMessage || _str('pharos-daily-vault-idle');
	};

	/**
	 * Everything _renderVault() draws, as one comparable value.
	 *
	 * Same reason as _railKey() and _detailKey(): _render() runs every 1.5s
	 * while a sweep is live, and rebuilding the action tiles moves focus off
	 * whichever one had it. An input read below and missing here is a sheet that
	 * silently stops updating for it.
	 */
	function _vaultKey() {
		return JSON.stringify([
			_vaultOpen,
			_vaultPath,
			_vaultState,
			_vaultWarn,
			_vaultWarnArgs,
			_vaultMessage,
			_vaultError,
			_vaultBusy,
			_signedOut,
			_vaultManifest && _vaultManifest.vault_id,
			_vaultDays(_vaultManifest),
		]);
	}

	this._renderVault = function () {
		if (_destroyed || !_el.vaultLayer) {
			return;
		}
		let key = _vaultKey();
		if (key === _vaultDrawn) {
			return;
		}
		_vaultDrawn = key;

		_el.vaultLayer.hidden = !_vaultOpen;
		if (!_vaultOpen) {
			return;
		}

		_el.vaultPath.textContent = _vaultPath || _str('pharos-daily-vault-none');
		_el.vaultMsg.textContent = _vaultMessage;

		_el.vaultWarn.replaceChildren();
		_el.vaultWarn.hidden = _vaultWarn === null;
		if (_vaultWarn !== null) {
			_el.vaultWarn.append(_div('pharos-dv-vault-warn-h',
				_str('pharos-daily-vault-warn-head')));
			_el.vaultWarn.append(_div('pharos-dv-vault-warn-p',
				_vaultWarnArgs ? _fmt(_vaultWarn, _vaultWarnArgs) : _str(_vaultWarn)));
		}

		_el.vaultError.textContent = _vaultError || '';
		_el.vaultError.hidden = !_vaultError;

		_el.vaultActions.replaceChildren();
		for (let spec of this._vaultActions()) {
			_el.vaultActions.append(this._vaultAction(spec));
		}

		_el.vaultDisconnect.hidden = !_vaultPath;
		_el.vaultDisconnect.disabled = _vaultBusy;
	};

	/**
	 * The action tiles, in the order the situation makes them useful.
	 *
	 * 恢复 leads whenever the directory holds a snapshot this account has not
	 * adopted, because that is what a new machine is for. 覆盖 is offered beside
	 * it and never alone -- an "overwrite" with no "restore" next to it is a
	 * trap on exactly the machine where the account is empty.
	 */
	this._vaultActions = function () {
		let connected = !!_vaultPath;
		let attention = _vaultState == 'attention';
		let hasManifest = _vaultManifest !== null;
		let actions = [];

		if (attention && hasManifest) {
			actions.push({
				label: 'pharos-daily-vault-restore',
				hint: 'pharos-daily-vault-restore-hint',
				action: () => this.restoreVault(),
				primary: true,
			});
			actions.push({
				label: 'pharos-daily-vault-overwrite',
				hint: 'pharos-daily-vault-overwrite-hint',
				action: () => this.overwriteVault(),
				danger: true,
			});
		}

		actions.push({
			label: connected ? 'pharos-daily-vault-change' : 'pharos-daily-vault-choose',
			hint: 'pharos-daily-vault-choose-hint',
			action: () => this.chooseVault(),
			primary: !connected,
		});

		actions.push({
			label: 'pharos-daily-vault-save-now',
			hint: 'pharos-daily-vault-save-now-hint',
			action: () => this.saveVault(),
			// Signed out there is nothing to export; in the attention state the
			// two buttons above are the decision this one would pre-empt.
			disabled: !connected || _signedOut || attention,
		});

		if (connected && !attention && hasManifest) {
			// Disaster recovery on a machine whose directory is already trusted:
			// the account was reset, the server moved, someone deleted a month.
			actions.push({
				label: 'pharos-daily-vault-restore',
				hint: 'pharos-daily-vault-restore-hint',
				action: () => this.restoreVault(),
				disabled: _signedOut,
			});
		}
		return actions;
	};

	this._vaultAction = function (spec) {
		let className = 'pharos-dv-vault-act'
			+ (spec.primary ? ' is-primary' : '')
			+ (spec.danger ? ' is-danger' : '');
		let button = _btn(className);
		button.disabled = _vaultBusy || !!spec.disabled;
		button.append(_div('pharos-dv-vault-act-k', _str(spec.label)));
		button.append(_div('pharos-dv-vault-act-v', _str(spec.hint)));
		button.addEventListener('click', spec.action);
		return button;
	};

	this._renderToolbar = function () {
		_el.barDate.textContent = _activeDate || _str('pharos-daily-heading');

		let domains = _domains();
		let active = _activeDomain();
		_el.filters.replaceChildren();
		if (domains.length) {
			_el.filters.append(this._chip('', _str('pharos-daily-filter-all'), active === null));
			for (let domain of domains) {
				_el.filters.append(this._chip(domain, domain, active === domain));
			}
		}
		_el.filters.hidden = !domains.length;

		_el.sortScore.classList.toggle('is-on', _sort == 'score');
		_el.sortTime.classList.toggle('is-on', _sort == 'time');

		// Suppressed on a day with nothing in it: the empty-state panel below
		// already explains why, and "0 篇" over it reads as a second, contentless
		// answer to the same question.
		let visible = _visible();
		_el.count.textContent = _dayState == 'ready' && !_signedOut && _papers.length
			? _fmt('pharos-daily-count', { count: visible.length })
			: '';
		// The day's own total when a direction chip is narrowing the list, so a
		// filtered count cannot be read as the day being thin.
		_el.count.title = visible.length != _papers.length
			? _fmt('pharos-daily-count-all', { count: _papers.length })
			: '';

		let model = _status && _status.provider && _status.provider.model;
		_el.model.textContent = _statusReady && !_signedOut && model ? model : '';
		_el.model.title = _el.model.textContent ? _providerText() : '';

		let busy = _busy();
		_el.refresh.disabled = _signedOut || busy;
		_el.refreshLabel.textContent = busy
			? _str('pharos-daily-refreshing')
			: _str('pharos-daily-refresh');
		_el.refreshIcon.classList.toggle('is-spin', busy);
		// The provider rides on this tooltip whenever the banner is not on
		// screen to carry it, so which model produced a reading is always one
		// hover away.
		_el.refresh.title = _bannerShown()
			? _str('pharos-daily-refresh-tooltip')
			: _str('pharos-daily-refresh-tooltip') + '\n' + _providerText();
	};

	this._chip = function (domain, label, on) {
		let chip = _btn('pharos-dv-fchip' + (on ? ' is-on' : ''), label);
		chip.dataset.domain = domain;
		chip.addEventListener('click', () => this.setDomain(domain));
		return chip;
	};

	this._renderNote = function () {
		let text = '';
		let isError = false;
		if (_signedOut) {
			text = _str('pharos-error-signed-out-detail');
		}
		else if (_refreshError) {
			// Outranks the progress line: a refresh that was refused is the more
			// recent answer to what the user just did, and it is cleared the
			// moment the sweep it is about ends.
			text = _refreshError;
			isError = true;
		}
		else if (_busy()) {
			text = _progressText();
		}
		else if (_statusReady && _status.last_run && _status.last_run.status == 'error') {
			text = _status.last_run.error
				? _fmt('pharos-daily-last-run-failed-detail',
					{ error: _clip(_status.last_run.error) })
				: _str('pharos-daily-last-run-failed');
			isError = true;
		}
		_el.note.textContent = text;
		_el.note.classList.toggle('is-err', isError);
		_el.note.hidden = !text;
	};

	this._renderBanner = function () {
		let show = _bannerShown();
		_el.banner.hidden = !show;
		if (show) {
			_el.bannerText.textContent = _str('pharos-daily-no-llm');
			_el.banner.title = _providerText();
		}
	};

	this._renderRail = function () {
		// Skipped when nothing it draws changed. During a sweep this runs every
		// 1.5s, and rebuilding it moves keyboard focus off whichever date row
		// had it -- the same defect the detail pane had, with the same cause:
		// replaceChildren() discards the focused node, and focus falls to the
		// document. A user arrowing through dates while a sweep runs simply
		// loses their place, twice a second, with nothing to explain it.
		let key = _railKey();
		if (key === _railDrawn) {
			return;
		}
		_railDrawn = key;

		_el.rail.replaceChildren();

		// Exactly one note, or the dates. Never both.
		let note = null;
		if (_signedOut) {
			note = _str('pharos-daily-rail-empty');
		}
		else if (_datesState == 'loading') {
			note = _str('pharos-daily-loading');
		}
		else if (_datesState == 'error') {
			let el = _div('pharos-dv-rail-note is-err', _str('pharos-daily-rail-unreachable'));
			_el.rail.append(el);
			return;
		}
		else if (_railEmpty()) {
			// The rail lists dates that matched YOU, so "empty" here says the
			// same thing the centre panel spells out, kept terse for its width.
			if (_noDirections()) {
				note = _str('pharos-daily-rail-no-directions');
			}
			else if (_everSwept()) {
				note = _str('pharos-daily-rail-no-match');
			}
			else {
				note = _str('pharos-daily-rail-empty');
			}
		}
		if (note !== null) {
			_el.rail.append(_div('pharos-dv-rail-note', note));
			return;
		}

		for (let summary of _dates) {
			let row = _btn('pharos-dv-date' + (summary.date == _activeDate ? ' is-active' : ''));
			row.dataset.date = summary.date;
			row.append(_span('pharos-dv-date-text', summary.date));
			if (summary.pending > 0) {
				// "three papers on Tuesday are still unread" was fetched and
				// thrown away by the old single-select date control.
				let pending = _span('pharos-dv-date-pending', String(summary.pending));
				pending.title = _fmt('pharos-daily-date-pending', { count: summary.pending });
				row.append(pending);
			}
			row.append(_span('pharos-dv-date-count', String(summary.total)));
			row.addEventListener('click', () => this.selectDate(summary.date));
			_el.rail.append(row);
		}
	};

	this._renderList = function () {
		// Preserved across a rebuild so a poll partway through a sweep does not
		// throw the reader back to the top of the day.
		let scroll = _el.list.scrollTop;
		_el.list.replaceChildren();

		if (_signedOut) {
			_el.list.append(this._empty(_str('pharos-error-signed-out-detail'), null, true));
			return;
		}
		// A failed /dates must NOT blank a day that loaded. The two are separate
		// requests, and the rail already reports the dates failure on its own
		// (see _renderRail), so the only case where a dates failure belongs here
		// is the one that left no date to ask about. Gating on either state was
		// worse than cosmetic: it replaced a fully loaded list -- including the
		// reading the user had just paid for, since read() reloads the dates --
		// with an error panel, and `!_dayError` then made it print "make sure
		// the service is running" about a service that had just answered.
		if (_dayState == 'error' || (_datesState == 'error' && !_activeDate)) {
			// The server's own detail wherever there is one. The hint is only
			// added for a connection that never completed, because "start the
			// service" is wrong advice for a request the service answered.
			let unreachable = _dayUnreachable || (!_dayError && _dayState == 'error');
			_el.list.append(this._empty(
				_dayError || _str('pharos-daily-error'),
				unreachable ? _str('pharos-daily-unreachable-hint') : null,
				true
			));
			return;
		}

		// The whole-module empty states, in the order their causes nest. Each
		// one is a different person's problem and names a different fix, which
		// is the entire reason they are not one sentence.
		if (_digestOff()) {
			// First, because with the module switched off nothing downstream can
			// reach this view no matter how many directions are configured.
			_el.list.append(this._firstUse('pharos-daily-disabled-title',
				'pharos-daily-disabled-desc',
				{ label: 'pharos-daily-open-settings', action: () => this.openDirections() }));
			return;
		}
		if (_noDirections()) {
			// With no enabled direction the backend matches nothing by
			// construction, so every emptiness downstream is a consequence of
			// this one and "nothing matched" would name the symptom.
			_el.list.append(this._firstUse('pharos-daily-no-directions-title',
				'pharos-daily-no-directions-desc',
				{ label: 'pharos-daily-open-settings', action: () => this.openDirections() }));
			return;
		}
		if (_railEmpty() && !_everSwept()) {
			_el.list.append(this._firstUse('pharos-daily-firstuse-title',
				'pharos-daily-firstuse-desc',
				{
					label: _busy() ? 'pharos-daily-refreshing' : 'pharos-daily-refresh',
					action: () => this.refresh(),
					disabled: _busy(),
				},
				{ label: 'pharos-daily-edit-directions', action: () => this.openDirections() }));
			return;
		}
		if (_railEmpty()) {
			// Swept, but nothing matched THIS reader. The fix is theirs, so the
			// settings action leads and 重新抓取 follows.
			_el.list.append(this._firstUse('pharos-daily-nomatch-title',
				'pharos-daily-nomatch-desc',
				{ label: 'pharos-daily-edit-directions', action: () => this.openDirections() },
				{
					label: _busy() ? 'pharos-daily-refreshing' : 'pharos-daily-refetch',
					action: () => this.refresh(),
					disabled: _busy(),
				}));
			return;
		}

		if (_dayState == 'loading') {
			_el.list.append(this._empty(_str('pharos-daily-loading')));
			return;
		}

		if (!_papers.length) {
			// The same triage for one date the user navigated to. `run` is what
			// tells "nobody swept this day" apart from "it was swept and you
			// matched none of it", and they want different words.
			if (!_day || !_day.run) {
				_el.list.append(this._empty(_str('pharos-daily-day-unswept'),
					_str('pharos-daily-day-unswept-hint')));
			}
			else {
				_el.list.append(this._empty(
					_str('pharos-daily-day-nomatch'),
					_fmt('pharos-daily-day-nomatch-hint', { fetched: _day.run.fetched || 0 }),
					false,
					{ label: 'pharos-daily-edit-directions', action: () => this.openDirections() }
				));
			}
			return;
		}

		// No "this chip is empty" state: the active domain is derived from the
		// chips, every one of which has at least one paper today.
		for (let paper of _visible()) {
			_el.list.append(this._card(paper));
		}
		_el.list.scrollTop = scroll;
	};

	this._empty = function (text, hint, isError, link) {
		let wrap = _div('pharos-dv-empty');
		let body = _div('pharos-dv-empty-text' + (isError ? ' is-err' : ''), text);
		if (hint) {
			body.append(_span('pharos-dv-empty-hint', hint));
		}
		if (link) {
			let button = _btn('pharos-dv-empty-link', _str(link.label));
			button.addEventListener('click', link.action);
			body.append(button);
		}
		wrap.append(body);
		return wrap;
	};

	this._firstUse = function (titleID, descID, primary, ghost) {
		let wrap = _div('pharos-dv-firstuse');
		let inner = _div('pharos-dv-firstuse-inner');
		inner.append(_div('pharos-dv-firstuse-mark'));
		inner.append(_div('pharos-dv-firstuse-title', _str(titleID)));
		inner.append(_div('pharos-dv-firstuse-desc', _str(descID)));

		// The reader's current directions, so the configuration that produced
		// the emptiness can be checked against it without opening settings.
		let directions = _directions();
		if (directions.length) {
			let dirs = _div('pharos-dv-dirs');
			dirs.append(_span('pharos-dv-dirs-k', _str('pharos-daily-directions-label')));
			for (let name of directions) {
				dirs.append(_span('pharos-dv-dirs-v', name));
			}
			inner.append(dirs);
		}

		let buttons = _div('pharos-dv-firstuse-btns');
		for (let [spec, className] of [[primary, 'pharos-dv-cta'], [ghost, 'pharos-dv-cta-ghost']]) {
			if (!spec) {
				continue;
			}
			let button = _btn(className, _str(spec.label));
			button.disabled = !!spec.disabled;
			button.addEventListener('click', spec.action);
			buttons.append(button);
		}
		inner.append(buttons);
		wrap.append(inner);
		return wrap;
	};

	this._card = function (paper) {
		let card = _div('pharos-dv-card' + (paper.id == _selectedID ? ' is-selected' : ''));
		card.dataset.paperId = paper.id;

		// Reachable from the keyboard, the way every date in the rail already is
		// -- those are real buttons, so the dates, the sort toggle, the chips and
		// 更新 could all be tabbed to, and then a paper could not be selected at
		// all, which put the entire detail panel (summary, highlights, scores,
		// 导入文库, 解读) out of reach. A div rather than a <button> because the
		// card is a stack of line-clamped paragraphs; the role and the key
		// handler are what make it announce and behave as the control it is.
		card.tabIndex = 0;
		card.setAttribute('role', 'button');
		card.setAttribute('aria-pressed', paper.id == _selectedID ? 'true' : 'false');
		card.addEventListener('click', () => this.selectPaper(paper.id));
		card.addEventListener('keydown', (event) => {
			if (event.key != 'Enter' && event.key != ' ') {
				return;
			}
			// Space would otherwise page the list, and it is the half of the
			// button contract a div does not get for free.
			event.preventDefault();
			this.selectPaper(paper.id);
		});

		let top = _div('pharos-dv-card-top');
		let score = _span('pharos-dv-score ' + _scoreTier(paper.score_recommendation),
			_fmtScore(paper.score_recommendation));
		score.title = _str('pharos-daily-score-tooltip');
		top.append(score);
		if (paper.matched_domain) {
			top.append(_span('pharos-dv-domain', paper.matched_domain));
		}
		top.append(_span('pharos-dv-card-title', paper.title));
		card.append(top);

		let authors = _authorText(paper.authors);
		if (authors) {
			card.append(_div('pharos-dv-card-meta', authors));
		}

		if (paper.read_status == 'done') {
			// Nothing at all when a paper was read and produced no summary: an
			// empty bordered row reads as "no data" rather than as "there is
			// simply nothing more to say about this one".
			let summary = (paper.summary_zh || '').trim();
			if (summary) {
				card.append(_div('pharos-dv-card-sum', summary));
			}
		}
		else {
			let row = _div('pharos-dv-card-sum');
			row.append(this._readChip(paper));
			card.append(row);
		}
		return card;
	};

	this._readChip = function (paper) {
		return paper.read_status == 'error'
			? _span('pharos-dv-chip is-err', _str('pharos-daily-read-failed'))
			: _span('pharos-dv-chip is-pending', _str('pharos-daily-pending'));
	};

	/* --------------------------------------------------------- detail panel */

	/**
	 * Everything _renderDetail() draws, as one string.
	 *
	 * This panel holds the longest content in the module -- the Chinese summary,
	 * four highlight blocks and the whole English abstract -- and _tick() renders
	 * twice per list refresh, so during a sweep it was torn down and rebuilt
	 * every 1.5s. That destroyed any selection the reader was dragging, every
	 * time, for the length of the sweep: the text they were about to copy is
	 * anchored in nodes replaceChildren() throws away, and unlike a scroll offset
	 * a selection cannot be put back afterwards. So the panel is rebuilt only
	 * when something it draws has actually changed.
	 *
	 * EVERY input _renderDetail() and its helpers read has to appear here. One
	 * that does not is a panel that silently stops updating -- add to this in
	 * the same commit that adds to those.
	 */
	/**
	 * Everything _renderRail() reads, as one comparable value.
	 *
	 * Every branch of that function is represented here. A new input added there
	 * and not here means the rail silently stops updating for it -- which is why
	 * this sits directly above it rather than somewhere tidier.
	 */
	function _railKey() {
		return JSON.stringify([
			_signedOut,
			_datesState,
			_activeDate,
			_railEmpty(),
			_noDirections(),
			_everSwept(),
			_dates.map(d => [d.date, d.pending, d.total]),
		]);
	}

	function _detailKey(paper) {
		if (!paper) {
			return 'none';
		}
		let entry = _inLibrary.get(paper.id);
		return JSON.stringify([
			paper,
			_reading.has(paper.id),
			_readErrors.get(paper.id) || null,
			_importing.has(paper.id),
			_importErrors.get(paper.id) || null,
			!!(entry && entry.item),
			!_status || _status.llm_configured,
		]);
	}

	/**
	 * Answer "is this already in MY library" for one paper, at most once per
	 * state of the library.
	 *
	 * Called before _detailKey() rather than from inside _importRow(), so the
	 * pending answer is already recorded when the key is taken: started from
	 * inside the render it feeds, the first key would say "not looked up" and
	 * the second "looked up, not found", and the panel would rebuild a second
	 * time under a reader who had just started selecting text.
	 *
	 * The answer is a local archiveID search, not paper.imported_paper_id --
	 * that column names a row in the web library and is blanked for anyone who
	 * does not own it.
	 */
	this._ensureInLibrary = function (paper) {
		let entry = _inLibrary.get(paper.id);
		if (entry && entry.token === _libraryToken) {
			return;
		}
		// The previous answer is kept, and only its token moves: until the
		// re-check lands, "in the library" is still the best thing known.
		_inLibrary.set(paper.id, { token: _libraryToken, item: entry ? entry.item : null });
		let token = _libraryToken;
		Zotero.Pharos.Daily.findInLibrary(paper).then((item) => {
			// The library changed again while this was running, and a newer
			// lookup is already on its way with a better answer.
			if (token !== _libraryToken) {
				return;
			}
			let current = _inLibrary.get(paper.id);
			if (current && current.item === item) {
				return;
			}
			_inLibrary.set(paper.id, { token, item });
			if (_selectedID == paper.id) {
				this._renderDetail();
			}
		});
	};

	this._renderDetail = function () {
		if (_destroyed) {
			return;
		}
		let paper = _selected();
		if (paper) {
			this._ensureInLibrary(paper);
		}
		let key = _detailKey(paper);
		if (key === _detailDrawn) {
			return;
		}
		let differentPaper = !paper || paper.id != _detailDrawnID;
		_detailDrawn = key;
		_detailDrawnID = paper ? paper.id : null;

		_el.detail.replaceChildren();
		if (differentPaper) {
			// A different paper is a different document and belongs at the top.
			//
			// This is the ONLY scroll handling the panel needs, and it is the
			// opposite of what it looks like: replaceChildren() and the append
			// below run inside one synchronous call, so no layout ever observes
			// the box empty and the offset is NOT lost on a rebuild -- which is
			// right for a poll redrawing the same paper and wrong for a paper the
			// reader has just clicked, who would otherwise land halfway down it.
			_el.detail.scrollTop = 0;
		}
		if (!paper) {
			_el.detail.append(_div('pharos-dv-detail-empty', _str('pharos-daily-detail-empty')));
			return;
		}

		let body = _div('pharos-dv-detail-body');
		body.append(_div('pharos-dv-d-title', paper.title));
		body.append(_div('pharos-dv-d-sub',
			(paper.arxiv_id || '') + (paper.venue ? ` · ${paper.venue}` : '')));

		body.append(this._importRow(paper));
		let importError = _importErrors.get(paper.id);
		if (importError) {
			body.append(_div('pharos-dv-d-err', importError));
		}

		// The read state is stated before any content, so an empty section is
		// never ambiguous between "not read" and "read, nothing to say".
		if (paper.read_status != 'done') {
			body.append(this._readRow(paper));
		}

		let done = paper.read_status == 'done';
		let summary = done ? (paper.summary_zh || '').trim() : '';
		if (summary) {
			let section = this._section('pharos-daily-section-summary');
			section.append(_div('pharos-dv-d-summary', summary));
			body.append(section);
		}

		if (done && paper.highlights) {
			let section = this._section('pharos-daily-section-highlights');
			let any = false;
			for (let key of HIGHLIGHT_KEYS) {
				let text = (paper.highlights[key] || '').trim();
				if (!text) {
					continue;
				}
				any = true;
				let block = _div('pharos-dv-hl');
				block.append(_div('pharos-dv-hl-k', _str('pharos-daily-highlight-' + key)));
				block.append(_div('pharos-dv-hl-v', text));
				section.append(block);
			}
			if (any) {
				body.append(section);
			}
		}

		if (done && paper.scores) {
			body.append(this._scores(paper.scores));
		}

		body.append(this._info(paper));

		if (paper.abstract) {
			let section = this._section('pharos-daily-section-abstract');
			section.append(_div('pharos-dv-d-abstract', paper.abstract));
			body.append(section);
		}

		body.append(this._links(paper));
		_el.detail.append(body);
	};

	this._section = function (labelID) {
		let section = _div('pharos-dv-d-sec');
		section.append(_div('pharos-dv-d-label', _str(labelID)));
		return section;
	};

	this._importRow = function (paper) {
		// Answered by _ensureInLibrary(), which _renderDetail() has already
		// called: the lookup must not start from inside the render whose key
		// depends on it.
		let entry = _inLibrary.get(paper.id);
		let existing = entry && entry.item;
		let importing = _importing.has(paper.id);

		let actions = _div('pharos-dv-d-actions');
		let button = _btn('pharos-dv-d-primary');
		button.id = 'pharos-dv-import';
		button.disabled = !!existing || importing;
		button.append(_span('pharos-dv-d-ic'));
		let label = existing
			? 'pharos-daily-imported'
			: importing ? 'pharos-daily-importing' : 'pharos-daily-import';
		button.append(_span('pharos-dv-d-primary-label', _str(label)));
		button.addEventListener('click', () => this.importToLibrary(paper));
		actions.append(button);
		return actions;
	};

	this._readRow = function (paper) {
		let failed = paper.read_status == 'error';
		let block = _div('pharos-dv-d-state is-col');
		let row = _div('pharos-dv-d-state-row');
		row.append(this._readChip(paper));

		let canRead = !_status || _status.llm_configured;
		// This paper's own read, not any read: a request running on another card
		// used to disable this button and label it 解读中… with nothing at all
		// happening to the paper in front of the reader.
		let reading = _reading.has(paper.id);
		let label;
		if (reading) {
			label = failed ? 'pharos-daily-retrying' : 'pharos-daily-reading';
		}
		else {
			label = failed ? 'pharos-daily-retry' : 'pharos-daily-read';
		}
		let button = _btn('pharos-dv-d-ghost', _str(label));
		button.disabled = reading || !canRead;
		if (!canRead) {
			// Reading would 503, so the button says why up front rather than
			// letting the click fail with nothing to act on.
			button.title = _str('pharos-daily-no-llm-tooltip');
		}
		button.addEventListener('click', () => this.read(paper));
		row.append(button);
		block.append(row);

		// The stored failure, then the failure of the retry itself. Two
		// different events; collapsing them would hide the newer one. Both
		// belong to THIS paper -- a message keyed by nothing was worse than
		// collapsing them, because it attributed one paper's failure to another.
		if (failed && paper.read_error) {
			block.append(_div('pharos-dv-d-err', _clip(paper.read_error)));
		}
		let readError = _readErrors.get(paper.id);
		if (readError) {
			block.append(_div('pharos-dv-d-err', _fmt(
				failed ? 'pharos-daily-retry-failed' : 'pharos-daily-read-failed-detail',
				{ error: _clip(readError) }
			)));
		}
		return block;
	};

	this._scores = function (scores) {
		let section = this._section('pharos-daily-section-scores');
		for (let key of SCORE_KEYS) {
			let value = scores[key];
			// A 0-10 scale; the bar is what makes the spread between dimensions
			// legible at a glance.
			let pct = typeof value == 'number' ? Math.max(0, Math.min(100, value * 10)) : 0;
			let row = _div('pharos-dv-sc' + (key == 'recommendation' ? ' is-rec' : ''));
			row.title = _str('pharos-daily-score-' + key + '-hint');
			row.append(_span('pharos-dv-sc-k', _str('pharos-daily-score-' + key)));
			let track = _span('pharos-dv-sc-track');
			let bar = _span('pharos-dv-sc-bar');
			bar.style.width = pct + '%';
			track.append(bar);
			row.append(track);
			row.append(_span('pharos-dv-sc-v', _fmtScore(value)));
			section.append(row);
		}
		// Said once, in the one place both kinds of number are on screen at all.
		section.append(_div('pharos-dv-sc-note', _str('pharos-daily-score-note')));
		return section;
	};

	this._info = function (paper) {
		let section = this._section('pharos-daily-section-info');
		let grid = _div('pharos-dv-d-grid');
		// 方向 and 命中 are the caller's OWN direction and the caller's own
		// keywords that fired, resolved per request rather than read off the
		// shared row -- hence the tooltips.
		let rows = [
			{
				label: 'pharos-daily-info-authors',
				value: _authorText(paper.authors),
			},
			{
				label: 'pharos-daily-info-direction',
				value: paper.matched_domain,
				hint: 'pharos-daily-info-direction-hint',
			},
			{
				label: 'pharos-daily-info-categories',
				value: (paper.categories || []).join(', '),
			},
			{
				label: 'pharos-daily-info-keywords',
				value: (paper.matched_keywords || []).join(', '),
				hint: 'pharos-daily-info-keywords-hint',
			},
		];
		for (let row of rows) {
			let key = _span('pharos-dv-d-k', _str(row.label));
			if (row.hint) {
				key.title = _str(row.hint);
			}
			grid.append(key);
			grid.append(_span('pharos-dv-d-v', row.value || _str('pharos-daily-none')));
		}
		section.append(grid);
		return section;
	};

	this._links = function (paper) {
		let section = _div('pharos-dv-d-sec-last');
		let links = _div('pharos-dv-d-links');
		for (let [url, labelID] of [
			[paper.arxiv_url, 'pharos-daily-open'],
			[paper.pdf_url, 'pharos-daily-open-pdf'],
		]) {
			if (!url) {
				continue;
			}
			// A button rather than an <a href>: these go to the system browser,
			// never to an in-app tab.
			let button = _btn('pharos-dv-d-link');
			button.dataset.url = url;
			button.append(_span('pharos-dv-d-ic'));
			button.append(_span(null, _str(labelID)));
			button.addEventListener('click', () => Zotero.launchURL(url));
			links.append(button);
		}
		section.append(links);
		return section;
	};
};

/**
 * The rail's handle on this view.
 *
 * A fixed name so `pharosRail.js` can call `onShown()` without knowing which
 * module its browser is showing. Modules that have nothing to recheck simply do
 * not define this.
 */
var PharosView = Zotero_Pharos_Daily;
