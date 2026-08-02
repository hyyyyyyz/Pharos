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
 * The daily arXiv digest window.
 *
 * Its own window rather than a pane in the library: the digest is a list of
 * papers you do NOT have yet, and mixing them into the item tree would blur the
 * line between "my library" and "today's candidates".
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
 */
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
	 */
	const LIST_EVERY = 2;

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

	let _reading = false;
	let _readError = null;
	let _importing = false;
	let _importError = null;

	/** paper id -> Zotero.Item or null. Populated lazily by findInLibrary(),
	 *  which never throws, so a failed lookup reads as "not in the library"
	 *  rather than taking the panel down. */
	let _inLibrary = new Map();

	let _signedOut = false;

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
	 * What to show the user for a failed request.
	 *
	 * The server's own `detail` wherever there is one -- Zotero.Pharos.API
	 * unwraps it -- because rewriting it would mean guessing which failure
	 * occurred. A connection that never completed is the exception: its message
	 * names the URL and "status code 0", which is not something anyone can act
	 * on.
	 */
	function _errorText(e) {
		if (e instanceof Zotero.HTTP.TimeoutException || !e || !e.message || e.status === 0) {
			return Zotero.getString('pharos-error-unreachable');
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
			refresh: document.getElementById('pharos-daily-refresh'),
			refreshIcon: document.getElementById('pharos-dv-refresh-ic'),
			refreshLabel: document.getElementById('pharos-dv-refresh-label'),
			note: document.getElementById('pharos-daily-status'),
			banner: document.getElementById('pharos-dv-banner'),
			bannerText: document.getElementById('pharos-dv-banner-text'),
			list: document.getElementById('pharos-dv-list'),
			detail: document.getElementById('pharos-dv-detail'),
		};

		_el.sortScore.addEventListener('click', () => this.setSort('score'));
		_el.sortTime.addEventListener('click', () => this.setSort('time'));
		// `click` only. This is an html:button, which has no `command` event;
		// the XUL button it replaced needed both and rendered a blank label.
		_el.refresh.addEventListener('click', () => this.refresh());

		if (!Zotero.Pharos.API.hasCredentials()) {
			_signedOut = true;
			this._render();
			return;
		}

		this._render();

		// Sequential rather than parallel: the empty-state triage reads all
		// three, and a screen assembled from two of them flashes the wrong
		// diagnosis on the way to the right one.
		await this.loadConfig();
		await this.loadStatus();
		await this.loadDates();
		// No explicit selection yet -> the newest digest, which is what someone
		// opening this window in the morning wants.
		await this.load(_dates.length ? _dates[0].date : Zotero.Pharos.Daily.today());

		// A sweep may already have been running before this window opened.
		this._syncPoll();
		this._render();
	};

	this.destroy = function () {
		// Otherwise a sweep observed here keeps polling after the window is gone.
		if (_pollTimer) {
			clearTimeout(_pollTimer);
			_pollTimer = null;
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
			_dayUnreachable = _dayError == Zotero.getString('pharos-error-unreachable');
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
		// These belong to an attempt on the paper being left behind.
		_readError = null;
		_importError = null;
		// Classes only, so the list does not rebuild and lose its scroll offset.
		for (let card of _el.list.querySelectorAll('.pharos-dv-card')) {
			card.classList.toggle('is-selected', card.dataset.paperId == id);
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
		if (_reading) {
			return;
		}
		_reading = true;
		_readError = null;
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
				_readError = _str('pharos-daily-read-unavailable');
			}
			else {
				_readError = _errorText(e);
			}
		}
		finally {
			_reading = false;
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
		if (_importing) {
			return;
		}
		_importing = true;
		_importError = null;
		this._renderDetail();
		try {
			let item = await Zotero.Pharos.Daily.saveToLibrary(paper);
			_inLibrary.set(paper.id, item);
		}
		catch (e) {
			Zotero.logError(e);
			_importError = _fmt('pharos-daily-import-failed', { error: _clip(_errorText(e)) });
		}
		finally {
			_importing = false;
		}
		this._renderDetail();
	};

	/* ---------------------------------------------------------------- polling */

	this._syncPoll = function () {
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
	};

	/* ---------------------------------------------------------------- render */

	this._render = function () {
		this._renderToolbar();
		this._renderNote();
		this._renderBanner();
		this._renderRail();
		this._renderList();
		this._renderDetail();
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

		_el.count.textContent = _dayState == 'ready' && !_signedOut
			? _fmt('pharos-daily-count', { count: _visible().length })
			: '';

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
		if (_datesState == 'error' || _dayState == 'error') {
			// The server's own detail wherever there is one. The hint is only
			// added for a connection that never completed, because "start the
			// service" is wrong advice for a request the service answered.
			let unreachable = _dayUnreachable || !_dayError;
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
		card.addEventListener('click', () => this.selectPaper(paper.id));

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

	this._renderDetail = function () {
		_el.detail.replaceChildren();
		let paper = _selected();
		if (!paper) {
			_el.detail.append(_div('pharos-dv-detail-empty', _str('pharos-daily-detail-empty')));
			return;
		}

		let body = _div('pharos-dv-detail-body');
		body.append(_div('pharos-dv-d-title', paper.title));
		body.append(_div('pharos-dv-d-sub',
			(paper.arxiv_id || '') + (paper.venue ? ` · ${paper.venue}` : '')));

		body.append(this._importRow(paper));
		if (_importError) {
			body.append(_div('pharos-dv-d-err', _importError));
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
		// The answer to "is this already in MY library" is a local archiveID
		// search, not paper.imported_paper_id -- that column names a row in the
		// web library and is blanked for anyone who does not own it.
		if (!_inLibrary.has(paper.id)) {
			_inLibrary.set(paper.id, null);
			Zotero.Pharos.Daily.findInLibrary(paper).then((item) => {
				_inLibrary.set(paper.id, item);
				if (_selectedID == paper.id) {
					this._renderDetail();
				}
			});
		}
		let existing = _inLibrary.get(paper.id);

		let actions = _div('pharos-dv-d-actions');
		let button = _btn('pharos-dv-d-primary');
		button.id = 'pharos-dv-import';
		button.disabled = !!existing || _importing;
		button.append(_span('pharos-dv-d-ic'));
		let label = existing
			? 'pharos-daily-imported'
			: _importing ? 'pharos-daily-importing' : 'pharos-daily-import';
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
		let label;
		if (_reading) {
			label = failed ? 'pharos-daily-retrying' : 'pharos-daily-reading';
		}
		else {
			label = failed ? 'pharos-daily-retry' : 'pharos-daily-read';
		}
		let button = _btn('pharos-dv-d-ghost', _str(label));
		button.disabled = _reading || !canRead;
		if (!canRead) {
			// Reading would 503, so the button says why up front rather than
			// letting the click fail with nothing to act on.
			button.title = _str('pharos-daily-no-llm-tooltip');
		}
		button.addEventListener('click', () => this.read(paper));
		row.append(button);
		block.append(row);

		// The stored failure, then the failure of the retry itself. Two
		// different events; collapsing them would hide the newer one.
		if (failed && paper.read_error) {
			block.append(_div('pharos-dv-d-err', _clip(paper.read_error)));
		}
		if (_readError) {
			block.append(_div('pharos-dv-d-err', _fmt(
				failed ? 'pharos-daily-retry-failed' : 'pharos-daily-read-failed-detail',
				{ error: _clip(_readError) }
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
