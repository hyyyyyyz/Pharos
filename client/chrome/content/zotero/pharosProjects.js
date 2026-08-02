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
 * The research projects window.
 *
 * A project is a research question, the papers it rests on, and the records
 * written along the way. The desktop owns all of it -- creating a project,
 * correcting a hypothesis, filing a source and writing the note that explains
 * why it belongs are reading-side work, done while looking at the paper that
 * prompted them (docs/DECISIONS.md §4, as amended). What stays web-only is
 * composing the manuscript itself. The header this replaced said the opposite,
 * and the empty state it justified sent the user to the browser to do something
 * this window can do in place.
 *
 * The backend persists; it runs nothing, and says so in `automation_notice`,
 * which this window shows verbatim rather than paraphrases (§9). That notice
 * lives inside the records panel, immediately above the record list and stuck to
 * the top of the scroll, because a record of type `result` reading "95% accuracy"
 * must never be able to appear detached from it. It used to sit at the top of
 * the window in the same low-contrast italic as the "nothing here" placeholder,
 * which is two ways of saying the same thing badly.
 *
 * Deliberately not the web's:
 *
 *   - 存为笔记 files a record as a real standalone Zotero note through
 *     Zotero.Pharos.Projects.saveArtifactAsNote(), so a claim ends up beside the
 *     papers it was argued from. The web has no equivalent.
 *   - 加入文库 saves a source's paper into the local library through
 *     Zotero.Pharos.Discovery.saveToLibrary().
 *   - Links open in the system browser through Zotero.launchURL.
 *   - Source rows keep the desktop's author line; the web omits authors.
 */
var Zotero_Pharos_Projects = new function () {
	let _resolveInit;

	/** How many authors a source row prints before "et al.". */
	const MAX_AUTHORS = 3;

	/** The static elements, resolved once in _init(). */
	let _el = {};

	/* ------------------------------------------------------------ view state */

	let _signedOut = false;

	/** Every project the account has, archived included: list(true) is fetched
	 *  once and the archived filter is applied in the view, so toggling it costs
	 *  no request. */
	let _projects = [];
	let _listState = 'loading'; // loading | ready | error
	let _listError = null;

	/** Shown, like the web's ProjectsView. An account whose projects are all
	 *  archived used to open this window on 没有符合筛选的项目 and an empty desk
	 *  while the same account opened the web on a full list -- the same
	 *  checkbox, opposite initial state, and nothing on screen to say which. */
	let _showArchived = true;

	let _selectedID = null;
	let _project = null;
	let _projectState = 'idle'; // idle | loading | ready | error
	let _projectError = null;

	/**
	 * Which stage's records the panel is showing.
	 *
	 * Starts at the project's own stage and follows a deliberate stage PATCH and
	 * an advance. Clicking a node moves this and NOT the project: browsing what
	 * was written at an earlier stage must not rewind the project to it.
	 */
	let _viewedStage = null;

	/** The polite region: success notices, loading, and the signed-out state. */
	let _notice = '';

	/** The alert region: {text, hint}. */
	let _error = null;

	let _createOpen = false;
	let _creating = false;
	let _createError = null;
	let _createDraft = null;

	let _editOpen = false;
	let _saving = false;
	let _editDraft = null;

	let _stageChoice = null;
	let _stageSaving = false;
	let _advancing = false;

	/** A destructive action waiting for its second click: {kind, id}. Inline
	 *  rather than a modal, so the question stays next to the row it destroys. */
	let _confirm = null;
	let _busy = false;

	/** The open record editor, or null. `id` is null for a new record. */
	let _artifactDraft = null;
	let _artifactSaving = false;

	/** The source whose evidence note is being edited, and its draft text. */
	let _noteSourceID = null;
	let _noteDraft = '';
	let _noteSaving = false;

	/**
	 * What this session has already filed into the local library: discovery
	 * result ids for 加入文库, record ids for 存为笔记.
	 *
	 * Module level, not on the button. Every _render() rebuilds both controls --
	 * an edit, an archive, a stage save, an inline confirm, and the render inside
	 * _mutate()'s finally all do -- so a "已保存" written onto the element is gone
	 * by the next pass. Neither call underneath de-duplicates:
	 * saveExternalPaper() does `new Zotero.Item(itemType)` and saveArtifactAsNote()
	 * does `new Zotero.Item('note')` unconditionally, so a second click writes a
	 * second real item into the user's library and both clicks report success.
	 * 文献探索 keeps the same set for the same reason.
	 *
	 * Not cleared on project switch: the library is one library, and a paper
	 * filed from one project is still filed when it appears under another.
	 */
	let _savedSources = new Set();
	let _savedArtifacts = new Set();

	/**
	 * Settles once init() has finished, however it finished.
	 *
	 * Created here, when the script loads, rather than assigned from the onload
	 * handler: a caller that opens this window gets control back before onload
	 * has run, so an `initialized` that only exists afterwards is `undefined`
	 * exactly when someone needs to wait on it -- and `await undefined` succeeds
	 * silently.
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
	 * the .properties bundle, where no pharos-* id exists. It throws in en-US and
	 * silently returns the bare id in zh-CN. Fluent's own formatter is what reads
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
	 * A label from Zotero.Pharos.Projects' own lookup helpers.
	 *
	 * Those centralise the id spelling -- notably the `_` to `-` mangling for
	 * experiment_plan, which used to be inlined at four call sites and got it
	 * wrong at one of them -- but they call Zotero.getString() underneath, so an
	 * unknown stage or type arriving from a newer backend would throw in en-US
	 * and take the render with it. The key itself is at least a legible
	 * placeholder.
	 */
	function _label(fn, key) {
		try {
			return Zotero.Pharos.Projects[fn](key);
		}
		catch (e) {
			Zotero.logError(e);
			return String(key);
		}
	}

	/**
	 * An element localized through Fluent's DOM layer.
	 *
	 * Required for any message that is attributes-only (`.placeholder`,
	 * `.aria-label`): those have no value, so Zotero.getString() cannot read them
	 * at all and only data-l10n-id + data-l10n-attrs applies them.
	 */
	function _l10n(el, id, attrs) {
		el.setAttribute('data-l10n-id', id);
		if (attrs) {
			el.setAttribute('data-l10n-attrs', attrs);
		}
		return el;
	}

	/**
	 * What to show the user for a failed request.
	 *
	 * The server's own `detail` wherever there is one -- Zotero.Pharos.API
	 * unwraps it -- because those strings are specific and written for a person
	 * ("Archived projects cannot advance; reactivate the project first"), and
	 * rewriting them would mean guessing which failure occurred. A request that
	 * never completed is the exception: request() hands back the raw transport
	 * error, whose message names the URL and "status code 0", which is not
	 * something anyone can act on.
	 */
	function _failure(e) {
		if (e && typeof e.status == 'number' && e.status !== 0) {
			return { text: e.message || _str('pharos-projects-error') };
		}
		return {
			text: _str('pharos-error-unreachable'),
			hint: _str('pharos-daily-unreachable-hint'),
		};
	}

	function _isSignedOut(e) {
		return e instanceof Zotero.Pharos.API.SignedOutError;
	}

	let _dateFormat = null;

	/**
	 * A backend timestamp, formatted for display.
	 *
	 * Formatted here rather than with Fluent's DATETIME(): every date message in
	 * the bundle takes a preformatted string, matching what the discovery window
	 * does with its history times.
	 */
	function _date(value) {
		if (!value) {
			return '';
		}
		let iso = String(value);
		// A naive UTC datetime with no zone designator, which is what FastAPI
		// serialises by default, would be read as local time and land a day out
		// either side of midnight.
		if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(iso)) {
			iso += 'Z';
		}
		let date = new Date(iso);
		if (isNaN(date.getTime())) {
			return iso;
		}
		if (!_dateFormat) {
			_dateFormat = new Intl.DateTimeFormat(undefined, {
				year: 'numeric', month: 'short', day: 'numeric',
			});
		}
		return _dateFormat.format(date);
	}

	/* ------------------------------------------------------------ DOM helpers */

	// document.createElement() produces an HTML element in this document even
	// though the default namespace is XUL. Nothing built here is XUL: a XUL
	// button renders its `label` attribute and ignores textContent, which would
	// be a blank button for every string in the table.
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

	/** A ghost control. `modifier` is one of is-primary / is-danger /
	 *  is-danger-text, all of them state semantics and none of them a
	 *  XUL-reserved attribute name. */
	function _action(text, modifier, onClick) {
		let button = _btn('pharos-pv-btn' + (modifier ? ' ' + modifier : ''), text);
		// Never `type = "submit"`: these live inside an html:form in the sidebar,
		// and a submit button would fire the form's handler on the same click
		// that already ran onClick.
		button.type = 'button';
		if (onClick) {
			button.addEventListener('click', onClick);
		}
		return button;
	}

	function _field(labelText, control) {
		let wrap = _div('pharos-pv-field');
		if (labelText) {
			wrap.append(_make('label', 'pharos-pv-label', labelText));
		}
		wrap.append(control);
		return wrap;
	}

	function _input(placeholderID, value, onInput) {
		let el = _make('input', 'pharos-pv-input');
		el.type = 'text';
		el.value = value || '';
		if (placeholderID) {
			_l10n(el, placeholderID, 'placeholder');
		}
		el.addEventListener('input', () => onInput(el.value));
		return el;
	}

	function _textarea(placeholderID, value, onInput, rows) {
		let el = _make('textarea', 'pharos-pv-textarea');
		el.value = value || '';
		if (rows) {
			el.rows = rows;
		}
		if (placeholderID) {
			_l10n(el, placeholderID, 'placeholder');
		}
		el.addEventListener('input', () => onInput(el.value));
		return el;
	}

	function _select(options, value, onChange) {
		let el = _make('select', 'pharos-pv-select');
		for (let option of options) {
			let node = _make('option', null, option.label);
			node.value = option.value;
			el.append(node);
		}
		el.value = value;
		el.addEventListener('change', () => onChange(el.value));
		return el;
	}

	function _stageOptions() {
		return Zotero.Pharos.Projects.STAGES.map(stage => ({
			value: stage,
			label: _label('stageLabel', stage),
		}));
	}

	function _typeOptions() {
		return Zotero.Pharos.Projects.ARTIFACT_TYPES.map(type => ({
			value: type,
			label: _label('typeLabel', type),
		}));
	}

	function _statusOptions() {
		return Zotero.Pharos.Projects.ARTIFACT_STATUSES.map(status => ({
			value: status,
			label: _label('statusLabel', status),
		}));
	}

	/* --------------------------------------------------------------- derived */

	/** The rows the sidebar shows. The backend returns everything; this is the
	 *  whole cost of the archived toggle. */
	function _visible() {
		return _showArchived ? _projects : _projects.filter(p => p.status != 'archived');
	}

	function _artifacts() {
		return (_project && _project.artifacts) || [];
	}

	function _stageCount(stage) {
		return _artifacts().filter(a => a.stage == stage).length;
	}

	/** The record list is filtered to the viewed stage, not the flat list of
	 *  every record in the project: a claim and the plan it came from belong to
	 *  different moments and reading them as one list is how a plan gets mistaken
	 *  for a result. */
	function _viewedArtifacts() {
		return _artifacts().filter(a => a.stage == _viewedStage);
	}

	function _sourceCount(project) {
		return typeof project.source_count == 'number'
			? project.source_count
			: (project.sources || []).length;
	}

	function _artifactCount(project) {
		return typeof project.artifact_count == 'number'
			? project.artifact_count
			: (project.artifacts || []).length;
	}

	/**
	 * What 加入文库 saves, keyed the way the library sees it.
	 *
	 * The discovery result, not the project source: the same paper filed into two
	 * projects is two ProjectSource rows and one item in the library, and keying
	 * on the source id would offer to save it a second time.
	 */
	function _sourceKey(source) {
		return (source.result && source.result.id) || source.id;
	}

	/** Kept from the desktop's own row rather than dropped to match the web,
	 *  which omits authors entirely. */
	function _authorText(authors) {
		if (!authors || !authors.length) {
			return '';
		}
		return authors.slice(0, MAX_AUTHORS).join(', ')
			+ (authors.length > MAX_AUTHORS ? ' et al.' : '');
	}

	/**
	 * Take a project payload as the new truth.
	 *
	 * create / update / archive / restore / setStage / advance all return the
	 * COMPLETE project -- project_out always serialises the eager-loaded sources
	 * and artifacts -- so their responses are rendered directly. The comment this
	 * replaced claimed advance() came back without them and paid for a whole
	 * extra GET to work around something that was never true.
	 *
	 * @param {Object} project
	 * @param {Boolean} follow - move the viewed stage to the project's own. True
	 *     for a deliberate stage change and for a project the user just opened;
	 *     false for a refresh, which must not yank the reader off the stage they
	 *     were browsing.
	 */
	function _adopt(project, follow) {
		_project = project;
		_selectedID = project.id;
		let index = _projects.findIndex(p => p.id == project.id);
		if (index == -1) {
			_projects.unshift(project);
		}
		else {
			_projects[index] = project;
		}
		if (follow || !_viewedStage) {
			_viewedStage = project.stage;
		}
		_stageChoice = null;
	}

	/** Editors hold unsaved text, so leaving a project has to drop them rather
	 *  than carry them onto the next one. */
	function _closeEditors() {
		_editOpen = false;
		_editDraft = null;
		_artifactDraft = null;
		_noteSourceID = null;
		_noteDraft = '';
		_confirm = null;
		_stageChoice = null;
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
			count: document.getElementById('pharos-pv-count'),
			new: document.getElementById('pharos-pv-new'),
			create: document.getElementById('pharos-pv-create'),
			showArchived: document.getElementById('pharos-pv-show-archived'),
			list: document.getElementById('pharos-pv-list'),
			sideState: document.getElementById('pharos-pv-side-state'),
			status: document.getElementById('pharos-pv-status'),
			error: document.getElementById('pharos-pv-error'),
			desk: document.getElementById('pharos-pv-desk'),
		};

		// `click` only. These are html:button and html:input, which have no
		// `command` event; the XUL controls they replaced needed both and
		// rendered blank labels.
		_el.new.addEventListener('click', () => this.toggleCreate());
		_el.showArchived.addEventListener('change', () => {
			_showArchived = _el.showArchived.checked;
			this._render();
		});
		// Submitting an HTML form inside a chrome document would try to navigate
		// the docshell out from under the window. Kept as the guard it is, but it
		// is NOT how Enter reaches createProject(): implicit submission needs
		// either a submit button or exactly one field that blocks it, and this
		// form has neither -- every control is type="button" on purpose, to avoid
		// firing the form on the same click that already ran onClick, and there
		// are two text inputs. So `submit` never fires here.
		_el.create.addEventListener('submit', (event) => {
			event.preventDefault();
			this.createProject();
		});
		// Which is why Enter is handled outright, the way 文献探索 handles it on
		// its query field. Inputs only: Enter inside the description textarea is a
		// newline, and swallowing it would cost the user the one multi-line field
		// in the form.
		_el.create.addEventListener('keydown', (event) => {
			if (event.key == 'Enter' && event.target.localName == 'input') {
				event.preventDefault();
				this.createProject();
			}
		});

		if (!Zotero.Pharos.API.hasCredentials()) {
			this._goSignedOut();
			return;
		}

		await this.loadProjects();
	};

	this._goSignedOut = function () {
		_signedOut = true;
		_listState = 'ready';
		_projects = [];
		_selectedID = null;
		_project = null;
		_projectState = 'idle';
		_closeEditors();
		_createOpen = false;
		_notice = _str('pharos-error-signed-out-detail');
		_error = null;
		this._render();
	};

	/* ----------------------------------------------------------------- loads */

	this.loadProjects = async function () {
		_listState = 'loading';
		_listError = null;
		this._render();
		let projects;
		try {
			// list(true): the archived filter is applied in the view, so the
			// toggle never costs a second request. Discovery's addToProject()
			// calls list() bare and keeps getting active-only.
			projects = await Zotero.Pharos.Projects.list(true);
		}
		catch (e) {
			Zotero.logError(e);
			if (_isSignedOut(e)) {
				this._goSignedOut();
				return;
			}
			_projects = [];
			_listState = 'error';
			_listError = _failure(e);
			this._render();
			return;
		}
		_projects = projects;
		_listState = 'ready';

		let visible = _visible();
		let stillThere = _selectedID && _projects.some(p => p.id == _selectedID);
		if (stillThere) {
			await this.select(_selectedID);
			return;
		}
		if (visible.length) {
			await this.select(visible[0].id);
			return;
		}
		_selectedID = null;
		_project = null;
		_projectState = 'idle';
		this._render();
	};

	this.select = async function (projectID) {
		_selectedID = projectID;
		_projectState = 'loading';
		_projectError = null;
		_closeEditors();
		// Render what is already in hand rather than blanking the desk. The list
		// payload is not a stub: GET /api/projects returns full ProjectOut rows
		// with `sources` and `artifacts` eager-loaded, and _projects holds them --
		// they are what Discovery's 已在当前项目 chip reads. Setting _project =
		// null here emptied the header, the research question, the stage path and
		// both panels on every project switch, over data the window already had.
		// The web renders straight through for the same reason.
		let cached = _projects.find(p => p.id == projectID) || null;
		_project = cached;
		_viewedStage = cached ? cached.stage : null;
		_notice = _str('pharos-projects-loading');
		_error = null;
		this._render();
		try {
			_adopt(await Zotero.Pharos.Projects.get(projectID), true);
			_projectState = 'ready';
			_notice = '';
		}
		catch (e) {
			Zotero.logError(e);
			if (_isSignedOut(e)) {
				this._goSignedOut();
				return;
			}
			_projectState = 'error';
			_projectError = _failure(e);
			_notice = '';
		}
		this._render();
	};

	/**
	 * Refetch the selected project.
	 *
	 * Needed after every source and record mutation: those return only the
	 * changed row, or null, which leaves source_count and artifact_count on the
	 * cached project wrong -- and both are rendered in the sidebar. One request,
	 * and the counts stay honest.
	 */
	this._refresh = async function () {
		_adopt(await Zotero.Pharos.Projects.get(_project.id), false);
	};

	/**
	 * Run a mutation, routing its failure to the alert region.
	 *
	 * @param {Function} work - returns a promise
	 * @param {String} [notice] - shown on success
	 */
	this._mutate = async function (work, notice) {
		if (_busy) {
			return false;
		}
		_busy = true;
		_error = null;
		this._render();
		try {
			await work();
			_notice = notice || '';
			return true;
		}
		catch (e) {
			Zotero.logError(e);
			if (_isSignedOut(e)) {
				this._goSignedOut();
				return false;
			}
			_error = _failure(e);
			return false;
		}
		finally {
			_busy = false;
			this._render();
		}
	};

	/* ------------------------------------------------------- project writes */

	this.toggleCreate = function () {
		_createOpen = !_createOpen;
		_createError = null;
		_createDraft = _createOpen ? { name: '', question: '', description: '' } : null;
		this._render();
		if (_createOpen) {
			let input = document.getElementById('pharos-pv-create-name');
			if (input) {
				input.focus();
			}
		}
	};

	this.createProject = async function () {
		if (_creating || !_createDraft || !_createDraft.name.trim()) {
			return;
		}
		_creating = true;
		_createError = null;
		this._render();
		try {
			let project = await Zotero.Pharos.Projects.create({
				name: _createDraft.name.trim(),
				description: _createDraft.description.trim(),
				researchQuestion: _createDraft.question.trim(),
			});
			_createOpen = false;
			_createDraft = null;
			_closeEditors();
			_adopt(project, true);
			_projectState = 'ready';
			_notice = _fmt('pharos-projects-created', { name: project.name });
			_error = null;
		}
		catch (e) {
			Zotero.logError(e);
			if (_isSignedOut(e)) {
				_creating = false;
				this._goSignedOut();
				return;
			}
			_createError = _failure(e).text;
		}
		finally {
			_creating = false;
			this._render();
		}
	};

	this.openEdit = function () {
		_editOpen = true;
		_editDraft = {
			name: _project.name || '',
			question: _project.research_question || '',
			description: _project.description || '',
		};
		_confirm = null;
		this._render();
	};

	this.closeEdit = function () {
		_editOpen = false;
		_editDraft = null;
		this._render();
	};

	this.saveProject = async function () {
		if (_saving || !_editDraft || !_editDraft.name.trim()) {
			return;
		}
		let patch = {};
		if (_editDraft.name.trim() != _project.name) {
			patch.name = _editDraft.name.trim();
		}
		// "" rather than null to clear. patch_project model_dumps with
		// exclude_none, so a null is DROPPED rather than applied:
		// update(id, {description: null}) succeeds and changes nothing.
		if (_editDraft.description != (_project.description || '')) {
			patch.description = _editDraft.description;
		}
		if (_editDraft.question != (_project.research_question || '')) {
			// eslint-disable-next-line camelcase -- the wire field name
			patch.research_question = _editDraft.question;
		}
		// An empty PATCH body is a 400, never a no-op, so a save with nothing
		// changed closes the form instead of asking.
		if (!Object.keys(patch).length) {
			this.closeEdit();
			return;
		}
		_saving = true;
		let ok = await this._mutate(async () => {
			_adopt(await Zotero.Pharos.Projects.update(_project.id, patch), false);
		}, _str('pharos-projects-updated'));
		_saving = false;
		if (ok) {
			_editOpen = false;
			_editDraft = null;
		}
		this._render();
	};

	this.toggleArchive = async function () {
		let id = _project.id;
		let archived = _project.status == 'archived';
		await this._mutate(async () => {
			_adopt(archived
				? await Zotero.Pharos.Projects.restore(id)
				: await Zotero.Pharos.Projects.archive(id), false);
		}, _str('pharos-projects-updated'));
	};

	this.deleteProject = async function () {
		let id = _project.id;
		await this._mutate(async () => {
			await Zotero.Pharos.Projects.remove(id);
			// remove() returns null: drop the row locally and let the empty state
			// take over rather than paying for a list() that says the same thing.
			_projects = _projects.filter(p => p.id != id);
			_project = null;
			_selectedID = null;
			_viewedStage = null;
			_projectState = 'idle';
			_closeEditors();
		}, _str('pharos-projects-deleted'));
	};

	/* --------------------------------------------------------- stage writes */

	/** Browsing, not moving. The project's own stage is untouched. */
	this.viewStage = function (stage) {
		_viewedStage = stage;
		_artifactDraft = null;
		_confirm = null;
		this._render();
	};

	this.saveStage = async function () {
		let stage = _stageChoice;
		if (!stage || stage == _project.stage) {
			return;
		}
		let id = _project.id;
		_stageSaving = true;
		await this._mutate(async () => {
			// The rewind path. update_project allows backwards movement by design
			// -- a failed experiment sends a project back to ideation -- and
			// allows it while archived, because a PATCH only corrects recorded
			// metadata.
			_adopt(await Zotero.Pharos.Projects.setStage(id, stage), true);
		}, _str('pharos-projects-updated'));
		_stageSaving = false;
		this._render();
	};

	this.advance = async function () {
		if (!_project || !Zotero.Pharos.Projects.canAdvance(_project)) {
			return;
		}
		let id = _project.id;
		_advancing = true;
		// The notice names the stage the project ARRIVED at, which is only known
		// once the response is in, and _mutate() writes its own notice after the
		// work resolves -- so this one is set afterwards or it would be erased.
		let ok = await this._mutate(async () => {
			_adopt(await Zotero.Pharos.Projects.advance(id), true);
		});
		_advancing = false;
		if (ok) {
			_notice = _fmt('pharos-projects-advanced', {
				stage: _label('stageLabel', _project.stage),
			});
		}
		this._render();
	};

	/* -------------------------------------------------------- source writes */

	this.editSourceNote = function (source) {
		_noteSourceID = source.id;
		_noteDraft = source.note || '';
		_confirm = null;
		this._render();
	};

	this.closeSourceNote = function () {
		_noteSourceID = null;
		_noteDraft = '';
		this._render();
	};

	this.saveSourceNote = async function (sourceID) {
		let text = _noteDraft.trim();
		let id = _project.id;
		_noteSaving = true;
		let ok = await this._mutate(async () => {
			// SourcePatch.note is str | None with no default, and
			// patch_project_source passes it straight through: this is the one
			// place in the API where null means "clear" rather than "leave alone".
			await Zotero.Pharos.Projects.updateSource(id, sourceID, text || null);
			await this._refresh();
		}, _str('pharos-projects-source-note-saved'));
		_noteSaving = false;
		if (ok) {
			_noteSourceID = null;
			_noteDraft = '';
		}
		this._render();
	};

	this.removeSource = async function (sourceID) {
		let id = _project.id;
		_confirm = null;
		await this._mutate(async () => {
			await Zotero.Pharos.Projects.removeSource(id, sourceID);
			await this._refresh();
		}, _str('pharos-projects-source-removed'));
	};

	/* ------------------------------------------------------ record writes */

	this.newArtifact = function () {
		let stage = _viewedStage || _project.stage;
		_artifactDraft = {
			id: null,
			stage,
			// Seeded from the stage the user is looking at, so the common case is
			// one field already right rather than four fields to set.
			type: Zotero.Pharos.Projects.DEFAULT_TYPE[stage],
			status: 'draft',
			title: '',
			body: '',
			original: null,
		};
		_confirm = null;
		this._render();
	};

	this.editArtifact = function (artifact) {
		_artifactDraft = {
			id: artifact.id,
			stage: artifact.stage,
			type: artifact.type,
			status: artifact.status,
			title: artifact.title || '',
			body: artifact.body || '',
			original: artifact,
		};
		_confirm = null;
		this._render();
	};

	this.closeArtifact = function () {
		_artifactDraft = null;
		this._render();
	};

	this.saveArtifact = async function () {
		let draft = _artifactDraft;
		if (!draft || _artifactSaving || !draft.title.trim()) {
			return;
		}
		let id = _project.id;
		let saved = draft.status == 'verified'
			? _str('pharos-projects-artifact-saved-verified')
			: _str('pharos-projects-artifact-saved');

		if (draft.id) {
			let patch = {};
			let was = draft.original;
			if (draft.stage != was.stage) {
				patch.stage = draft.stage;
			}
			if (draft.type != was.type) {
				patch.type = draft.type;
			}
			if (draft.status != was.status) {
				patch.status = draft.status;
			}
			if (draft.title.trim() != was.title) {
				patch.title = draft.title.trim();
			}
			// "" clears; a null would be dropped by exclude_none and the body
			// would silently stay.
			if (draft.body != (was.body || '')) {
				patch.body = draft.body;
			}
			if (!Object.keys(patch).length) {
				this.closeArtifact();
				return;
			}
			_artifactSaving = true;
			let ok = await this._mutate(async () => {
				await Zotero.Pharos.Projects.updateArtifact(id, draft.id, patch);
				await this._refresh();
			}, saved);
			_artifactSaving = false;
			if (ok) {
				_artifactDraft = null;
			}
			this._render();
			return;
		}

		_artifactSaving = true;
		let ok = await this._mutate(async () => {
			await Zotero.Pharos.Projects.createArtifact(id, {
				stage: draft.stage,
				type: draft.type,
				title: draft.title.trim(),
				body: draft.body,
				status: draft.status,
			});
			await this._refresh();
			// A record filed at a stage the user is not looking at would vanish
			// on save, which reads as a failure.
			_viewedStage = draft.stage;
		}, saved);
		_artifactSaving = false;
		if (ok) {
			_artifactDraft = null;
		}
		this._render();
	};

	this.deleteArtifact = async function (artifactID) {
		let id = _project.id;
		_confirm = null;
		await this._mutate(async () => {
			await Zotero.Pharos.Projects.removeArtifact(id, artifactID);
			await this._refresh();
		}, _str('pharos-projects-artifact-deleted'));
	};

	/* ------------------------------------------------------ library writes */

	/**
	 * 加入文库 on a source row. Desktop-only; the web has no equivalent.
	 *
	 * The button reports its own outcome without a render pass, because nothing
	 * about the project changed -- but the fact that it succeeded is recorded in
	 * _savedSources, not on the button, because the next render rebuilds it.
	 */
	this.saveSource = async function (source, button) {
		let key = _sourceKey(source);
		if (_savedSources.has(key)) {
			return;
		}
		button.disabled = true;
		let original = button.textContent;
		button.textContent = _str('pharos-daily-saving');
		try {
			await Zotero.Pharos.Discovery.saveToLibrary(source.result);
			_savedSources.add(key);
			button.textContent = _str('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			_error = { text: e.message || _str('pharos-daily-save-failed') };
			this._render();
		}
	};

	/** 存为笔记. The module's reason for existing: a claim ends up beside the
	 *  papers it was argued from, searchable with everything else. */
	this.saveArtifactNote = async function (artifact, project, button) {
		if (_savedArtifacts.has(artifact.id)) {
			return;
		}
		button.disabled = true;
		let original = button.textContent;
		button.textContent = _str('pharos-daily-saving');
		try {
			await Zotero.Pharos.Projects.saveArtifactAsNote(artifact, project);
			_savedArtifacts.add(artifact.id);
			button.textContent = _str('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			_error = { text: e.message || _str('pharos-daily-save-failed') };
			this._render();
		}
	};

	/* ---------------------------------------------------------------- render */

	this._render = function () {
		this._renderRegions();
		this._renderSidebar();
		this._renderDesk();
	};

	this._renderRegions = function () {
		_el.status.textContent = _notice || '';
		_el.status.hidden = !_notice;

		_el.error.replaceChildren();
		_el.error.hidden = !_error;
		if (_error) {
			_el.error.append(document.createTextNode(_error.text));
			if (_error.hint) {
				_el.error.append(_span('pharos-pv-error-hint', _error.hint));
			}
		}
	};

	/* ---------------------------------------------------------------- sidebar */

	this._renderSidebar = function () {
		let visible = _visible();

		_el.new.disabled = _signedOut || _creating;
		_el.showArchived.disabled = _signedOut;
		_el.showArchived.checked = _showArchived;
		// The account's projects, not the rows the filter left. The web puts
		// projects.length in this same slot, and a badge that counted the visible
		// rows reported a smaller number for the same account -- and blanked
		// itself at zero, removing the one place that would have said the account
		// has projects the filter is hiding.
		_el.count.textContent = _listState == 'ready' && !_signedOut
			? String(_projects.length)
			: '';

		this._renderCreateForm();

		_el.list.replaceChildren();
		for (let project of visible) {
			_el.list.append(this._renderItem(project));
		}

		_el.sideState.replaceChildren();
		_el.sideState.classList.remove('is-error');
		_el.sideState.hidden = false;
		if (_signedOut) {
			// The signed-out sentence is already in the status region, and the
			// contract keeps the list and the desk empty.
			_el.sideState.hidden = true;
		}
		else if (_listState == 'loading') {
			_el.sideState.textContent = _str('pharos-projects-loading');
		}
		else if (_listState == 'error') {
			_el.sideState.classList.add('is-error');
			_el.sideState.append(_div(null, _listError.text));
			if (_listError.hint) {
				_el.sideState.append(_div(null, _listError.hint));
			}
			let retry = _btn('pharos-pv-side-retry', _str('pharos-projects-retry'));
			retry.addEventListener('click', () => this.loadProjects());
			_el.sideState.append(retry);
		}
		else if (!_projects.length) {
			_el.sideState.textContent = _str('pharos-projects-empty');
		}
		else if (!visible.length) {
			// Distinct from "no projects": everything is archived and the filter
			// is hiding it, which is one checkbox away from being fixed.
			_el.sideState.textContent = _str('pharos-projects-none-matched');
		}
		else {
			_el.sideState.hidden = true;
		}
	};

	this._renderCreateForm = function () {
		_el.create.replaceChildren();
		_el.create.hidden = !_createOpen;
		if (!_createOpen) {
			return;
		}

		// The two controls first, because the name field enables and disables the
		// submit as it is typed into and must not re-render to do it: a render
		// per keystroke would rebuild the input and take the caret with it.
		let cancel = _action(_str('pharos-projects-cancel'), null, () => this.toggleCreate());
		let submit = _action(
			_creating ? _str('pharos-projects-creating') : _str('pharos-projects-create-submit'),
			'is-primary',
			() => this.createProject()
		);
		// The backend answers 400 "name cannot be empty"; a round trip to be told
		// what the form already knows is a worse answer than a disabled button.
		submit.disabled = !_createDraft.name.trim() || _creating;
		cancel.disabled = _creating;

		_el.create.append(_div('pharos-pv-create-head', _str('pharos-projects-create-title')));

		let name = _input('pharos-projects-name-input', _createDraft.name, (v) => {
			_createDraft.name = v;
			submit.disabled = !v.trim() || _creating;
		});
		name.id = 'pharos-pv-create-name';
		_el.create.append(_field(_str('pharos-projects-name'), name));

		_el.create.append(_field(null,
			_input('pharos-projects-question-input', _createDraft.question, (v) => {
				_createDraft.question = v;
			})));
		_el.create.append(_field(null,
			_textarea('pharos-projects-description-input', _createDraft.description, (v) => {
				_createDraft.description = v;
			}, 2)));

		if (_createError) {
			_el.create.append(_div('pharos-pv-form-error', _createError));
		}

		let actions = _div('pharos-pv-create-actions');
		actions.append(cancel, submit);
		_el.create.append(actions);
	};

	this._renderItem = function (project) {
		let row = _btn('pharos-pv-item' + (project.id == _selectedID ? ' is-active' : ''));
		row.addEventListener('click', () => this.select(project.id));

		let top = _div('pharos-pv-item-top');
		let dot = _span('pharos-pv-state-dot'
			+ (project.status == 'archived' ? ' is-archived' : ''));
		dot.title = _str(project.status == 'archived'
			? 'pharos-projects-state-archived'
			: 'pharos-projects-state-active');
		top.append(_span('pharos-pv-item-name', project.name), dot);
		row.append(top);

		row.append(_div('pharos-pv-item-stage', _label('stageLabel', project.stage)));
		row.append(_div('pharos-pv-item-meta', _fmt('pharos-projects-item-meta', {
			sources: _sourceCount(project),
			records: _artifactCount(project),
		})));
		return row;
	};

	/* ------------------------------------------------------------------ desk */

	this._renderDesk = function () {
		_el.desk.replaceChildren();
		if (_signedOut) {
			return;
		}
		if (_projectState == 'loading' && !_project) {
			// Nothing cached to render through -- the very first selection of the
			// session, or a project that arrived from somewhere other than the
			// list. The status region carries 正在读取项目…; a skeleton here would
			// just be a second thing saying it.
			return;
		}
		if (_projectState == 'error') {
			_el.desk.append(this._renderLoadFailed());
			return;
		}
		if (!_project) {
			_el.desk.append(this._renderWelcome());
			return;
		}

		_el.desk.append(this._renderHeader());
		if (_editOpen) {
			_el.desk.append(this._renderEdit());
		}
		// Not while the edit form is open: the same text is loaded in that form's
		// 研究问题 textarea directly above, so an edit in progress would sit beside
		// the stale saved copy of itself with nothing saying which is which.
		if (_project.research_question && !_editOpen) {
			let block = _div('pharos-pv-question');
			block.append(_div('pharos-pv-question-label', _str('pharos-projects-question')));
			block.append(_div('pharos-pv-question-text', _project.research_question));
			_el.desk.append(block);
		}
		_el.desk.append(this._renderStagePath());
		_el.desk.append(this._renderWorkGrid());
	};

	this._renderWelcome = function () {
		let wrap = _div('pharos-pv-welcome');
		let inner = _div('pharos-pv-welcome-inner');
		inner.append(_div('pharos-pv-welcome-mark'));
		inner.append(_div('pharos-pv-welcome-title', _str('pharos-projects-welcome-title')));
		inner.append(_div('pharos-pv-welcome-desc', _str('pharos-projects-welcome-desc')));
		let cta = _btn('pharos-pv-cta', _str('pharos-projects-new'));
		cta.addEventListener('click', () => this.toggleCreate());
		inner.append(cta);
		wrap.append(inner);
		return wrap;
	};

	this._renderLoadFailed = function () {
		let wrap = _div('pharos-pv-load-failed');
		let inner = _div('pharos-pv-welcome-inner');
		inner.append(_div('pharos-pv-load-failed-title',
			_str('pharos-projects-load-failed-title')));
		inner.append(_div('pharos-pv-load-failed-text', _projectError.text));
		if (_projectError.hint) {
			inner.append(_div('pharos-pv-load-failed-text', _projectError.hint));
		}
		let retry = _btn('pharos-pv-cta', _str('pharos-projects-retry'));
		retry.addEventListener('click', () => this.select(_selectedID));
		inner.append(retry);
		wrap.append(inner);
		return wrap;
	};

	/* ---------------------------------------------------------------- header */

	this._renderHeader = function () {
		let header = _div('pharos-pv-header');
		let title = _div('pharos-pv-header-title');

		let kicker = _div('pharos-pv-kicker');
		let archived = _project.status == 'archived';
		kicker.append(_span('pharos-pv-state' + (archived ? ' is-archived' : ''),
			_str(archived ? 'pharos-projects-state-archived' : 'pharos-projects-state-active')));
		kicker.append(_span(null, _label('stageLabel', _project.stage)));
		title.append(kicker);

		title.append(_div('pharos-pv-title', _project.name));
		if (_project.description) {
			title.append(_div('pharos-pv-desc', _project.description));
		}

		let meta = _div('pharos-pv-meta');
		if (_project.created_at) {
			meta.append(_span(null, _fmt('pharos-projects-meta-created',
				{ date: _date(_project.created_at) })));
		}
		if (_project.updated_at) {
			meta.append(_span(null, _fmt('pharos-projects-meta-updated',
				{ date: _date(_project.updated_at) })));
		}
		if (meta.children.length) {
			title.append(meta);
		}
		header.append(title);

		let actions = _div('pharos-pv-header-actions');
		let edit = _action(_str('pharos-projects-edit'), null,
			() => (_editOpen ? this.closeEdit() : this.openEdit()));
		edit.disabled = _busy;
		actions.append(edit);

		let archive = _action(
			_str(archived ? 'pharos-projects-restore' : 'pharos-projects-archive'),
			null,
			() => this.toggleArchive()
		);
		archive.disabled = _busy;
		actions.append(archive);

		if (_confirm && _confirm.kind == 'project') {
			// Inline, not a modal: a delete that cascades to every source and
			// record has no undo, so the question belongs beside the thing.
			let confirm = _span('pharos-pv-delete-confirm');
			confirm.append(_span(null, _str('pharos-projects-delete-confirm')));
			let go = _action(_str('pharos-projects-delete-submit'), 'is-danger',
				() => this.deleteProject());
			go.disabled = _busy;
			let cancel = _action(_str('pharos-projects-cancel'), null, () => {
				_confirm = null;
				this._render();
			});
			confirm.append(go, cancel);
			actions.append(confirm);
		}
		else {
			let del = _action(_str('pharos-projects-delete'), 'is-danger-text', () => {
				_confirm = { kind: 'project', id: _project.id };
				this._render();
			});
			del.disabled = _busy;
			actions.append(del);
		}
		header.append(actions);
		return header;
	};

	this._renderEdit = function () {
		let form = _div('pharos-pv-edit');

		// Declared before the fields that toggle them: typing must not re-render.
		let cancel = _action(_str('pharos-projects-cancel'), null, () => this.closeEdit());
		let save = _action(
			_saving ? _str('pharos-projects-saving') : _str('pharos-projects-save'),
			'is-primary',
			() => this.saveProject()
		);
		save.disabled = !_editDraft.name.trim() || _saving;
		cancel.disabled = _saving;

		form.append(_field(_str('pharos-projects-name'),
			_input('pharos-projects-name-input', _editDraft.name, (v) => {
				_editDraft.name = v;
				save.disabled = !v.trim() || _saving;
			})));
		form.append(_field(_str('pharos-projects-question'),
			_textarea('pharos-projects-question-input', _editDraft.question, (v) => {
				_editDraft.question = v;
			}, 2)));
		// Cleared to "" rather than left null: a null in the patch is dropped by
		// exclude_none and the old text would silently survive the save.
		form.append(_field(_str('pharos-projects-description'),
			_textarea('pharos-projects-description-input', _editDraft.description, (v) => {
				_editDraft.description = v;
			}, 3)));

		let actions = _div('pharos-pv-edit-actions');
		actions.append(cancel, save);
		form.append(actions);
		return form;
	};

	/* ------------------------------------------------------------ stage path */

	this._renderStagePath = function () {
		let section = _div('pharos-pv-stage-section');

		let head = _div('pharos-pv-section-head');
		head.append(_span('pharos-pv-section-kicker', _str('pharos-projects-path')));
		head.append(_span('pharos-pv-section-title', _label('stageNote', _project.stage)));
		section.append(head);

		let controls = _div('pharos-pv-stage-controls');
		let select = _select(_stageOptions(), _stageChoice || _project.stage, (value) => {
			_stageChoice = value;
			this._render();
		});
		select.id = 'pharos-pv-stage-select';
		// Attributes-only message: unreadable by getString, so it can only be
		// applied through Fluent's DOM layer.
		_l10n(select, 'pharos-projects-stage-select', 'aria-label');
		select.disabled = _busy;
		controls.append(select);

		let saveStage = _action(_str('pharos-projects-stage-save'), null, () => this.saveStage());
		saveStage.id = 'pharos-pv-stage-save';
		saveStage.disabled = _busy || _stageSaving
			|| !_stageChoice || _stageChoice == _project.stage;
		controls.append(saveStage);

		let advance = _action(
			_advancing ? _str('pharos-projects-advancing') : _str('pharos-projects-advance'),
			'is-primary',
			() => this.advance()
		);
		advance.id = 'pharos-pv-advance';
		// canAdvance() is false for the last stage AND for an archived project:
		// advance_project answers 409 "Archived projects cannot advance" and an
		// enabled button that only ever 409s is worse than a disabled one.
		advance.disabled = _busy || !Zotero.Pharos.Projects.canAdvance(_project);
		controls.append(advance);
		section.append(controls);

		let timeline = _div('pharos-pv-timeline');
		let current = Zotero.Pharos.Projects.STAGES.indexOf(_project.stage);
		Zotero.Pharos.Projects.STAGES.forEach((stage, index) => {
			let classes = ['pharos-pv-stage'];
			if (index < current) {
				classes.push('is-past');
			}
			if (stage == _project.stage) {
				classes.push('is-current');
			}
			if (stage == _viewedStage) {
				classes.push('is-viewed');
			}
			let node = _btn(classes.join(' '));
			node.addEventListener('click', () => this.viewStage(stage));
			node.append(_span('pharos-pv-stage-node', String(index + 1)));
			node.append(_span('pharos-pv-stage-short', _label('stageShort', stage)));
			let count = _stageCount(stage);
			node.append(_span('pharos-pv-stage-count', count
				? _fmt('pharos-projects-stage-count', { count })
				: _str('pharos-projects-stage-count-none')));
			timeline.append(node);
		});
		section.append(timeline);

		// The one control in this window that could be read as "run this stage",
		// so §9 is said right next to it.
		section.append(_div('pharos-pv-stage-help', _str('pharos-projects-stage-help')));
		return section;
	};

	/* ------------------------------------------------------------- work grid */

	this._renderWorkGrid = function () {
		let grid = _div('pharos-pv-workgrid');
		grid.append(this._renderSourcesPanel());
		grid.append(this._renderArtifactsPanel());
		return grid;
	};

	this._renderSourcesPanel = function () {
		let panel = _div('pharos-pv-panel');
		let head = _div('pharos-pv-panel-head');
		head.append(_span('pharos-pv-panel-kicker', _str('pharos-projects-sources-head')));
		head.append(_span('pharos-pv-panel-count',
			_fmt('pharos-projects-sources', { count: _sourceCount(_project) })));
		panel.append(head);

		let sources = _project.sources || [];
		if (!sources.length) {
			let empty = _div('pharos-pv-panel-empty');
			empty.append(_div('pharos-pv-panel-empty-title',
				_str('pharos-projects-sources-empty-title')));
			empty.append(_div('pharos-pv-panel-empty-desc',
				_str('pharos-projects-sources-empty-desc')));
			panel.append(empty);
			return panel;
		}
		for (let source of sources) {
			panel.append(this._renderSource(source));
		}
		return panel;
	};

	this._renderSource = function (source) {
		let result = source.result || {};
		let card = _div('pharos-pv-source-card');

		let head = _div('pharos-pv-source-head');
		let title;
		if (result.url) {
			title = _btn('pharos-pv-source-title is-link', result.title || '');
			// The system browser, never an in-app tab.
			title.addEventListener('click', () => Zotero.launchURL(result.url));
		}
		else {
			title = _div('pharos-pv-source-title', result.title || '');
		}
		head.append(title);

		let bits = [];
		let authors = _authorText(result.authors);
		if (authors) {
			bits.push(authors);
		}
		if (result.year) {
			bits.push(String(result.year));
		}
		if (result.venue) {
			bits.push(result.venue);
		}
		if (bits.length) {
			head.append(_div('pharos-pv-source-meta', bits.join(' · ')));
		}
		card.append(head);

		card.append(this._renderProvenance(source, result));

		if (Zotero.Pharos.Discovery.isRules(result)) {
			// The disclosure 文献探索 makes, in the same words and in the same
			// place: a visible localized block ABOVE the content, never a footnote
			// under it. pharos-discovery-rules-note, which used to be here, is note
			// body copy -- it tells the reader to press 「精读」, a control that
			// exists only in the other window and is called 生成核心思路 there --
			// and it was rendered in the smallest, lowest-contrast text on the
			// card, below the sentences it was correcting. This id is the one
			// written for the screen, and it is also the only place either window
			// says the other half of the truth: no full text was downloaded or
			// read.
			// Two sentences: what this is, then where to replace it. The second
			// matters because the control that produces a model reading exists
			// only in the 文献探索 window -- naming a button that is not on this
			// screen is how the old copy sent readers looking for one.
			//
			// Two elements rather than one concatenated string. Joining them in
			// JS would need a separator, and the right separator is
			// language-dependent: English wants a space between sentences and
			// Chinese does not. Letting the layout do it keeps that out of the
			// code and out of the translator's hands.
			let warn = _div('pharos-pv-analysis-warning',
				_str('pharos-discovery-mode-rules-detail'));
			warn.append(_span('pharos-pv-analysis-where',
				_str('pharos-projects-source-rules-where')));
			if (result.analysis_warning) {
				// The server's own English sentence stays inspectable without being
				// the only surface the fact has.
				warn.title = String(result.analysis_warning);
			}
			card.append(warn);
		}
		else if (result.analysis_warning) {
			// Backend free text. Verbatim, no id. analyze_result nulls the warning
			// when it records a model reading, so this is not supposed to happen --
			// and if it does, it is the only thing on the card qualifying what the
			// model produced.
			card.append(_div('pharos-pv-analysis-warning', result.analysis_warning));
		}

		if (result.summary_zh) {
			card.append(_div('pharos-pv-source-insight', result.summary_zh));
		}

		// Through Discovery.trick(), never off result.core_trick directly. For a
		// rules source that field is a sentence cut out of the English abstract
		// by cue matching or -- when no cue matched -- the paper's own cleaned
		// TITLE, and printing either under an accent 核心思路 heading presents a
		// restated title as something a model distilled. The three states are the
		// three 文献探索 draws, and they are visually distinct there for exactly
		// this reason.
		let trick = Zotero.Pharos.Discovery.trick(result);
		let insight = _div('pharos-pv-source-insight is-' + trick.state);
		if (trick.state == 'extracted') {
			insight.title = _str('pharos-discovery-trick-extracted-tooltip');
		}
		insight.append(_div('pharos-pv-source-insight-label',
			_str('pharos-discovery-trick-label')));
		insight.append(_div('pharos-pv-source-insight-text', trick.text));
		card.append(insight);

		card.append(this._renderSourceNote(source));
		card.append(this._renderSourceFoot(source));
		return card;
	};

	/**
	 * Where the reading of a source came from.
	 *
	 * analysis_mode, analysis_model and sources are all in the payload and none
	 * was rendered before, so a model's summary and a rules extraction of the
	 * abstract looked identical -- RESEARCH_WORKFLOW.md §10 item 4, which asks
	 * for the analysis MODE to be shown and treats the model as the optional
	 * extra.
	 */
	this._renderProvenance = function (source, result) {
		let row = _div('pharos-pv-source-prov');

		// Through the shared helper, and unconditional. isRules() is `!= 'llm'`
		// on purpose: a mode this build does not know, a missing field or a
		// half-built object all have to read as rules, because the failure in the
		// other direction is an English abstract extract presented as a Chinese
		// AI summary. The test this replaced was `== 'rules'` at three sites and
		// gated the whole chip on a field being set, so a payload with no
		// analysis_mode rendered 核心思路 with no disclaimer and no chip at all --
		// a rules extraction with zero provenance rather than a mislabelled one.
		let rules = Zotero.Pharos.Discovery.isRules(result);
		// The mode, in words. The chip used to print result.analysis_model, so
		// 「AI 深读」 appeared only when the backend had recorded NO model and the
		// normal case was a bare `deepseek-chat` sitting in the same row as the
		// source chips -- read as a third retrieval source, with the fact that a
		// model wrote the paragraph below never stated anywhere on screen. The
		// mode is the question a reader is asking; the model id is the footnote,
		// so it follows as plain text rather than as a second capsule.
		let chip = _span('pharos-pv-analysis',
			_str(rules ? 'pharos-analysis-mode-rules' : 'pharos-analysis-mode-llm'));
		if (!rules) {
			chip.classList.add('is-ai');
		}
		row.append(chip);
		if (!rules) {
			row.append(_span('pharos-pv-analysis-model', result.analysis_model
				? _fmt('pharos-discovery-model', { model: result.analysis_model })
				: _str('pharos-discovery-model-unknown')));
		}

		for (let name of result.sources || []) {
			// Never the bare lowercase wire id: "arxiv" and "openalex" are not how
			// either project writes its own name, and sourceName() exists precisely
			// so no UI prints one.
			row.append(_span('pharos-pv-source-src',
				Zotero.Pharos.Discovery.sourceName(name)));
		}
		if (source.paper && source.paper.title) {
			// source.paper arrives in every GET; nothing here calls
			// /sources/{sid}/paper.
			row.append(_span('pharos-pv-source-paper'
				+ (source.paper.deleted_at ? ' is-deleted' : ''), source.paper.title));
		}
		return row;
	};

	this._renderSourceNote = function (source) {
		if (_noteSourceID == source.id) {
			let editor = _div('pharos-pv-note-editor');
			editor.append(_make('label', 'pharos-pv-label', _str('pharos-projects-source-note')));
			editor.append(_textarea('pharos-projects-source-note-input', _noteDraft, (v) => {
				_noteDraft = v;
			}, 3));
			let actions = _div('pharos-pv-note-actions');
			let cancel = _action(_str('pharos-projects-cancel'), null,
				() => this.closeSourceNote());
			let save = _action(
				_noteSaving ? _str('pharos-projects-saving') : _str('pharos-projects-source-note-save'),
				'is-primary',
				() => this.saveSourceNote(source.id)
			);
			save.disabled = _busy || _noteSaving;
			cancel.disabled = _noteSaving;
			actions.append(cancel, save);
			editor.append(actions);
			return editor;
		}

		let button = _btn('pharos-pv-source-note' + (source.note ? '' : ' is-empty'));
		button.append(_span('pharos-pv-source-note-label', _str('pharos-projects-source-note')));
		button.append(_span(null, source.note || _str('pharos-projects-source-note-empty')));
		button.addEventListener('click', () => this.editSourceNote(source));
		return button;
	};

	this._renderSourceFoot = function (source) {
		let foot = _div('pharos-pv-source-foot');
		let added = source.added_at || source.created_at;
		foot.append(_span('pharos-pv-source-added', added
			? _fmt('pharos-projects-source-added', { date: _date(added) })
			: ''));

		if (_confirm && _confirm.kind == 'source' && _confirm.id == source.id) {
			let confirm = _span('pharos-pv-inline-confirm');
			confirm.append(_span(null, _str('pharos-projects-source-remove-confirm')));
			let go = _action(_str('pharos-projects-source-remove'), 'is-danger',
				() => this.removeSource(source.id));
			go.disabled = _busy;
			let cancel = _action(_str('pharos-projects-cancel'), null, () => {
				_confirm = null;
				this._render();
			});
			confirm.append(go, cancel);
			foot.append(confirm);
		}
		else {
			let remove = _action(_str('pharos-projects-source-remove'), 'is-danger-text', () => {
				_confirm = { kind: 'source', id: source.id };
				this._render();
			});
			remove.disabled = _busy;
			foot.append(remove);
		}

		// Desktop-only, kept: the paper itself goes into the local library. The
		// already-filed state is read from _savedSources rather than left on the
		// element, which the next render replaces.
		let saved = _savedSources.has(_sourceKey(source));
		let save = _action(_str(saved ? 'pharos-daily-saved' : 'pharos-daily-save'), null, null);
		save.disabled = saved;
		if (!saved) {
			save.addEventListener('click', () => this.saveSource(source, save));
		}
		foot.append(save);
		return foot;
	};

	/* --------------------------------------------------------- records panel */

	this._renderArtifactsPanel = function () {
		let panel = _div('pharos-pv-panel is-artifacts');

		let head = _div('pharos-pv-panel-head');
		// Which stage's records these are. The list is filtered to the viewed
		// stage, so a head that did not name it would look like the whole project.
		head.append(_span('pharos-pv-panel-kicker', _label('stageLabel', _viewedStage)));
		// The viewed stage's count, so the number and the rows underneath it are
		// the same set -- which is what the web renders here. The project's total
		// used to sit in this slot, directly above a list filtered to one stage,
		// so a project with twelve records and one at the viewed stage read
		// 「文献探索 · 12 条研究记录」 over a single card. The whole-project number
		// is still in the sidebar row, and the per-stage breakdown on the timeline.
		head.append(_span('pharos-pv-panel-count',
			_fmt('pharos-projects-artifacts', { count: _viewedArtifacts().length })));
		let add = _action(_str('pharos-projects-artifact-new'), null, () => this.newArtifact());
		add.disabled = _busy || !!_artifactDraft;
		head.append(add);
		panel.append(head);

		// DECISIONS §9. Inside this panel and immediately above the records,
		// never at the top of the window: a record reading "95% accuracy" must
		// not be able to scroll away from the sentence saying nothing ran it.
		// Verbatim -- never through _fmt, never truncated, never in a title=.
		if (_project.automation_notice) {
			let notice = _div('pharos-pv-automation');
			notice.setAttribute('role', 'note');
			notice.append(_span('pharos-pv-automation-icon'));
			notice.append(_span('pharos-pv-automation-text', _project.automation_notice));
			panel.append(notice);
		}

		if (_artifactDraft) {
			panel.append(this._renderArtifactEditor());
		}

		let artifacts = _viewedArtifacts();
		if (!artifacts.length) {
			let empty = _div('pharos-pv-panel-empty');
			empty.append(_div('pharos-pv-panel-empty-title',
				_str('pharos-projects-artifacts-empty-title')));
			empty.append(_div('pharos-pv-panel-empty-desc',
				_str('pharos-projects-artifacts-empty-desc')));
			panel.append(empty);
			return panel;
		}

		let list = _div('pharos-pv-artifact-list');
		for (let artifact of artifacts) {
			list.append(this._renderArtifact(artifact));
		}
		panel.append(list);
		return panel;
	};

	this._renderArtifact = function (artifact) {
		let card = _div('pharos-pv-artifact-card');

		let top = _div('pharos-pv-artifact-top');
		top.append(_span('pharos-pv-artifact-type', _label('typeLabel', artifact.type)));
		// Type and stage side by side, which is why `review` must not render as a
		// bare "Review" twice: the stage is 反方审阅, the type 审阅记录.
		top.append(_span('pharos-pv-artifact-stage', _label('stageLabel', artifact.stage)));
		top.append(_span('pharos-pv-artifact-status is-' + artifact.status,
			_label('statusLabel', artifact.status)));
		card.append(top);

		card.append(_div('pharos-pv-artifact-title', artifact.title));
		if (artifact.body) {
			card.append(_div('pharos-pv-artifact-body', artifact.body));
		}

		let foot = _div('pharos-pv-artifact-foot');
		// Falling back to created_at, as the web does. ProjectArtifact.updated_at
		// defaults to NULL and is only written by a PATCH, so a record that has
		// never been edited carried no timestamp at all and there was no way to
		// tell when it had been written.
		let written = artifact.updated_at || artifact.created_at;
		foot.append(_span('pharos-pv-artifact-updated', written
			? _fmt('pharos-projects-artifact-updated', { date: _date(written) })
			: ''));

		let edit = _action(_str('pharos-projects-edit'), null, () => this.editArtifact(artifact));
		edit.disabled = _busy;
		foot.append(edit);

		if (_confirm && _confirm.kind == 'artifact' && _confirm.id == artifact.id) {
			let confirm = _span('pharos-pv-inline-confirm');
			confirm.append(_span(null, _str('pharos-projects-artifact-delete-confirm')));
			let go = _action(_str('pharos-projects-artifact-delete'), 'is-danger',
				() => this.deleteArtifact(artifact.id));
			go.disabled = _busy;
			let cancel = _action(_str('pharos-projects-cancel'), null, () => {
				_confirm = null;
				this._render();
			});
			confirm.append(go, cancel);
			foot.append(confirm);
		}
		else {
			let del = _action(_str('pharos-projects-artifact-delete'), 'is-danger-text', () => {
				_confirm = { kind: 'artifact', id: artifact.id };
				this._render();
			});
			del.disabled = _busy;
			foot.append(del);
		}

		// Read from _savedArtifacts, not from the button: saveArtifactAsNote()
		// files a new standalone note every time it is called.
		let noteSaved = _savedArtifacts.has(artifact.id);
		let save = _action(
			_str(noteSaved ? 'pharos-daily-saved' : 'pharos-projects-save-note'), null, null);
		save.disabled = noteSaved;
		if (!noteSaved) {
			save.addEventListener('click', () => this.saveArtifactNote(artifact, _project, save));
		}
		foot.append(save);

		card.append(foot);
		return card;
	};

	this._renderArtifactEditor = function () {
		let editor = _div('pharos-pv-artifact-editor');

		// Declared before the title field that toggles them: typing must not
		// re-render, or the caret goes with the rebuilt input.
		let cancel = _action(_str('pharos-projects-cancel'), null, () => this.closeArtifact());
		let save = _action(
			_artifactSaving ? _str('pharos-projects-saving') : _str('pharos-projects-artifact-save'),
			'is-primary',
			() => this.saveArtifact()
		);
		save.disabled = !_artifactDraft.title.trim() || _artifactSaving;
		cancel.disabled = _artifactSaving;

		editor.append(_div('pharos-pv-editor-head', _str(_artifactDraft.id
			? 'pharos-projects-artifact-edit-title'
			: 'pharos-projects-artifact-new-title')));

		let fields = _div('pharos-pv-editor-fields');
		fields.append(_field(_str('pharos-projects-artifact-stage'),
			_select(_stageOptions(), _artifactDraft.stage, (v) => {
				_artifactDraft.stage = v;
			})));
		fields.append(_field(_str('pharos-projects-artifact-type'),
			_select(_typeOptions(), _artifactDraft.type, (v) => {
				_artifactDraft.type = v;
			})));
		fields.append(_field(_str('pharos-projects-artifact-status'),
			_select(_statusOptions(), _artifactDraft.status, (v) => {
				_artifactDraft.status = v;
			})));
		editor.append(fields);

		editor.append(_field(_str('pharos-projects-artifact-title'),
			_input('pharos-projects-artifact-title-input', _artifactDraft.title, (v) => {
				_artifactDraft.title = v;
				save.disabled = !v.trim() || _artifactSaving;
			})));
		editor.append(_field(_str('pharos-projects-artifact-body'),
			_textarea('pharos-projects-artifact-body-input', _artifactDraft.body, (v) => {
				_artifactDraft.body = v;
			}, 6)));

		let actions = _div('pharos-pv-editor-actions');
		actions.append(cancel, save);
		editor.append(actions);
		return editor;
	};
};
