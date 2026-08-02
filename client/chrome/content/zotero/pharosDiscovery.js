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

// The 文献探索 window.
//
// Three things this window is built around, all of them corrections:
//
// 1. A result's analysis has a PROVENANCE, and it is the most important thing
//    on the card. A fresh search comes back analysis_mode "rules": sentences
//    cut out of the English abstract, with summary_zh empty. Only the per-card
//    button sends a paper to a model. Every card therefore carries a mode chip,
//    the model that read it when one did, and -- for rules -- a visible
//    sentence saying no model was called and no full text was read. The key
//    idea block is filled from Discovery.trick() and from nothing else; the
//    abstract has its own labelled block underneath it.
//
// 2. A run is a RECORD, not a transient. A search where every provider died is
//    saved with status "error" and the per-provider reasons, and reopens from
//    the rail like any other. status "running" means a request that died
//    mid-flight, not one in progress: POST /api/discovery/search is synchronous
//    and commits only on success. Nothing here polls, and nothing animates.
//
// 3. Filing is a BATCH. Twelve papers used to mean twelve modal pickers, and
//    the picker dropped the archived suffix, so a paper could be filed into an
//    archived project with nothing on screen to say so. The selection dock
//    files the whole selection in one call, against a <select> that spells the
//    archived state out.
//
// Its own stylesheet, NOT pharosDaily.css. That file styles Research Projects,
// which builds .pharos-daily-* rows of its own; no pharos-daily-* name appears
// anywhere below.
var Zotero_Pharos_Discovery = new function () {
	/** How much of a server error message is worth showing before it stops
	 *  being a sentence and starts being a stack trace. */
	const ERR_MAX = 300;

	let _resolveInit;

	/** Settles once init() has finished. See pharosDaily.js for why this is
	 *  created here rather than assigned from the onload attribute. */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});


	/* ----------------------------------------------------------------- state */

	/** Every project this account has, archived ones included. Their `sources`
	 *  arrive with them, which is what the 已在当前项目 chip is read from. */
	let _projects = [];
	let _projectsFailed = false;

	/** The current project: what a search is linked to, what filing targets,
	 *  and what the 已在当前项目 chip means. One notion, two <select>s, so that
	 *  the chip can never refer to a project the filing control is not aimed at. */
	let _projectID = '';

	/** Past runs, newest first, each already carrying its full `results`. */
	let _searches = [];
	let _historyState = 'loading';

	/** The run on screen. Null before the first search of the session. */
	let _search = null;

	/** result ids, ticked. Kept as a Set so that re-selecting exactly the
	 *  failures of a partial filing is a one-liner. */
	let _selected = new Set();

	/** result ids saved to the library this session, so that a card re-rendered
	 *  after an analyze does not forget it is already in the library. */
	let _saved = new Set();

	let _searching = false;
	let _filing = false;

	let _el = {};


	/* --------------------------------------------------------------- strings */

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

	/** One of { id, args } as returned by the module's classifiers. */
	function _msg(problem) {
		return problem.args ? _fmt(problem.id, problem.args) : _str(problem.id);
	}

	/**
	 * What to show the user as the reason a request failed.
	 *
	 * The server's own `detail` wherever there is one -- Zotero.Pharos.API
	 * unwraps it -- because rewriting it would mean guessing which failure
	 * occurred. A connection that never completed is the exception: its message
	 * names the URL and "status code 0", which nobody can act on.
	 */
	function _reason(e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			return _str('pharos-error-signed-out-detail');
		}
		if (e instanceof Zotero.HTTP.TimeoutException || !e || !e.message || e.status === 0) {
			return _str('pharos-error-unreachable');
		}
		return String(e.message).slice(0, ERR_MAX);
	}

	/**
	 * The full stop that ends the filing sentence.
	 *
	 * pharos-discovery-file-skipped and -file-failed are fragments appended only
	 * when their count is non-zero, so the sentence has no fixed last word and
	 * cannot carry its own terminator. A translation unit consisting of one
	 * punctuation mark would be worse than deriving it, and the split is by
	 * writing system rather than by locale so that a future ja-JP gets it right
	 * without another edit here.
	 */
	function _stop() {
		return /^(zh|ja)\b/.test(String(Zotero.locale || '')) ? '。' : '.';
	}

	let _dtf = null;

	/**
	 * A timestamp, formatted for reading.
	 *
	 * Passed into Fluent as a plain string, deliberately: the two ids that take
	 * it are declared with a String argument, not a Fluent DATETIME(), so the
	 * window owns the format and the rail and the run header agree.
	 */
	function _time(iso) {
		if (!iso) {
			return '';
		}
		let date = new Date(iso);
		if (isNaN(date.getTime())) {
			return String(iso);
		}
		try {
			if (!_dtf) {
				_dtf = new Intl.DateTimeFormat(Zotero.locale, {
					dateStyle: 'medium',
					timeStyle: 'short',
				});
			}
			return _dtf.format(date);
		}
		catch (e) {
			Zotero.logError(e);
			return date.toLocaleString();
		}
	}


	/* ------------------------------------------------------------ DOM helpers */

	// document.createElement() produces an HTML element in this document even
	// though the default namespace is XUL; document.createXULElement() is what
	// makes a XUL one. Nothing in this module is XUL: a XUL button renders its
	// `label` attribute and ignores textContent, which is a blank button for
	// every string in the table.
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
		let el = _make('button', className, text);
		// The composer and the new-project panel are real <form>s, and a button
		// inside a form defaults to type=submit -- which would fire the click
		// handler and the submit handler and run the action twice.
		el.type = 'button';
		return el;
	}

	/** Text, or nothing at all. An empty node still takes its margin. */
	function _line(el, text, isError) {
		el.textContent = text || '';
		el.hidden = !text;
		if (isError !== undefined) {
			el.classList.toggle('is-err', !!isError);
		}
	}


	/* --------------------------------------------------------------- derived */

	function _project(id) {
		return _projects.find(p => p.id == (id === undefined ? _projectID : id)) || null;
	}

	function _projectName(project) {
		if (!project) {
			return '';
		}
		return project.status == 'archived'
			? _fmt('pharos-discovery-project-archived', { name: project.name })
			: project.name;
	}

	/** result ids already filed into the current project. Read from the
	 *  project's own `sources`, which GET /api/projects returns in full. */
	function _filed() {
		let project = _project();
		if (!project || !project.sources) {
			return new Set();
		}
		return new Set(project.sources.map(s => s.result_id));
	}

	function _results() {
		return (_search && _search.results) || [];
	}

	function _sources() {
		return Zotero.Pharos.Discovery.SOURCES.filter((id) => {
			let box = _el['source-' + id];
			return box && box.checked;
		});
	}

	function _form() {
		return {
			query: _el.query.value,
			sources: _sources(),
			limit: Number(_el.limit.value),
		};
	}


	/* ------------------------------------------------------------------ init */

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		for (let [key, id] of Object.entries({
			railCount: 'pharos-ds-rail-count',
			history: 'pharos-ds-history',
			railNote: 'pharos-ds-rail-note',
			railNoteText: 'pharos-ds-rail-note-text',
			railRetry: 'pharos-ds-rail-retry',
			contextName: 'pharos-ds-context-name',
			composer: 'pharos-ds-composer',
			query: 'pharos-discovery-query',
			'source-arxiv': 'pharos-ds-source-arxiv',
			'source-openalex': 'pharos-ds-source-openalex',
			project: 'pharos-ds-project',
			limit: 'pharos-ds-limit',
			run: 'pharos-discovery-search',
			hint: 'pharos-ds-hint',
			formError: 'pharos-ds-form-error',
			notice: 'pharos-ds-notice',
			runBox: 'pharos-ds-run',
			status: 'pharos-ds-status',
			runQuery: 'pharos-ds-run-query',
			runMeta: 'pharos-ds-run-meta',
			reuse: 'pharos-ds-reuse',
			runWarn: 'pharos-ds-runwarn',
			errors: 'pharos-ds-errors',
			selbar: 'pharos-ds-selbar',
			selall: 'pharos-ds-selall-box',
			selcount: 'pharos-ds-selcount',
			fileProject: 'pharos-ds-file-project',
			file: 'pharos-ds-file',
			newProject: 'pharos-ds-new-project',
			newBox: 'pharos-ds-newproject',
			newName: 'pharos-ds-newproject-name',
			newQuestion: 'pharos-ds-newproject-question',
			newCreate: 'pharos-ds-newproject-create',
			list: 'pharos-ds-list',
			empty: 'pharos-ds-empty',
			emptyTitle: 'pharos-ds-empty-title',
			emptyDesc: 'pharos-ds-empty-desc',
		})) {
			_el[key] = document.getElementById(id);
		}

		// Set before anything can return early: a button whose label is written
		// only on the happy path is a blank button in the signed-out state.
		_el.run.textContent = _str('pharos-discovery-search');
		_el.file.textContent = _str('pharos-discovery-add-to-project');
		_el.newCreate.textContent = _fmt('pharos-discovery-new-project-create', { count: 0 });
		_line(_el.formError, '');
		_line(_el.notice, '');
		_el.newBox.hidden = true;

		_wire();
		_renderProjects();
		_renderRun();

		if (!Zotero.Pharos.API.hasCredentials()) {
			_line(_el.hint, _str('pharos-error-signed-out-detail'), true);
			_el.run.disabled = true;
			_el.query.disabled = true;
			_el.runBox.hidden = true;
			_el.selbar.hidden = true;
			_historyState = 'signedout';
			_renderHistory();
			return;
		}

		_line(_el.hint, _str('pharos-discovery-hint'), false);
		_renderHistory();

		// Independent of each other, and neither blocks typing: the composer is
		// usable before either lands.
		await Promise.all([_loadProjects(), _loadHistory()]);
		_el.query.focus();
	};

	function _wire() {
		_el.composer.addEventListener('submit', (event) => {
			event.preventDefault();
			Zotero_Pharos_Discovery.search();
		});
		_el.run.addEventListener('click', () => Zotero_Pharos_Discovery.search());
		_el.query.addEventListener('keydown', (event) => {
			// Enter runs; Shift+Enter is a newline, because the field is three
			// rows tall and a research question can want one.
			if (event.key == 'Enter' && !event.shiftKey) {
				event.preventDefault();
				Zotero_Pharos_Discovery.search();
			}
		});
		_el.query.addEventListener('input', _clearFormError);
		_el.limit.addEventListener('input', _clearFormError);

		for (let id of Zotero.Pharos.Discovery.SOURCES) {
			let box = _el['source-' + id];
			box.checked = true;
			box.addEventListener('change', () => {
				box.closest('.pharos-ds-source').classList.toggle('is-on', box.checked);
				_clearFormError();
			});
		}

		_el.project.addEventListener('change', () => _setProject(_el.project.value));
		_el.fileProject.addEventListener('change', () => _setProject(_el.fileProject.value));

		_el.railRetry.addEventListener('click', () => _loadHistory());
		_el.reuse.addEventListener('click', _reuse);

		_el.selall.addEventListener('change', () => {
			if (_el.selall.checked) {
				for (let result of _results()) {
					_selected.add(result.id);
				}
			}
			else {
				_selected.clear();
			}
			_syncSelection();
		});

		_el.file.addEventListener('click', _fileSelection);
		_el.newProject.addEventListener('click', () => {
			_el.newBox.hidden = !_el.newBox.hidden;
			if (!_el.newBox.hidden) {
				_el.newName.focus();
			}
		});
		_el.newBox.addEventListener('submit', (event) => {
			event.preventDefault();
			_createProject();
		});
		_el.newCreate.addEventListener('click', _createProject);
	}

	function _clearFormError() {
		_line(_el.formError, '');
	}


	/* -------------------------------------------------------------- projects */

	async function _loadProjects() {
		try {
			// Archived ones included, on purpose: a project that is archived can
			// still be filed into, and hiding it would turn the archived-suffix
			// fix into a disappearing act instead.
			_projects = await Zotero.Pharos.Projects.list(true);
			_projectsFailed = false;
		}
		catch (e) {
			Zotero.logError(e);
			_projects = [];
			_projectsFailed = true;
			_line(_el.notice, _str('pharos-discovery-projects-failed'), true);
		}
		_renderProjects();
	}

	function _renderProjects() {
		if (!_projects.some(p => p.id == _projectID)) {
			_projectID = '';
		}
		_fillProjectSelect(_el.project, 'pharos-discovery-project-none');
		_fillProjectSelect(_el.fileProject, 'pharos-discovery-pick-project');
		_el.fileProject.title = _str('pharos-discovery-pick-project');

		let project = _project();
		_el.contextName.textContent = project
			? _projectName(project)
			: _str('pharos-discovery-project-none');

		_renderList();
	}

	/**
	 * The project picker.
	 *
	 * The archived suffix is the point. The window used to file through
	 * Services.prompt.select over `projects.map(p => p.name)`, which dropped it
	 * -- so a paper could be filed into an archived project with nothing on
	 * screen to say so.
	 */
	function _fillProjectSelect(select, placeholderID) {
		select.replaceChildren();
		let none = _make('option', null, _str(placeholderID));
		none.value = '';
		select.append(none);
		for (let project of _projects) {
			let option = _make('option', null, _projectName(project));
			option.value = project.id;
			select.append(option);
		}
		select.value = _projectID;
	}

	function _setProject(id) {
		_projectID = id || '';
		_el.project.value = _projectID;
		_el.fileProject.value = _projectID;
		let project = _project();
		_el.contextName.textContent = project
			? _projectName(project)
			: _str('pharos-discovery-project-none');
		// The 已在当前项目 chip is per-project, so changing the project changes
		// every card.
		_renderList();
	}


	/* --------------------------------------------------------------- history */

	async function _loadHistory() {
		_historyState = 'loading';
		_renderHistory();
		try {
			_searches = await Zotero.Pharos.Discovery.getSearches();
			_historyState = 'ready';
		}
		catch (e) {
			Zotero.logError(e);
			_searches = [];
			_historyState = 'error';
		}
		_renderHistory();
	}

	function _renderHistory() {
		_el.railCount.textContent = _searches.length ? String(_searches.length) : '';
		_el.history.replaceChildren();

		for (let search of _searches) {
			_el.history.append(_historyItem(search));
		}

		let note = '';
		let isError = false;
		if (_historyState == 'loading') {
			note = _str('pharos-discovery-history-loading');
		}
		else if (_historyState == 'error') {
			note = _str('pharos-discovery-history-failed');
			isError = true;
		}
		else if (_historyState == 'signedout') {
			note = _str('pharos-error-signed-out-detail');
			isError = true;
		}
		else if (!_searches.length) {
			note = _str('pharos-discovery-history-empty');
		}
		_line(_el.railNoteText, note, false);
		_el.railNote.hidden = !note;
		_el.railNote.classList.toggle('is-err', isError);
		_el.railRetry.hidden = _historyState != 'error';
	}

	function _historyItem(search) {
		let item = _btn('pharos-ds-hitem');
		item.dataset.searchId = search.id;
		if (_search && _search.id == search.id) {
			item.classList.add('is-active');
		}

		let top = _div('pharos-ds-hitem-top');
		let dot = _span('pharos-ds-dot');
		// statusStringID fails closed, so an unrecognised status paints as a
		// failure rather than as a success.
		dot.classList.add('is-' + Zotero.Pharos.Discovery.statusStringID(search.status)
			.replace('pharos-discovery-status-', ''));
		dot.title = _str(Zotero.Pharos.Discovery.statusStringID(search.status));
		top.append(dot, _span('pharos-ds-hitem-query', search.query));
		item.append(top);

		item.append(_div('pharos-ds-hitem-meta', _fmt('pharos-discovery-history-meta', {
			time: _time(search.created_at),
			count: search.result_count,
		})));
		item.append(_div('pharos-ds-hitem-sources',
			Zotero.Pharos.Discovery.sourceLabel(search.sources)));

		item.addEventListener('click', () => _open(search.id));
		return item;
	}

	/**
	 * Reopen a past run.
	 *
	 * Straight from the cached row: GET /api/discovery/searches serialises every
	 * run through the same search_out() as the detail endpoint, so the rail
	 * already holds every result. The detail call is the fallback for a row that
	 * somehow arrived without them, which is also the only place getSearch() is
	 * needed at all.
	 */
	async function _open(searchID) {
		let cached = _searches.find(s => s.id == searchID);
		if (cached && Array.isArray(cached.results)) {
			_show(cached);
			return;
		}
		try {
			_show(await Zotero.Pharos.Discovery.getSearch(searchID));
		}
		catch (e) {
			Zotero.logError(e);
			_line(_el.notice, _fmt('pharos-discovery-open-failed', { error: _reason(e) }), true);
		}
	}

	function _show(search) {
		_search = search;
		_selected.clear();
		_el.newBox.hidden = true;
		_renderHistory();
		_renderRun();
	}


	/* ---------------------------------------------------------------- search */

	this.search = async function () {
		if (_searching) {
			return;
		}
		let form = _form();
		let problem = Zotero.Pharos.Discovery.searchProblem(form);
		if (problem) {
			_line(_el.formError, _msg(problem), true);
			return;
		}
		_clearFormError();

		_searching = true;
		_el.run.disabled = true;
		_el.run.textContent = _str('pharos-discovery-searching');
		_line(_el.notice, '');
		try {
			let search = await Zotero.Pharos.Discovery.search(form.query, {
				sources: form.sources,
				limit: form.limit,
				projectID: _projectID,
			});
			_show(search);
			_noticeForRun(search);
			// A run that failed outright is still a saved record and belongs in
			// the rail, so the refresh is not conditional on the status.
			await _loadHistory();
		}
		catch (e) {
			Zotero.logError(e);
			_line(_el.notice, _fmt('pharos-discovery-search-failed', { error: _reason(e) }), true);
		}
		finally {
			_searching = false;
			_el.run.disabled = false;
			_el.run.textContent = _str('pharos-discovery-search');
		}
	};

	function _noticeForRun(search) {
		if (search.status == 'complete') {
			_line(_el.notice,
				_fmt('pharos-discovery-notice-complete', { count: search.result_count }), false);
		}
		else if (search.status == 'partial') {
			_line(_el.notice,
				_fmt('pharos-discovery-notice-partial', { count: search.result_count }), false);
		}
		else if (search.status == 'error') {
			_line(_el.notice, _str('pharos-discovery-notice-error'), true);
		}
		else {
			_line(_el.notice, '');
		}
	}

	/** Put a past run's conditions back in the form. Running again creates a new
	 *  record; it never overwrites the one being reused. */
	function _reuse() {
		if (!_search) {
			return;
		}
		_el.query.value = _search.query;
		for (let id of Zotero.Pharos.Discovery.SOURCES) {
			let box = _el['source-' + id];
			box.checked = (_search.sources || []).includes(id);
			box.closest('.pharos-ds-source').classList.toggle('is-on', box.checked);
		}
		// The requested limit is deliberately left alone. LiteratureSearchOut
		// does not carry it, and result_count is not a stand-in: a run that
		// asked for 20 and got 3 would come back with the box reading 3, which
		// silently narrows the next search.
		_setProject(_search.project_id && _projects.some(p => p.id == _search.project_id)
			? _search.project_id
			: '');
		_clearFormError();
		_line(_el.notice, _str('pharos-discovery-reused'), false);
		_el.query.focus();
	}


	/* ------------------------------------------------------------------- run */

	function _renderRun() {
		if (!_search) {
			_el.runBox.hidden = true;
			_el.empty.hidden = false;
			_el.empty.classList.remove('is-small');
			_el.emptyTitle.textContent = _str('pharos-discovery-first-title');
			_el.emptyDesc.textContent = _str('pharos-discovery-first-desc');
			return;
		}

		_el.runBox.hidden = false;

		let statusID = Zotero.Pharos.Discovery.statusStringID(_search.status);
		_el.status.textContent = _str(statusID);
		_el.status.className = 'pharos-ds-status is-'
			+ statusID.replace('pharos-discovery-status-', '');
		_el.runQuery.textContent = _search.query;
		_el.runMeta.textContent = _fmt('pharos-discovery-run-meta', {
			count: _search.result_count,
			// The run header answers "what did I ask for", so this is the
			// search's own source list, not any card's.
			sources: Zotero.Pharos.Discovery.sourceLabel(_search.sources),
			time: _time(_search.created_at),
		});

		_el.runWarn.hidden = _search.status != 'running';
		_renderErrors();
		_renderList();
	}

	/**
	 * Per-provider failures, one row each.
	 *
	 * Not joined into a single sentence with separators: two providers that
	 * failed for different reasons are two facts, and the name has to be
	 * attached to its own reason for either to be actionable.
	 */
	function _renderErrors() {
		let entries = Object.entries((_search && _search.errors) || {});
		_el.errors.replaceChildren();
		_el.errors.hidden = !entries.length;
		if (!entries.length) {
			return;
		}
		_el.errors.append(_make('strong', 'pharos-ds-errors-head', _str(
			_search.status == 'partial'
				? 'pharos-discovery-errors-partial'
				: 'pharos-discovery-errors-all'
		)));
		for (let [source, message] of entries) {
			let row = _make('p', 'pharos-ds-error-row');
			row.append(
				_span('pharos-ds-error-source', Zotero.Pharos.Discovery.sourceName(source)),
				_span('pharos-ds-error-msg', String(message).slice(0, ERR_MAX))
			);
			_el.errors.append(row);
		}
	}

	function _renderList() {
		if (!_search) {
			return;
		}
		let results = _results();
		_el.list.replaceChildren();
		for (let result of results) {
			_el.list.append(_card(result));
		}

		_el.selbar.hidden = !results.length;
		if (!results.length) {
			_el.newBox.hidden = true;
			_el.empty.hidden = false;
			_el.empty.classList.add('is-small');
			_el.emptyTitle.textContent = _str(_search.status == 'error'
				? 'pharos-discovery-error'
				: 'pharos-discovery-empty');
			_el.emptyDesc.textContent = _str('pharos-discovery-empty-hint');
		}
		else {
			_el.empty.hidden = true;
		}
		_syncSelection();
	}


	/* ----------------------------------------------------------------- cards */

	function _card(result) {
		let card = _make('article', 'pharos-ds-card');
		card.dataset.resultId = result.id;
		if (_selected.has(result.id)) {
			card.classList.add('is-selected');
		}

		let select = _div('pharos-ds-card-select');
		let check = _make('label', 'pharos-ds-check');
		let box = _make('input');
		box.type = 'checkbox';
		box.checked = _selected.has(result.id);
		box.addEventListener('change', () => {
			if (box.checked) {
				_selected.add(result.id);
			}
			else {
				_selected.delete(result.id);
			}
			// .is-selected, never the XUL-reserved `selected` attribute, which
			// applies layout of its own.
			card.classList.toggle('is-selected', box.checked);
			_syncSelection();
		});
		check.append(box, _span());
		let rank = _span('pharos-ds-rank', String(result.rank).padStart(2, '0'));
		rank.title = _str('pharos-discovery-rank-tooltip');
		select.append(check, rank);
		card.append(select);

		let body = _div('pharos-ds-card-body');
		body.append(_cardTitle(result), _cardMeta(result), _cardTrick(result));

		let warning = Zotero.Pharos.Discovery.analysisWarning(result);
		if (warning) {
			let warn = _div('pharos-ds-warn');
			// Visible localized text, with the server's raw English sentence in
			// the title of the same block. A tooltip-only treatment hides the one
			// fact that decides how much the card can be trusted.
			warn.title = warning;
			warn.append(_span('pharos-ds-warn-ic'),
				_span('pharos-ds-warn-text', _str('pharos-discovery-mode-rules-detail')));
			body.append(warn);
		}

		let highlights = _cardHighlights(result);
		if (highlights) {
			body.append(highlights);
		}

		let abstract = String(result.abstract || '').trim();
		if (abstract) {
			// The ONLY place the abstract appears, and always under its own
			// heading, so it can be read without being mistaken for analysis.
			let abs = _div('pharos-ds-abs');
			abs.append(_span('pharos-ds-abs-k', _str('pharos-discovery-abstract-label')),
				_make('p', 'pharos-ds-abs-v', abstract));
			body.append(abs);
		}

		let error = _div('pharos-ds-card-err is-err');
		error.hidden = true;
		body.append(error, _cardFoot(result, card, error));

		card.append(body);
		return card;
	}

	function _cardTitle(result) {
		let row = _div('pharos-ds-card-titlerow');
		if (result.url) {
			// A button rather than an <a>: Zotero.launchURL hands the link to the
			// system browser, which is the deliberate desktop behaviour. Styled
			// as a link because that is what it acts like.
			let link = _btn('pharos-ds-card-title is-link', result.title);
			link.title = _str('pharos-discovery-open');
			link.addEventListener('click', () => Zotero.launchURL(result.url));
			row.append(link);
		}
		else {
			row.append(_span('pharos-ds-card-title', result.title));
		}
		return row;
	}

	function _cardMeta(result) {
		let meta = _div('pharos-ds-card-meta');
		let bits = [];
		// Authors first, and kept: the web client omits them entirely, and on a
		// desktop row they are what identifies a paper you already know.
		if (result.authors && result.authors.length) {
			bits.push(result.authors.slice(0, 4).join(', ')
				+ (result.authors.length > 4 ? ' et al.' : ''));
		}
		if (result.year) {
			bits.push(String(result.year));
		}
		if (result.venue) {
			bits.push(result.venue);
		}
		// A card answers "who corroborated this paper", which is not the same
		// list as the run's requested sources.
		bits.push(Zotero.Pharos.Discovery.sourceLabel(result.sources));
		// arXiv never sets one, so an arXiv-only result has null here and the
		// segment is dropped rather than printed as 0.
		if (typeof result.citation_count == 'number') {
			bits.push(_fmt('pharos-discovery-citations', { count: result.citation_count }));
		}
		for (let bit of bits) {
			meta.append(_span('pharos-ds-meta-item', bit));
		}
		return meta;
	}

	/**
	 * The key idea.
	 *
	 * Filled from Discovery.trick() and from nothing else. `summary_zh ||
	 * abstract`, which is what this was, renders the raw English abstract in the
	 * box a Chinese AI summary occupies for every rules result -- which is every
	 * result of a fresh search.
	 */
	function _cardTrick(result) {
		let trick = Zotero.Pharos.Discovery.trick(result);
		let block = _div('pharos-ds-trick is-' + trick.state);
		if (trick.state == 'extracted') {
			block.title = _str('pharos-discovery-trick-extracted-tooltip');
		}
		block.append(_span('pharos-ds-trick-k', _str('pharos-discovery-trick-label')),
			_make('p', 'pharos-ds-trick-v', trick.text));
		return block;
	}

	/**
	 * contribution / method / results / limitations.
	 *
	 * rule_summary() fills all four from the abstract, and the web client shows
	 * none of them. core_trick is not repeated here; it is the block above.
	 */
	function _cardHighlights(result) {
		const KEYS = ['contribution', 'method', 'results', 'limitations'];
		let rows = KEYS
			.map(key => ({ key, text: String(result[key] || '').trim() }))
			.filter(row => row.text);
		if (!rows.length) {
			return null;
		}
		// A deep read overwrites contribution, method and results but DELIBERATELY
		// leaves limitations at what rule_summary() produced -- a cue-matched
		// sentence copied out of the English abstract, which the model never saw
		// (backend/pharos/services/projects.py:393-403). By then the card carries
		// the accent "AI reading" chip and the rules warning has gone, so an
		// unlabelled limitations row reads as the named model's assessment of the
		// paper's weaknesses. It is also the only English row among four, which
		// reads as a translation gap rather than the provenance gap it is.
		let staleLimitations = String(result.analysis_mode || '').toLowerCase() == 'llm';

		let box = _div('pharos-ds-hls');
		for (let row of rows) {
			let hl = _div('pharos-ds-hl');
			let ruleRow = staleLimitations && row.key == 'limitations';
			if (ruleRow) {
				hl.classList.add('is-rules');
				hl.setAttribute('title', _str('pharos-discovery-limitations-rules-hint'));
			}
			let key = _span('pharos-ds-hl-k', _str('pharos-discovery-section-' + row.key));
			if (ruleRow) {
				key.append(_span('pharos-ds-hl-src',
					_str('pharos-discovery-limitations-rules')));
			}
			hl.append(key, _span('pharos-ds-hl-v', row.text));
			box.append(hl);
		}
		return box;
	}

	function _cardFoot(result, card, error) {
		let foot = _div('pharos-ds-card-foot');

		let state = _div('pharos-ds-card-state');
		// Always present, never conditional: a chip that only appears sometimes
		// is what makes rules and llm indistinguishable at a glance.
		let mode = _span('pharos-ds-mode',
			_str(Zotero.Pharos.Discovery.modeStringID(result)));
		if (!Zotero.Pharos.Discovery.isRules(result)) {
			mode.classList.add('is-ai');
		}
		state.append(mode);
		if (result.analysis_mode == 'llm') {
			state.append(_span('pharos-ds-model', result.analysis_model
				? _fmt('pharos-discovery-model', { model: result.analysis_model })
				: _str('pharos-discovery-model-unknown')));
		}
		if (_filed().has(result.id)) {
			state.append(_span('pharos-ds-filed', _str('pharos-discovery-filed')));
		}
		foot.append(state);

		let actions = _div('pharos-ds-card-actions');

		let analyze = _btn('pharos-ds-abtn is-accent', _str(
			Zotero.Pharos.Discovery.isRules(result)
				? 'pharos-discovery-analyze'
				: 'pharos-discovery-reanalyze'
		));
		analyze.addEventListener('click', () => _analyze(result, card, error, analyze));
		actions.append(analyze);

		let save = _btn('pharos-ds-abtn', _str(
			_saved.has(result.id) ? 'pharos-discovery-saved' : 'pharos-discovery-save'
		));
		save.disabled = _saved.has(result.id);
		save.addEventListener('click', () => _save(result, error, save));
		actions.append(save);

		if (result.pdf_url) {
			let pdf = _btn('pharos-ds-abtn', _str('pharos-discovery-pdf'));
			pdf.addEventListener('click', () => Zotero.launchURL(result.pdf_url));
			actions.append(pdf);
		}

		foot.append(actions);
		return foot;
	}

	/**
	 * Send one result to a model.
	 *
	 * On 409 and on 503 the stored row is untouched -- analyze_result raises
	 * before the first field assignment and the request is rolled back -- so the
	 * card keeps everything it was already showing and only gains an inline
	 * line. Clearing or greying it would tell the reader their rules extraction
	 * had been lost, which is the opposite of what happened.
	 */
	async function _analyze(result, card, error, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = _str('pharos-discovery-analyzing');
		_line(error, '');
		try {
			let updated = await Zotero.Pharos.Discovery.analyze(result.id);
			let index = _results().findIndex(r => r.id == result.id);
			if (index >= 0) {
				_search.results[index] = updated;
			}
			// Also into the cached history row, so reopening this run from the
			// rail does not show the pre-analysis version.
			let cached = _searches.find(s => s.id == _search.id);
			if (cached && Array.isArray(cached.results)) {
				let cachedIndex = cached.results.findIndex(r => r.id == result.id);
				if (cachedIndex >= 0) {
					cached.results[cachedIndex] = updated;
				}
			}
			card.replaceWith(_card(updated));
		}
		catch (e) {
			Zotero.logError(e);
			button.disabled = false;
			button.textContent = original;
			_line(error, _msg(Zotero.Pharos.Discovery.analysisFailure(e)), true);
		}
	}

	async function _save(result, error, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = _str('pharos-discovery-saving');
		_line(error, '');
		try {
			await Zotero.Pharos.Discovery.saveToLibrary(result);
			_saved.add(result.id);
			button.textContent = _str('pharos-discovery-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.disabled = false;
			button.textContent = original;
			_line(error, _fmt('pharos-discovery-save-failed', { error: _reason(e) }), true);
		}
	}


	/* ------------------------------------------------------------- selection */

	function _syncSelection() {
		let results = _results();
		// A selection is only ever over what is on screen: a stale id from an
		// earlier run would be filed invisibly.
		for (let id of Array.from(_selected)) {
			if (!results.some(r => r.id == id)) {
				_selected.delete(id);
			}
		}

		for (let card of _el.list.children) {
			let on = _selected.has(card.dataset.resultId);
			card.classList.toggle('is-selected', on);
			let box = card.querySelector('input[type="checkbox"]');
			if (box) {
				box.checked = on;
			}
		}

		_el.selall.checked = !!results.length && _selected.size == results.length;
		_el.selall.indeterminate = _selected.size > 0 && _selected.size < results.length;
		_el.selcount.textContent = _fmt('pharos-discovery-selected', { count: _selected.size });
		_el.newCreate.textContent = _fmt('pharos-discovery-new-project-create', {
			count: _selected.size,
		});
	}

	/**
	 * File the whole selection in one go.
	 *
	 * One call, no modals. A partial failure resolves rather than throwing, so
	 * the exact papers that did not land are re-selected and the button is a
	 * retry.
	 */
	async function _fileSelection() {
		if (_filing) {
			return;
		}
		if (!_el.fileProject.value) {
			_line(_el.notice, _str('pharos-discovery-need-project'), true);
			return;
		}
		if (!_selected.size) {
			_line(_el.notice, _str('pharos-discovery-need-selection'), true);
			return;
		}

		let projectID = _el.fileProject.value;
		let project = _project(projectID);
		_filing = true;
		_el.file.disabled = true;
		_el.file.textContent = _str('pharos-discovery-adding');
		try {
			let outcome = await Zotero.Pharos.Discovery.addToProject({
				projectID,
				resultIDs: Array.from(_selected),
				existingResultIDs: project && project.sources
					? project.sources.map(s => s.result_id)
					: [],
			});
			_reportFiling(outcome, project ? project.name : projectID);
			_selected = new Set(outcome.failedIDs);
			// The 已在当前项目 chips are read from the project's own sources, so
			// they are stale until the list is re-fetched.
			await _loadProjects();
			_renderList();
		}
		catch (e) {
			Zotero.logError(e);
			_line(_el.notice, _fmt('pharos-discovery-file-error', { error: _reason(e) }), true);
		}
		finally {
			_filing = false;
			_el.file.disabled = false;
			_el.file.textContent = _str('pharos-discovery-add-to-project');
		}
	}

	/** The two optional clauses are fragments carrying their own leading
	 *  separator, appended only when their count is non-zero. */
	function _reportFiling(outcome, name) {
		let text = _fmt('pharos-discovery-file-result', { name, added: outcome.added });
		if (outcome.skipped) {
			text += _fmt('pharos-discovery-file-skipped', { count: outcome.skipped });
		}
		if (outcome.failed) {
			text += _fmt('pharos-discovery-file-failed', { count: outcome.failed });
		}
		_line(_el.notice, text + _stop(), !!outcome.failed);
	}

	/**
	 * Start a project from here and file the selection into it.
	 *
	 * Projects.create() has existed and been called from nowhere; the empty
	 * state used to tell the reader to go and use the web client instead.
	 */
	async function _createProject() {
		if (_filing) {
			return;
		}
		let name = _el.newName.value.trim();
		if (!name) {
			_el.newName.focus();
			return;
		}
		if (!_selected.size) {
			_line(_el.notice, _str('pharos-discovery-need-selection'), true);
			return;
		}

		_filing = true;
		_el.newCreate.disabled = true;
		let original = _el.newCreate.textContent;
		_el.newCreate.textContent = _str('pharos-discovery-new-project-creating');
		try {
			let project = await Zotero.Pharos.Projects.create({
				name,
				description: '',
				researchQuestion: _el.newQuestion.value.trim(),
			});
			let outcome = await Zotero.Pharos.Discovery.addToProject({
				projectID: project.id,
				resultIDs: Array.from(_selected),
				// Brand new, so nothing can already be on it.
				existingResultIDs: [],
			});
			_reportFiling(outcome, project.name);
			_selected = new Set(outcome.failedIDs);
			_el.newName.value = '';
			_el.newQuestion.value = '';
			_el.newBox.hidden = true;
			await _loadProjects();
			// The new project becomes the current one, which is what the panel
			// says it will do.
			_setProject(project.id);
		}
		catch (e) {
			Zotero.logError(e);
			_line(_el.notice, _fmt('pharos-discovery-file-error', { error: _reason(e) }), true);
		}
		finally {
			_filing = false;
			_el.newCreate.disabled = false;
			_el.newCreate.textContent = original;
			_syncSelection();
		}
	}
};
