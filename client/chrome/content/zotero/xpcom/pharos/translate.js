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
 * Layout-preserving translation (保排版翻译).
 *
 * The backend runs BabelDOC, which rebuilds the PDF with the translated text in
 * the original layout -- figures, tables, equations and page breaks all stay put.
 * That takes a Python toolchain with native dependencies, so it cannot live in
 * this process; the client uploads, polls, and downloads.
 *
 * The result is imported as an ordinary PDF attachment on the same item rather
 * than shown from a remote URL. That is the whole point of doing this inside a
 * reference manager: the translation opens in the same reader, takes
 * highlights and notes like any other PDF, syncs, and is still there offline.
 *
 * Modelled on Zotero.RecognizeDocument, which is the closest existing shape --
 * a queued, per-item, long-running remote operation with a progress dialog.
 */
Zotero.Pharos.Translate = new function () {
	// The backend also exposes SSE, but polling matches the web client and needs
	// no streaming support in this process.
	const POLL_INTERVAL = 2000;

	/** A translation of a long PDF legitimately takes minutes. This is the point
	 *  at which we stop believing the job is still alive. */
	const POLL_TIMEOUT = 30 * 60 * 1000;

	/** How much of a failure fits in a queue row. A backend failure arrives as
	 *  whatever the engine printed, which can be a whole Python traceback, and
	 *  the row is one line in a small dialog: untruncated, it blows the column
	 *  out and pushes everything else off screen. Same length the web client
	 *  uses (frontend/src/components/ReadingView.tsx ERROR_MAX), and nothing is
	 *  lost by it -- _processQueue() has already called Zotero.logError() on the
	 *  error itself, which logs the message in full and debug-logs the error
	 *  object with its stack. */
	const ERROR_MAX = 200;

	/**
	 * Where Zotero.Pharos.API.cacheUser() leaves this account's
	 * `pdf_translation`. See isEnabled() for how an absent value is resolved.
	 */
	const ENABLED_PREF = 'pharos.pdfTranslation';

	/** The column's key before ItemTreeManager namespaces it with the app id. */
	const COLUMN_KEY = 'pharosTranslation';

	let _queue = [];
	let _queueProcessing = false;
	let _cancelled = false;

	/**
	 * Attachment id -> what this process knows about its most recent run.
	 *
	 * Memory only, and the item pane section is built around that limitation
	 * rather than around this map -- see getState().
	 *
	 * Shape: { state, mode, phase, message, progress, stage, stageIndex, error }
	 */
	let _jobs = new Map();

	/** Called with an attachment id whenever its entry in _jobs changes. */
	let _stateListeners = new Set();

	/** The localized filename suffixes, resolved on first use. */
	let _suffixes = null;

	/** The localized state labels, resolved on first use. */
	let _stateLabels = null;

	/**
	 * Attachment id -> page count, or null for "asked, and there is none".
	 *
	 * A Map with a meaningful `has()`: absent means the question has not been
	 * put yet, and only that is worth another query. Zotero.Fulltext.getPages()
	 * is a DB read, and both surfaces that show this run on every selection
	 * change, so a PDF the indexer has never reached must be asked about once
	 * and then left alone -- otherwise a library of unindexed PDFs pays a query
	 * per row for an answer that will not change.
	 */
	let _pageCounts = new Map();

	/** The dataKey ItemTreeManager gave the column back, or null. */
	let _columnKey = null;

	let _progressQueue = Zotero.ProgressQueues.create({
		id: 'pharos-translate',
		title: 'pharos-translate-title',
		columns: [
			'pharos-translate-column-attachment',
			'pharos-translate-column-status'
		]
	});

	_progressQueue.addListener('cancel', function () {
		// Before _queue is emptied: everything still in it was reported as
		// running the moment it was queued, and nothing downstream will ever
		// touch those entries again. Left alone they would sit at "翻译中" in
		// the item pane for the rest of the session -- the exact stale-forever
		// state the section exists to remove.
		for (let entry of _queue) {
			_setJob(entry.attachment.id, {
				state: Zotero.Pharos.Translate.STATE_FAILED,
				phase: null,
				message: '',
				error: Zotero.getString('pharos-translate-error-cancelled'),
			});
		}
		_queue = [];
		_cancelled = true;
	});

	/**
	 * The two ways BabelDOC can emit a translation.
	 *
	 * "mono" is the translated text alone; "dual" interleaves the original and
	 * the translation page by page. Users who read alongside the source want
	 * dual; users reading to absorb want mono.
	 *
	 * Both come out of the same job, so this no longer decides which file is
	 * kept -- whatever the job produced is imported. What it still decides is
	 * which one the run is reported as having made, and so which one the user
	 * is handed when it finishes.
	 */
	this.MODE_MONO = 'mono';
	this.MODE_DUAL = 'dual';

	/**
	 * The reader toolbar uses the same two commands as the item context menu.
	 *
	 * renderToolbar's append target is Reader's CustomSections container, which
	 * sits immediately before the built-in find button. Keeping the entry here,
	 * rather than patching the Reader React tree, also means upstream toolbar
	 * changes cannot silently move it to a different group.
	 */
	this._renderToolbar = ({ reader, doc, append }) => {
		let attachment = reader && reader._item;
		if (!this.isEnabled() || !this.canTranslate(attachment)
				|| typeof reader._openContextMenu != 'function') {
			return;
		}

		let label = Zotero.getString('pharos-translate-menu');
		let button = doc.createElement('button');
		button.className = 'toolbar-button pharos-reader-translate-button';
		button.dataset.pharosReaderTranslate = 'true';
		button.title = label;
		button.setAttribute('aria-label', label);
		button.setAttribute('aria-haspopup', 'menu');
		button.append(_buildReaderToolbarIcon(doc));
		button.addEventListener('click', () => {
			let rect = button.getBoundingClientRect();
			try {
				Promise.resolve(reader._openContextMenu({
					x: rect.left,
					y: rect.bottom,
					itemGroups: [[
						{
							label: Zotero.getString('pharos-translate-menu-mono'),
							onCommand: () => _translateFromReader(
								reader, attachment, this.MODE_MONO
							),
						},
						{
							label: Zotero.getString('pharos-translate-menu-dual'),
							onCommand: () => _translateFromReader(
								reader, attachment, this.MODE_DUAL
							),
						},
					]],
				})).catch(Zotero.logError);
			}
			catch (e) {
				Zotero.logError(e);
			}
		});
		append(button);

		// Translate is loaded before ReaderChat, so its listener is called first.
		// Move this *section wrapper* after the synchronous renderToolbar dispatch
		// has let every other listener append, making translation the last custom
		// action and therefore the control directly beside Find. Moving the node,
		// rather than assigning a visual flex order, keeps keyboard and visual order
		// identical. The connection guards cover a React rerender that replaced the
		// CustomSections container before this microtask ran.
		Promise.resolve().then(() => {
			let section = button.parentElement;
			let container = section && section.parentElement;
			if (button.isConnected && container?.classList.contains('custom-sections')
					&& container.lastElementChild !== section) {
				container.append(section);
			}
		});
	};

	/**
	 * The four answers getState() is willing to give.
	 *
	 * STATE_UNKNOWN is the important one and is not a synonym for "not
	 * translated". See getState() for what it does and does not mean.
	 */
	this.STATE_UNKNOWN = 'unknown';
	this.STATE_RUNNING = 'running';
	this.STATE_FAILED = 'failed';
	this.STATE_TRANSLATED = 'translated';

	/**
	 * The number of steps the engine's stage strings are collapsed into.
	 *
	 * BabelDOC emits a free-form stage label and this module used to hand it
	 * straight to the progress row. Some of those labels are long and sit
	 * unchanged for minutes, which reads as a hang rather than as work. The web
	 * client answers that by mapping the label onto a fixed three-step stepper
	 * (frontend/src/lib/model.ts stageIndex): whatever the engine calls what it
	 * is doing, it is laying the page out, translating it, or putting the layout
	 * back.
	 *
	 * The mapping below is the web client's, term for term, deliberately -- a
	 * user with both surfaces open must not see one job described as being at
	 * two different steps. The raw label is not thrown away; it travels in the
	 * job record as `stage` and the item pane section shows it as a tooltip, so
	 * the engine's own word for it is one hover away.
	 */
	this.STAGE_COUNT = 3;

	/**
	 * @param {String} stage - the engine's raw stage label
	 * @param {Number} progress - 0-100
	 * @return {Number} 0, 1 or 2
	 */
	this.stageIndex = function (stage, progress) {
		let s = (stage || '').toLowerCase();
		if (s.includes('typeset') || s.includes('排版')) {
			return 2;
		}
		if (s.includes('translat') || s.includes('翻译')) {
			return 1;
		}
		if (s.includes('pars') || s.includes('解析') || s.includes('queue')) {
			return 0;
		}
		// An unrecognised label still has a percentage behind it, and a stepper
		// frozen at step one for a job that is 80% done would be a worse lie
		// than a rough guess.
		return Math.min(2, Math.floor(((progress || 0) / 100) * this.STAGE_COUNT));
	};

	/**
	 * Whether this account gets whole-PDF translation at all.
	 *
	 * Server-owned, not a device preference: the web client writes it onto the
	 * account and both surfaces have to agree, or the same account answers one
	 * way in a browser and the opposite way here.
	 *
	 * An ABSENT value resolves to true, and that direction is deliberate. The
	 * pref is only written once a login or a verify has seen the account, so
	 * "not set" means "not asked yet" -- and defaulting the other way would hide
	 * a feature the account actually has, every time, for the window between
	 * startup and the first successful verify. The web resolves it identically
	 * (frontend/src/store.ts pdfTranslationEnabled) for the same reason: a
	 * session blob written by an older build genuinely lacks the field.
	 *
	 * @return {Boolean}
	 */
	this.isEnabled = function () {
		let value = Zotero.Prefs.get(ENABLED_PREF);
		return value === undefined || value === null ? true : !!value;
	};

	/**
	 * How many pages a stored PDF has, or null if nothing knows yet.
	 *
	 * Synchronous by contract: both callers -- the item pane section and the
	 * item tree column -- run on every selection change, including a key held
	 * down to scroll a list, and neither may await. So this answers from the
	 * memo and starts a lookup for a question it has not been asked before,
	 * returning null for that first call.
	 *
	 * The number comes from Zotero's own full-text indexer, which is also where
	 * attachmentBox.js reads it. That means it is absent until the indexer has
	 * reached the file, and stays absent for a PDF it cannot read. Null is the
	 * honest answer in both cases, and the callers show nothing rather than a
	 * zero -- "0 pages" is a claim about the document.
	 *
	 * @param {Zotero.Item} attachment
	 * @return {Integer|null}
	 */
	this.getPageCount = function (attachment) {
		if (!attachment || !this.canTranslate(attachment)) {
			return null;
		}
		if (_pageCounts.has(attachment.id)) {
			return _pageCounts.get(attachment.id);
		}
		// Marked before the query resolves so a second render in the same tick
		// does not queue a second one for the same file.
		_pageCounts.set(attachment.id, null);
		Zotero.Fulltext.getPages(attachment.id)
			.then((pages) => {
				let total = pages && pages.total ? pages.total : null;
				if (total === _pageCounts.get(attachment.id)) {
					return;
				}
				_pageCounts.set(attachment.id, total);
				// Reuses the state channel rather than adding a second one: every
				// listener already redraws the whole row for this attachment, and
				// a page count arriving is exactly as interesting as a state
				// change to all of them.
				_notify(attachment.id);
			})
			.catch((e) => {
				// A page count is decoration. It must never take down the pane or
				// the row it was going to appear in.
				Zotero.logError(e);
			});
		return null;
	};

	/**
	 * Whether an item is something this can translate.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.canTranslate = function (item) {
		return item
			&& item.isAttachment()
			&& item.attachmentContentType == 'application/pdf'
			&& item.isStoredFileAttachment();
	};

	function _buildReaderToolbarIcon(doc) {
		let svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
		svg.setAttribute('width', '20');
		svg.setAttribute('height', '20');
		svg.setAttribute('viewBox', '0 0 24 24');
		svg.setAttribute('aria-hidden', 'true');

		let path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
		path.setAttribute('fill', 'currentColor');
		path.setAttribute(
			'd',
			'M12.87 15.07 10.33 12.56l.03-.03A17.5 17.5 0 0 0 14.07 6H17V4h-7V2H8v2H1v2h11.17A15.7 15.7 0 0 1 9 11.35 15.4 15.4 0 0 1 6.69 8h-2a17.5 17.5 0 0 0 3 4.5L2.6 17.52 4 18.93l5-5 3.11 3.11.76-1.97ZM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12Zm-2.62 7 1.62-4.33L19.12 17h-3.24Z'
		);
		svg.append(path);
		return svg;
	}

	function _translateFromReader(reader, attachment, mode) {
		let win = reader && reader._window;
		if (!Zotero.Pharos.API.hasCredentials()) {
			Services.prompt.alert(
				win || null,
				Zotero.getString('pharos-translate-title'),
				Zotero.getString('pharos-error-signed-out-detail')
			);
			return;
		}

		// translateItems() resolves only after the minutes-long queue is done.
		// Start it without holding the menu command open, and expose the existing
		// progress dialog immediately just like the library context-menu command.
		Zotero.Pharos.Translate.translateItems([attachment], mode)
			.catch(Zotero.logError);
		Zotero.ProgressQueues.get('pharos-translate').getDialog().open();
	}

	/**
	 * Whether an item, or something hanging off it, could be translated.
	 *
	 * Synchronous, because the context menu is built against the current
	 * selection and cannot await a lookup per item. getAttachments() returns ids
	 * from the already-loaded item, so no I/O happens here -- unlike
	 * getBestAttachment(), which translateItems() uses once the user has
	 * actually chosen to translate.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.hasTranslatableAttachment = function (item) {
		return !!this.getTranslatableAttachment(item);
	};

	/**
	 * The PDF an item's translation state is about, or null.
	 *
	 * Synchronous for the same reason hasTranslatableAttachment() is: the item
	 * pane section is rebuilt on every selection change, including an arrow key
	 * held down over a list, and must not do I/O to decide whether it belongs on
	 * screen.
	 *
	 * Where a regular item has several PDFs this prefers one that is not itself
	 * a Pharos translation. Translations are attached to the *same parent* as
	 * the file they were made from (see _importResult), so a translated paper
	 * normally has at least two PDF children and the order they come back in is
	 * not a promise. Picking the translation would show the state of the output
	 * instead of the state of the source, and offer to translate a translation.
	 *
	 * @param {Zotero.Item} item
	 * @return {Zotero.Item|null}
	 */
	this.getTranslatableAttachment = function (item) {
		if (!item) {
			return null;
		}
		if (this.canTranslate(item)) {
			return item;
		}
		if (!item.isRegularItem()) {
			return null;
		}
		let attachments = Zotero.Items.get(item.getAttachments())
			.filter(a => this.canTranslate(a));
		return attachments.find(a => !this.isTranslation(a)) || attachments[0] || null;
	};

	/**
	 * Whether an attachment is a translation this client produced.
	 *
	 * Two different questions get answered by two different pieces of evidence,
	 * and it matters which is which:
	 *
	 *   - *Which* paper a translation belongs to comes from the dc:relation
	 *     _relate() writes. That is the pairing, it is stored, it syncs, and it
	 *     survives renaming either file.
	 *   - *Whether* a given PDF is a translation at all comes from the suffix
	 *     _importResult() puts in its title. There is nowhere better to put that
	 *     flag: Zotero's relation predicates are a fixed set (Zotero.Relations
	 *     declares three and validates the namespace prefix), inventing one
	 *     would sync an unknown predicate to zotero.org, and the Pharos sidecar
	 *     must not be made load-bearing for a fact about a Zotero item.
	 *
	 * The known cost: the suffix is localized, so a user who switches locale
	 * after translating stops having their older translations recognised as
	 * translations. What that costs is the label and the role, not the link --
	 * the relation still shows both files as related, and the reader still opens
	 * both. It degrades to "we cannot tell", which is the failure this whole
	 * section is designed to be able to say out loud.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.isTranslation = function (item) {
		return !!this.getTranslationMode(item);
	};

	/**
	 * Which rendering an attachment is, or null if it is not a translation.
	 *
	 * Lets a caller offer the mode a paper does not have yet rather than both --
	 * a paper with a mono translation needs a "dual" button, not a second mono
	 * run it would pay full engine time for and then find it already had.
	 *
	 * @param {Zotero.Item} item
	 * @return {String|null} MODE_MONO, MODE_DUAL or null
	 */
	this.getTranslationMode = function (item) {
		let suffix = _translationSuffix(item);
		if (!suffix) {
			return null;
		}
		return suffix == _suffixes[0] ? this.MODE_MONO : this.MODE_DUAL;
	};

	/**
	 * The Pharos translations of an attachment, newest first.
	 *
	 * @param {Zotero.Item} attachment
	 * @return {Zotero.Item[]}
	 */
	this.getTranslations = function (attachment) {
		return _relatedPDFs(attachment).filter(a => this.isTranslation(a));
	};

	/**
	 * The PDF a translation was made from, or null.
	 *
	 * @param {Zotero.Item} translation
	 * @return {Zotero.Item|null}
	 */
	this.getOriginal = function (translation) {
		if (!this.isTranslation(translation)) {
			return null;
		}
		return _relatedPDFs(translation).find(a => !this.isTranslation(a)) || null;
	};

	/**
	 * Everything known about one item's translation, without touching the
	 * network.
	 *
	 * WHERE THE RESTING STATE COMES FROM, AND WHY
	 *
	 * There are two possible authorities. The backend is the real one: it holds
	 * every job this account has ever run, on any device, with its status and
	 * its error. This uses the *local library* instead -- the translated
	 * attachment and the dc:relation tying it to its source -- plus an in-memory
	 * record of jobs started in this process.
	 *
	 * Two reasons, and the second is the one that decided it:
	 *
	 *   1. An item pane section renders on every selection change. Asking the
	 *      backend here would put an HTTP request behind the down-arrow key.
	 *   2. There is no cheap question to ask it. The backend addresses papers by
	 *      the sha256 of their bytes, and the client keeps no durable
	 *      attachment -> paper-id map (Zotero.Pharos.Chat._paperIDs is
	 *      per-session and explains why). "Ask the backend about this
	 *      attachment" therefore begins by *uploading the file*, so the naive
	 *      fix costs a multi-MB POST per row scrolled past, not a GET.
	 *
	 * WHAT THIS CANNOT ANSWER, and what is done about it:
	 *
	 *   - A translation produced in the web client, or on another machine, that
	 *     has not synced into this library. There is no local trace of it.
	 *   - A run that failed, or is running right now, in another session --
	 *     including this account's own runs before the last restart, since _jobs
	 *     dies with the process.
	 *   - Whether a paper the user deleted the translation of was ever
	 *     translated.
	 *
	 * All three collapse into the same shape: no local evidence. That is
	 * reported as STATE_UNKNOWN and labelled as what is actually known ("no
	 * translation in this library"), never as "not translated" -- which would be
	 * a claim about the account, made from a library, and wrong precisely for
	 * the user who has been translating on the web.
	 *
	 * @param {Zotero.Item} item - an attachment, or an item with a PDF
	 * @return {Object|null} null when translation does not apply to this item
	 */
	this.getState = function (item) {
		// The account switch comes first, ahead of even asking what the item is:
		// an account with translation turned off must show no state, no buttons
		// and no column value, exactly as the web removes the whole apparatus.
		// A user who switched it off in a browser and finds 译文/对照 waiting for
		// them here is being told their own setting did not take.
		if (!this.isEnabled()) {
			return null;
		}
		let attachment = this.getTranslatableAttachment(item);
		if (!attachment) {
			return null;
		}

		let translations = this.getTranslations(attachment);
		let job = _jobs.get(attachment.id) || null;

		// A live run outranks everything: it is the newest fact there is. A
		// failure outranks an existing translation because it is a report on the
		// most recent attempt, and the translation it did not replace is still
		// reachable -- `translations` is returned either way, so the section can
		// say "the last run failed" and still offer the older result.
		let state;
		if (job && job.state == this.STATE_RUNNING) {
			state = this.STATE_RUNNING;
		}
		else if (job && job.state == this.STATE_FAILED) {
			state = this.STATE_FAILED;
		}
		else if (translations.length) {
			state = this.STATE_TRANSLATED;
		}
		else {
			state = this.STATE_UNKNOWN;
		}

		return {
			attachment,
			state,
			translations,
			// Only set when the selected item is itself a translation, which is
			// what the "back to the original" action needs.
			original: this.getOriginal(attachment),
			isTranslation: this.isTranslation(attachment),
			mode: (job && job.mode) || null,
			phase: (job && job.phase) || null,
			message: (job && job.message) || '',
			progress: (job && job.progress) || 0,
			stage: (job && job.stage) || '',
			stageIndex: (job && job.stageIndex) || 0,
			error: (job && job.error) || null,
		};
	};

	/**
	 * Run the failed job again, in the mode it was asked for the first time.
	 *
	 * Deliberately not a mode picker. A retry is a statement about the run, not
	 * a second chance to change your mind, and the mode the user chose is
	 * already recorded; asking again would make the common case (the server was
	 * briefly down) two clicks and a decision.
	 *
	 * @param {Zotero.Item} attachment
	 * @return {Promise}
	 */
	this.retry = function (attachment) {
		let job = _jobs.get(attachment.id);
		return this.translateItems([attachment], (job && job.mode) || this.MODE_MONO);
	};

	/**
	 * Subscribe to job-state changes.
	 *
	 * Progress arrives from a poll loop rather than from the data layer, so
	 * Zotero.Notifier never hears about it -- an item pane section watching the
	 * item would see nothing move until the translation was imported at the very
	 * end. Listeners are called with the attachment id that changed.
	 *
	 * @param {Function} listener
	 */
	/**
	 * Put the translation state in the item tree.
	 *
	 * The section shows this well, but only for the row that is selected -- so
	 * "which of these forty papers have I translated?" needs forty clicks. The
	 * column is the answer to that question, and it is cheap here for one
	 * reason: getState() is synchronous by construction, which is exactly the
	 * contract dataProvider has. Nothing below touches the network or the
	 * database, because a provider runs per visible row on every scroll.
	 *
	 * Registered lazily and idempotently. Zotero.Pharos loads before the item
	 * tree exists in any window, and a second call would otherwise add a second
	 * column with the same label.
	 *
	 * @return {String|null} the namespaced dataKey, or null if registration
	 *     failed -- a missing column is not worth failing startup over
	 */
	/**
	 * The one-word name of a state, for a place with no room to explain.
	 *
	 * Returns '' for STATE_UNKNOWN, and that is the design rather than a gap.
	 * The column answers "which of these have I translated?", where the useful
	 * values are the three that say something happened. "No translation here"
	 * is the resting state of almost every row, and printing it on all of them
	 * would turn a scannable column into a wall of the same phrase -- while
	 * also making a paper translated on another machine look identical to a
	 * book, which is the distinction the section exists to draw when you select
	 * the row and have space for a sentence.
	 *
	 * @param {String} state - one of the STATE_* constants
	 * @return {String}
	 */
	this.stateLabel = function (state) {
		if (!_stateLabels) {
			_stateLabels = {
				[this.STATE_RUNNING]: Zotero.getString('pharos-translate-state-translating'),
				[this.STATE_FAILED]: Zotero.getString('pharos-translate-state-failed'),
				[this.STATE_TRANSLATED]: Zotero.getString('pharos-translate-state-translated'),
			};
		}
		return _stateLabels[state] || '';
	};

	this.registerColumn = function () {
		if (_columnKey) {
			return _columnKey;
		}
		try {
			_columnKey = Zotero.ItemTreeManager.registerColumn({
				dataKey: COLUMN_KEY,
				label: Zotero.getString('pharos-translate-column-state'),
				pluginID: 'pharos@pharos.selab.top',
				enabledTreeIDs: ['main'],
				// Visible on a fresh profile, unlike a plugin's column. Zotero's
				// convention that custom columns are opt-in exists because a
				// plugin's column is a guest in somebody else's list; this one is
				// part of the product, and the web client has 状态 in its default
				// set. Without it a user moving web -> desktop loses "which of
				// these have I translated?" until they think to go looking in the
				// column picker.
				defaultIn: ['*'],
				flex: 0,
				width: 72,
				minWidth: 56,
				showInColumnPicker: true,
				zoteroPersist: ['width', 'hidden', 'sortDirection'],
				dataProvider: (item) => {
					// The empty string, not a dash or a word: this column is
					// meaningless for a note, a link attachment or a book, and
					// most rows in most libraries are one of those. A placeholder
					// on every one of them would make the column read as "nothing
					// here is translated" rather than "this does not apply".
					let state = this.getState(item);
					return state ? this.stateLabel(state.state) : '';
				},
			});
		}
		catch (e) {
			Zotero.logError(e);
			_columnKey = null;
		}
		return _columnKey;
	};

	/** Remove the column again. Only the tests need this. */
	this.unregisterColumn = function () {
		if (!_columnKey) {
			return;
		}
		try {
			Zotero.ItemTreeManager.unregisterColumn(_columnKey);
		}
		catch (e) {
			Zotero.logError(e);
		}
		_columnKey = null;
	};

	this.addStateListener = function (listener) {
		_stateListeners.add(listener);
	};

	this.removeStateListener = function (listener) {
		_stateListeners.delete(listener);
	};

	/** Test seam: forget every job this process has run. */
	this._clearJobs = function () {
		_jobs.clear();
	};

	/**
	 * Merge a patch into an attachment's job record and tell the listeners.
	 */
	function _setJob(itemID, patch) {
		_jobs.set(itemID, Object.assign({}, _jobs.get(itemID), patch));
		_notify(itemID);
	}

	/**
	 * Tell every listener that what is known about this attachment has changed.
	 *
	 * Separate from _setJob() because not everything worth redrawing a row for is
	 * a job: a page count arriving is the other case, and routing it through a
	 * fake job patch would put a field in _jobs that no run ever produced.
	 */
	function _notify(itemID) {
		for (let listener of _stateListeners) {
			try {
				listener(itemID);
			}
			catch (e) {
				// One badly-behaved section must not stop the others updating,
				// and must certainly not break the poll loop it is called from.
				Zotero.logError(e);
			}
		}
	}

	/**
	 * The parenthesised suffix _importResult() appended, or null.
	 */
	function _translationSuffix(item) {
		if (!Zotero.Pharos.Translate.canTranslate(item)) {
			return null;
		}
		let name = item.getField('title') || item.attachmentFilename || '';
		let match = /\(([^()]+)\)(?:\.pdf)?$/i.exec(name.trim());
		if (!match) {
			return null;
		}
		// Resolved once. This runs for every PDF child of every item the user
		// arrows past, and the locale cannot change without a restart.
		if (!_suffixes) {
			_suffixes = [
				Zotero.getString('pharos-translate-suffix-mono'),
				Zotero.getString('pharos-translate-suffix-dual'),
			];
		}
		return _suffixes.includes(match[1]) ? match[1] : null;
	}

	/**
	 * Related items that are PDFs we could have made or translated.
	 *
	 * The relation is symmetric by construction (_relate writes both halves), so
	 * this works from either end.
	 */
	function _relatedPDFs(item) {
		if (!item || !item.isAttachment()) {
			return [];
		}
		let out = [];
		for (let key of item.relatedItems) {
			let related = Zotero.Items.getByLibraryAndKey(item.libraryID, key);
			if (related && Zotero.Pharos.Translate.canTranslate(related)) {
				out.push(related);
			}
		}
		return out;
	}

	/**
	 * Queue items for translation and show the progress dialog.
	 *
	 * @param {Zotero.Item[]} items - attachments, or regular items whose best
	 *     PDF attachment will be used
	 * @param {String} mode - MODE_MONO or MODE_DUAL
	 */
	this.translateItems = async function (items, mode) {
		let attachments = [];
		for (let item of items) {
			if (this.canTranslate(item)) {
				attachments.push(item);
				continue;
			}
			// A regular item was selected: translate its best PDF, which is what
			// double-clicking the item would have opened.
			if (item.isRegularItem()) {
				let best = await item.getBestAttachment();
				if (best && this.canTranslate(best)) {
					attachments.push(best);
				}
			}
		}

		if (!attachments.length) {
			return;
		}

		_cancelled = false;
		for (let attachment of attachments) {
			_progressQueue.addRow(attachment);
			_queue.push({ attachment, mode });
			// Marked running on the way into the queue, not when the job
			// starts. The queue is processed one at a time, so the second of two
			// selected papers can sit here for minutes; reporting it as anything
			// other than in progress would invite a second click that queues it
			// twice.
			_setJob(attachment.id, {
				state: Zotero.Pharos.Translate.STATE_RUNNING,
				mode,
				phase: 'queued',
				message: Zotero.getString('pharos-translate-status-queued'),
				progress: 0,
				stage: '',
				stageIndex: 0,
				error: null,
			});
		}

		await _processQueue();
	};

	async function _processQueue() {
		if (_queueProcessing) {
			return;
		}
		_queueProcessing = true;
		try {
			while (_queue.length) {
				if (_cancelled) {
					break;
				}
				let { attachment, mode } = _queue.shift();
				let itemID = attachment.id;
				try {
					// The queue row still gets one line of prose; the job record
					// gets the same run in parts, because the item pane section
					// draws a bar and a stepper out of it rather than a sentence.
					let translated = await _translateOne(attachment, mode, (message, detail) => {
						_progressQueue.updateRow(
							itemID, Zotero.ProgressQueue.ROW_PROCESSING, message
						);
						_setJob(itemID, Object.assign({ message }, detail));
					});
					_progressQueue.updateRow(
						itemID,
						Zotero.ProgressQueue.ROW_SUCCEEDED,
						translated.getField('title') || translated.attachmentFilename
					);
					_setJob(itemID, {
						state: Zotero.Pharos.Translate.STATE_TRANSLATED,
						phase: null,
						message: '',
						progress: 100,
						stageIndex: Zotero.Pharos.Translate.STAGE_COUNT - 1,
						error: null,
					});
				}
				catch (e) {
					Zotero.logError(e);
					_progressQueue.updateRow(
						itemID, Zotero.ProgressQueue.ROW_FAILED, _describeError(e)
					);
					// The same truncated text the queue row shows, for the same
					// reason -- the section has a narrow column too, and an
					// untruncated traceback would push the retry button off the
					// bottom of the item pane.
					_setJob(itemID, {
						state: Zotero.Pharos.Translate.STATE_FAILED,
						phase: null,
						message: '',
						error: _describeError(e),
					});
				}
			}
		}
		finally {
			_queueProcessing = false;
		}
	}

	/**
	 * Upload one attachment, wait for the job, and import the result.
	 *
	 * @param {Zotero.Item} attachment
	 * @param {String} mode
	 * @param {Function} onProgress - called with a human-readable status string
	 *     and a structured detail object for the item pane section
	 * @return {Promise<Zotero.Item>} the attachment for the requested mode; the
	 *     other rendering, when the job produced one, is imported as well
	 */
	async function _translateOne(attachment, mode, onProgress) {
		let path = await attachment.getFilePathAsync();
		if (!path) {
			throw new Error(Zotero.getString('pharos-translate-error-missing-file'));
		}

		onProgress(Zotero.getString('pharos-translate-status-uploading'), {
			phase: 'uploading', progress: 0, stage: '', stageIndex: 0,
		});
		let paper = await _upload(path, attachment.attachmentFilename);

		onProgress(Zotero.getString('pharos-translate-status-queued'), {
			phase: 'queued', progress: 0, stage: '', stageIndex: 0,
		});
		let job = await Zotero.Pharos.API.request(
			'POST', `/api/papers/${paper.id}/translate`
		);

		job = await _awaitJob(job.id, onProgress);

		// The job produces both renderings; has_mono/has_dual say which actually
		// landed. Asking for one the job did not produce would 404 on a request
		// that otherwise looks fine.
		let kind = mode == Zotero.Pharos.Translate.MODE_DUAL ? 'dual' : 'mono';
		if ((kind == 'dual' && !job.has_dual) || (kind == 'mono' && !job.has_mono)) {
			throw new Error(Zotero.getString('pharos-translate-error-no-output'));
		}

		onProgress(Zotero.getString('pharos-translate-status-downloading'), {
			// The engine is done by now, so all three steps are behind us; the
			// remaining wait is a transfer, which the bar already covers.
			phase: 'downloading',
			progress: 100,
			stage: '',
			stageIndex: Zotero.Pharos.Translate.STAGE_COUNT - 1,
		});
		let translated = await _importResult(attachment, paper.id, kind);

		// Both renderings come out of the same job, and the mode was chosen from
		// a context menu before the user had read a word of the paper. Keeping
		// only the chosen one means changing your mind costs another full run --
		// minutes of engine time and API budget already spent, thrown away over
		// a menu click. So the other one is imported too when the job has it,
		// which is what the web client does by choosing at read time instead.
		let other = kind == 'dual' ? 'mono' : 'dual';
		if (other == 'dual' ? job.has_dual : job.has_mono) {
			try {
				await _importResult(attachment, paper.id, other);
			}
			catch (e) {
				// The rendering that was asked for is already attached. Failing
				// the whole translation over the spare one would report failure
				// for a job that succeeded.
				Zotero.logError(e);
			}
		}

		// The requested one, because it is what the progress row names and what
		// the user is waiting for.
		return translated;
	}

	async function _upload(path, filename) {
		let bytes = await IOUtils.read(path);
		let form = new FormData();
		// Blob rather than the path: FormData needs the bytes, and this is the
		// one place the whole file is held in memory. A paper-sized PDF is a few
		// MB, which is the same order as what the reader already loads.
		form.append('file', new Blob([bytes], { type: 'application/pdf' }), filename);
		return Zotero.Pharos.API.request('POST', '/api/papers', { body: form });
	}

	/**
	 * Poll until the job finishes.
	 *
	 * @return {Promise<Object>} the finished job
	 */
	async function _awaitJob(jobID, onProgress) {
		let deadline = Date.now() + POLL_TIMEOUT;
		while (true) {
			if (_cancelled) {
				throw new Error(Zotero.getString('pharos-translate-error-cancelled'));
			}
			if (Date.now() > deadline) {
				throw new Error(Zotero.getString('pharos-translate-error-timeout'));
			}

			await Zotero.Promise.delay(POLL_INTERVAL);
			let job = await Zotero.Pharos.API.request('GET', `/api/jobs/${jobID}`);

			if (job.status == 'done') {
				return job;
			}
			if (job.status == 'error') {
				throw new Error(job.error || Zotero.getString('pharos-translate-error-failed'));
			}
			// stage is the engine's own label for what it is doing right now
			// (parsing, translating, rebuilding); showing it beats a bare
			// percentage because a long stage otherwise looks like a hang.
			// Fluent's own formatter, NOT Zotero.getString(id, params): handed a
			// params argument, getString routes to the .properties bundle, where
			// no pharos-* id exists -- it throws in en-US and returns the bare id
			// in zh-CN. Here that threw from inside the poll loop, so an en-US
			// user lost the translation they were already paying for, at the
			// first progress tick, with the job still running on the server.
			//
			// The raw stage travels on in `detail` as well, where the item pane
			// section normalises it to a three-step stepper (see stageIndex) and
			// keeps this string as the stepper's tooltip. Both renderings are
			// fed from here so that neither can drift from the other.
			onProgress(Zotero.ftl.formatValueSync('pharos-translate-status-running', {
				stage: job.stage || '',
				percent: Math.round(job.progress || 0),
			}), {
				phase: 'running',
				progress: Math.round(job.progress || 0),
				stage: job.stage || '',
				stageIndex: Zotero.Pharos.Translate.stageIndex(job.stage, job.progress),
			});
		}
	}

	/**
	 * Download one rendering, attach it beside the original, and relate the two.
	 */
	async function _importResult(attachment, paperID, kind) {
		let buffer = await Zotero.Pharos.API.request(
			'GET', `/api/papers/${paperID}/pdf/${kind}`,
			{ responseType: 'arraybuffer', timeout: 300000 }
		);

		let baseName = _baseName(attachment.attachmentFilename || 'paper.pdf');
		let suffix = Zotero.getString(
			kind == 'dual'
				? 'pharos-translate-suffix-dual'
				: 'pharos-translate-suffix-mono'
		);
		let filename = `${baseName} (${suffix}).pdf`;

		// Written to the temp directory first because importFromFile copies from
		// a path into the storage directory; it has no bytes-in entry point.
		let tmpPath = PathUtils.join(Zotero.getTempDirectory().path, filename);
		await IOUtils.write(tmpPath, new Uint8Array(buffer));

		let options = {
			file: tmpPath,
			libraryID: attachment.libraryID,
			title: filename,
			contentType: 'application/pdf',
		};
		if (attachment.parentItemID) {
			// Attach to the same parent as the source PDF, so the translation
			// sits beside the original rather than becoming a loose item.
			options.parentItemID = attachment.parentItemID;
		}
		else {
			// A top-level PDF has no parent to hang the translation off, and an
			// attachment cannot parent another attachment. Left at that, the
			// translation lands in the library root with no collection at all --
			// findable only by scrolling the whole library, even though the file
			// it was made from is filed. Following the original into its
			// collections is the closest thing to adjacency that exists here;
			// the relation below is what actually links the two. Set instead of
			// parentItemID, never alongside it: importFromFile throws when given
			// both.
			options.collections = attachment.getCollections();
		}

		let translation;
		try {
			translation = await Zotero.Attachments.importFromFile(options);
		}
		finally {
			// The import copied the file; leaving the original behind would grow
			// the temp directory by a PDF per translation.
			try {
				await IOUtils.remove(tmpPath);
			}
			catch (e) {
				Zotero.logError(e);
			}
		}

		await _relate(attachment, translation);
		return translation;
	}

	/**
	 * Relate a translation and the PDF it was made from, both ways.
	 *
	 * Zotero's relations are not symmetric on their own: addRelatedItem() writes
	 * a dc:relation on the item it is called on and nothing on the other, so
	 * relating one side gives a link that exists from the translation and not
	 * from the paper. Everywhere Zotero relates two items it does both halves
	 * (zoteroPane.js duplicateSelectedItem, relatedBox.js), and this follows
	 * that rather than inventing a predicate of its own -- "Related" in the item
	 * pane is where a user would look, and only dc:relation puts it there.
	 *
	 * Deliberately outside any transaction and after the import has committed: a
	 * translation that is attached but unrelated is still a usable translation,
	 * and taking the import down over a failed link would trade the whole job
	 * for the cross-reference.
	 *
	 * skipDateModifiedUpdate because relating is bookkeeping, not an edit to the
	 * paper -- same call Zotero makes when it relates items itself.
	 */
	async function _relate(original, translation) {
		try {
			if (translation.addRelatedItem(original)) {
				await translation.saveTx({ skipDateModifiedUpdate: true });
			}
			if (original.addRelatedItem(translation)) {
				await original.saveTx({ skipDateModifiedUpdate: true });
			}
		}
		catch (e) {
			Zotero.logError(e);
		}
	}

	function _baseName(filename) {
		return filename.replace(/\.[^.]+$/, '');
	}

	/**
	 * Turn a failure into something the progress dialog can show.
	 *
	 * The 409 case is called out specifically: the backend returns it when the
	 * account has whole-document translation switched off, and it deliberately
	 * does not use 404 for that -- a client that reported "not found" would send
	 * the user looking for a problem with the paper instead of a setting they
	 * can change.
	 *
	 * Everything else is whatever the engine said, truncated -- see ERROR_MAX.
	 */
	function _describeError(e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			return Zotero.getString('pharos-error-signed-out');
		}
		if (e.status == 409) {
			return Zotero.getString('pharos-translate-error-disabled');
		}
		let text = (e.message || String(e) || '').trim();
		if (!text) {
			// A failure with nothing to say still has to say something: an empty
			// cell reads as a row that is still running.
			return Zotero.getString('pharos-translate-error-failed');
		}
		return text.length > ERROR_MAX ? text.slice(0, ERROR_MAX) + '…' : text;
	}

	Zotero.Reader.registerEventListener('renderToolbar', this._renderToolbar);
};
