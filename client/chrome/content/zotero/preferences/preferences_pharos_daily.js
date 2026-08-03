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
 * The 每日论文 settings subpane: research directions, sweep configuration, and a
 * read-only view of the AI model in use.
 *
 * Two behaviours here are deliberate and easy to "fix" into bugs:
 *
 * 1. The RAW keyword text is what gets posted, never this file's parse of it.
 *    Keyword syntax is load-bearing -- a quoted term matches a whole word, a
 *    space-padded one matches a padded substring -- so normalising before
 *    sending would make the client the authority on matching and would match
 *    against something the user never saw. The parse is rendered back beside the
 *    box instead, so the exact terms are visible.
 *
 * 2. Limit checks warn but never block. Every ceiling mirrored from the backend
 *    is a copy, and a copy that has fallen behind must not be able to refuse
 *    something the server would accept. The only disabled-submit cases are the
 *    two the server can never accept: no name, no keywords.
 *
 * The AI model panel is read-only plus a clear button. There is no field to type
 * an API key into, because a key typed here would have to live in this process;
 * the invariant is that it exists only in the backend, encrypted. Nothing in
 * this file writes any part of the provider response to prefs or to the log.
 */
Zotero_Preferences.PharosDaily = {
	/** Server state, as last returned. Never edited in place. */
	_directions: null,
	_config: null,

	/** The pending on/off choice, or null for "unchanged". Kept beside the
	 *  other two drafts so one save carries all three. */
	_enabledDraft: null,
	_provider: null,

	/** Which row the editor is open on: null, 'new', or a direction id. */
	_editing: null,

	/** Two-step delete, matching "Sign Out Everywhere" on the account pane. */
	_confirmDelete: null,

	/**
	 * Drafts are null until the user types, and each field renders
	 * `draft ?? server`. Deliberately not state seeded from the response plus an
	 * effect to re-seed it: that version clobbers what is being typed on every
	 * refetch. A successful save clears the draft and the field follows the
	 * server again -- which is also how the user gets shown the canonical
	 * spelling the server settled on ("CS.ro" comes back as "cs.RO").
	 */
	_catDraft: null,
	_maxDraft: null,

	init: async function () {
		this._list = document.getElementById('pharos-daily-prefs-list');
		this._status = document.getElementById('pharos-daily-prefs-status');
		this._editor = document.getElementById('pharos-daily-prefs-editor');
		this._editorHome = document.getElementById('pharos-daily-prefs-editor-home');
		this._nameField = document.getElementById('pharos-daily-prefs-name');
		this._keywordsField = document.getElementById('pharos-daily-prefs-keywords');
		this._catField = document.getElementById('pharos-daily-prefs-categories');
		this._maxField = document.getElementById('pharos-daily-prefs-max');

		// Signed out is not an error state, and the pane must say so rather than
		// sit blank or throw at every request.
		if (!Zotero.Pharos.API.hasCredentials()) {
			document.getElementById('pharos-daily-prefs-signed-out').hidden = false;
			document.getElementById('pharos-daily-prefs-body').hidden = true;
			return;
		}
		document.getElementById('pharos-daily-prefs-signed-out').hidden = true;
		document.getElementById('pharos-daily-prefs-body').hidden = false;

		this._keywordsField.addEventListener('input', () => this._renderParse());
		this._nameField.addEventListener('input', () => this._renderParse());
		this._nameField.addEventListener('keypress', (event) => {
			// Enter in the single-line field submits, which is what a one-line
			// form is expected to do. The textarea is left alone -- Enter there
			// is how you start the next keyword.
			if (event.key == 'Enter') {
				event.preventDefault();
				this.submitEditor();
			}
		});

		this._catField.addEventListener('input', () => {
			this._catDraft = this._catField.value;
			this._renderConfig();
		});
		this._maxField.addEventListener('input', () => {
			this._maxDraft = this._maxField.value;
			this._renderConfig();
		});

		// Independent of each other, and a failure in one must not blank the
		// others: someone whose model is misconfigured still needs to be able to
		// edit their directions.
		await Promise.all([
			this._loadDirections(),
			this._loadConfig(),
			this._loadProvider(),
		]);
	},


	//
	// Directions
	//

	_loadDirections: async function () {
		this._setStatus(Zotero.getString('pharos-prefs-daily-loading'));
		try {
			this._directions = await Zotero.Pharos.Directions.list();
			this._setStatus('');
		}
		catch (e) {
			Zotero.logError(e);
			this._directions = null;
			this._setStatus(e.message || Zotero.getString('pharos-prefs-daily-load-failed'));
		}
		this._renderDirections();
	},

	_setStatus: function (text) {
		this._status.textContent = text || '';
		this._status.hidden = !text;
	},

	_renderDirections: function () {
		let rows = this._directions;
		let empty = document.getElementById('pharos-daily-prefs-empty');
		let count = document.getElementById('pharos-daily-prefs-count');
		let addButton = document.getElementById('pharos-daily-prefs-add');
		let orderHelp = document.getElementById('pharos-daily-prefs-order-help');

		// The editor is a live node carrying the half-typed draft; park it back
		// home before the list is emptied, or replaceChildren() takes it with
		// the rows and the draft is gone.
		this._editorHome.append(this._editor);
		this._editor.hidden = this._editing === null;
		this._list.replaceChildren();

		if (!rows) {
			empty.hidden = true;
			orderHelp.hidden = true;
			count.textContent = '';
			addButton.disabled = true;
			return;
		}

		addButton.disabled = this._editing !== null || rows.length >= Zotero.Pharos.Directions.LIMITS.directions;
		empty.hidden = rows.length > 0 || this._editing == 'new';
		orderHelp.hidden = rows.length < 2;
		count.textContent = rows.length
			? Zotero.ftl.formatValueSync('pharos-prefs-daily-count', {
				count: rows.length,
				max: Zotero.Pharos.Directions.LIMITS.directions,
			})
			: '';

		for (let i = 0; i < rows.length; i++) {
			// The editor stands in for the row it belongs to, so an edit happens
			// where the user is looking rather than at the bottom of the list.
			if (this._editing == rows[i].id) {
				this._list.append(this._editor);
			}
			else {
				this._list.append(this._renderRow(rows[i], i));
			}
		}

		if (this._editing == 'new') {
			this._list.append(this._editor);
		}
	},

	_renderRow: function (direction, index) {
		let row = document.createElement('div');
		row.className = 'pharos-daily-prefs-row';
		row.dataset.directionId = direction.id;
		if (!direction.enabled) {
			row.classList.add('pharos-daily-prefs-row-off');
		}

		// Up/down rather than drag and drop. Position is not decoration -- it is
		// the tie-break when a paper matches several directions -- so two buttons
		// that move exactly one step are both precise and keyboard-reachable,
		// which a half-built drag would be neither.
		let order = document.createElement('div');
		order.className = 'pharos-daily-prefs-order';
		let up = this._button('pharos-prefs-daily-move-up', () => this.move(index, -1));
		up.classList.add('pharos-daily-prefs-move');
		up.disabled = index == 0;
		let down = this._button('pharos-prefs-daily-move-down', () => this.move(index, 1));
		down.classList.add('pharos-daily-prefs-move');
		down.disabled = index == this._directions.length - 1;
		order.append(up, down);
		row.append(order);

		let main = document.createElement('div');
		main.className = 'pharos-daily-prefs-main';

		let name = document.createElement('div');
		name.className = 'pharos-daily-prefs-name';
		name.textContent = direction.name;
		main.append(name);

		let keywords = document.createElement('div');
		keywords.className = 'pharos-daily-prefs-keywords';
		// The exact stored terms, so what is matched is never a mystery. Whole-word
		// terms keep their quotes; padded ones keep a visible marker for the space.
		keywords.append(...direction.keywords.map((keyword) => {
			let chip = document.createElement('span');
			chip.className = 'pharos-daily-prefs-chip';
			if (Zotero.Pharos.Directions.isWholeWord(keyword)) {
				chip.classList.add('pharos-daily-prefs-chip-word');
				chip.title = Zotero.getString('pharos-prefs-daily-chip-word');
			}
			chip.textContent = Zotero.Pharos.Directions.displayKeyword(keyword);
			return chip;
		}));
		main.append(keywords);
		row.append(main);

		let actions = document.createElement('div');
		actions.className = 'pharos-daily-prefs-actions';

		let toggle = this._button(
			direction.enabled ? 'pharos-prefs-daily-enabled' : 'pharos-prefs-daily-disabled',
			() => this.setEnabled(direction.id, !direction.enabled)
		);
		toggle.setAttribute('aria-pressed', String(!!direction.enabled));
		actions.append(toggle);

		actions.append(this._button('pharos-prefs-daily-edit', () => this.startEdit(direction.id)));

		if (this._confirmDelete == direction.id) {
			let confirmButton = this._button(
				'pharos-prefs-daily-delete-confirm',
				() => this.remove(direction.id, confirmButton)
			);
			confirmButton.classList.add('pharos-daily-prefs-danger');
			actions.append(confirmButton);
		}
		else {
			actions.append(this._button('pharos-prefs-daily-delete', () => {
				this._confirmDelete = direction.id;
				this._renderDirections();
			}));
		}

		row.append(actions);
		return row;
	},

	/**
	 * An HTML button carrying a plain string. HTML rather than XUL because the
	 * labels here change while a request is in flight, and a XUL button renders
	 * its `label` attribute rather than its text.
	 */
	_button: function (stringID, onClick) {
		let button = document.createElement('button');
		button.type = 'button';
		button.textContent = Zotero.getString(stringID);
		button.addEventListener('click', onClick);
		return button;
	},


	//
	// The editor
	//

	startCreate: function () {
		this._editing = 'new';
		this._confirmDelete = null;
		this._nameField.value = '';
		this._keywordsField.value = '';
		this._setEditorError('');
		document.getElementById('pharos-daily-prefs-submit').textContent
			= Zotero.getString('pharos-prefs-daily-create');
		this._renderDirections();
		this._renderParse();
		this._nameField.focus();
	},

	startEdit: function (directionID) {
		let direction = (this._directions || []).find(d => d.id == directionID);
		if (!direction) {
			return;
		}
		this._editing = directionID;
		this._confirmDelete = null;
		this._nameField.value = direction.name;
		// One per line, which is also how the backend stores them. Joining with
		// commas would be lossy: a term is allowed to contain a comma only
		// because the newline form exists.
		this._keywordsField.value = direction.keywords.join('\n');
		this._setEditorError('');
		document.getElementById('pharos-daily-prefs-submit').textContent
			= Zotero.getString('pharos-prefs-daily-save');
		this._renderDirections();
		this._renderParse();
		this._nameField.focus();
	},

	cancelEditor: function () {
		this._editing = null;
		this._setEditorError('');
		// _renderDirections() is the only thing that hides or shows the editor,
		// deriving it from _editing, so there is one answer to "is it open".
		this._renderDirections();
	},

	_setEditorError: function (text) {
		let node = document.getElementById('pharos-daily-prefs-editor-error');
		node.textContent = text || '';
		node.hidden = !text;
	},

	/**
	 * Render the parse of the keyword box back to the user, plus any advisory
	 * warnings, and settle whether the submit button can do anything.
	 */
	_renderParse: function () {
		let limits = Zotero.Pharos.Directions.LIMITS;
		let terms = Zotero.Pharos.Directions.parseKeywords(this._keywordsField.value);
		let head = document.getElementById('pharos-daily-prefs-parsed-head');
		let chips = document.getElementById('pharos-daily-prefs-chips');

		head.textContent = terms.length
			? Zotero.ftl.formatValueSync('pharos-prefs-daily-parsed-count', { count: terms.length })
			: Zotero.getString('pharos-prefs-daily-parsed-none');

		chips.replaceChildren(...terms.map((term) => {
			let chip = document.createElement('span');
			chip.className = 'pharos-daily-prefs-chip';
			let wholeWord = Zotero.Pharos.Directions.isWholeWord(term);
			chip.classList.add(wholeWord
				? 'pharos-daily-prefs-chip-word'
				: 'pharos-daily-prefs-chip-substring');
			chip.title = Zotero.getString(wholeWord
				? 'pharos-prefs-daily-chip-word'
				: 'pharos-prefs-daily-chip-substring');
			if (term.length > limits.keywordChars) {
				chip.classList.add('pharos-daily-prefs-chip-bad');
			}
			chip.textContent = Zotero.Pharos.Directions.displayKeyword(term);
			return chip;
		}));

		let warnings = [];
		if (terms.length > limits.keywords) {
			warnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-warn-keyword-count', {
				max: limits.keywords,
				count: terms.length,
			}));
		}
		let totalChars = terms.reduce((n, term) => n + term.length, 0);
		if (totalChars > limits.keywordsTotalChars) {
			warnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-warn-keyword-total', {
				max: limits.keywordsTotalChars,
				count: totalChars,
			}));
		}
		let tooLong = terms.filter(term => term.length > limits.keywordChars).length;
		if (tooLong) {
			warnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-warn-keyword-long', {
				count: tooLong,
				max: limits.keywordChars,
			}));
		}
		this._setWarnings(document.getElementById('pharos-daily-prefs-editor-warnings'), warnings);

		// The only two blocks, and both are requests the server cannot satisfy:
		// a direction needs a name, and one with no keywords matches nothing
		// while looking, in this list, exactly like one that works.
		document.getElementById('pharos-daily-prefs-submit').disabled
			= !this._nameField.value.trim() || !terms.length;
	},

	_setWarnings: function (container, messages) {
		container.replaceChildren(...messages.map((message) => {
			let line = document.createElement('div');
			line.textContent = message;
			return line;
		}));
		container.hidden = !messages.length;
	},

	submitEditor: async function () {
		if (this._editing === null) {
			return;
		}
		let name = this._nameField.value.trim();
		// The raw text, not the parse. See the file comment: the backend's parse
		// is the one that gets stored, and posting ours instead would make this
		// file the authority on matching.
		let keywords = this._keywordsField.value;
		if (!name || !Zotero.Pharos.Directions.parseKeywords(keywords).length) {
			return;
		}

		let submit = document.getElementById('pharos-daily-prefs-submit');
		let original = submit.textContent;
		submit.disabled = true;
		submit.textContent = Zotero.getString('pharos-prefs-daily-saving');
		this._setEditorError('');
		try {
			if (this._editing == 'new') {
				await Zotero.Pharos.Directions.create({ name, keywords });
			}
			else {
				await Zotero.Pharos.Directions.update(this._editing, { name, keywords });
			}
			this._editing = null;
			await this._loadDirections();
		}
		catch (e) {
			Zotero.logError(e);
			// e.message carries the backend's own `detail`, which is far more use
			// than a status code: it names the limit that was breached.
			this._setEditorError(e.message || Zotero.getString('pharos-prefs-daily-load-failed'));
			submit.textContent = original;
			submit.disabled = false;
		}
	},


	//
	// Row actions
	//

	setEnabled: async function (directionID, enabled) {
		try {
			await Zotero.Pharos.Directions.update(directionID, { enabled });
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-prefs-daily-load-failed'));
			return;
		}
		await this._loadDirections();
	},

	remove: async function (directionID, button) {
		if (button) {
			button.disabled = true;
			button.textContent = Zotero.getString('pharos-prefs-daily-deleting');
		}
		try {
			await Zotero.Pharos.Directions.remove(directionID);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-prefs-daily-load-failed'));
			if (button) {
				button.disabled = false;
				button.textContent = Zotero.getString('pharos-prefs-daily-delete-confirm');
			}
			return;
		}
		this._confirmDelete = null;
		if (this._editing == directionID) {
			this._editing = null;
		}
		await this._loadDirections();
	},

	move: async function (index, delta) {
		if (!this._directions) {
			return;
		}
		let target = index + delta;
		if (target < 0 || target >= this._directions.length) {
			return;
		}
		let ids = this._directions.map(d => d.id);
		let [moved] = ids.splice(index, 1);
		ids.splice(target, 0, moved);
		try {
			// The reorder endpoint answers with the new order, so there is no
			// second request and no window in which the list disagrees with the
			// server about what it just did.
			this._directions = await Zotero.Pharos.Directions.reorder(ids);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-prefs-daily-load-failed'));
			return;
		}
		this._renderDirections();
	},

	restoreDefaults: async function () {
		let button = document.getElementById('pharos-daily-prefs-restore');
		button.disabled = true;
		button.textContent = Zotero.getString('pharos-prefs-daily-restoring');
		let added = 0;
		try {
			added = await Zotero.Pharos.Directions.restoreDefaults();
		}
		catch (e) {
			Zotero.logError(e);
		}
		button.textContent = Zotero.getString('pharos-prefs-daily-restore');
		button.disabled = false;
		await this._loadDirections();
		if (!added) {
			this._setStatus(Zotero.getString('pharos-prefs-daily-restore-none'));
		}
	},


	//
	// Sweep configuration
	//

	_loadConfig: async function () {
		try {
			this._config = await Zotero.Pharos.Directions.getConfig();
			this._setConfigError('');
		}
		catch (e) {
			Zotero.logError(e);
			this._config = null;
			this._setConfigError(e.message || Zotero.getString('pharos-prefs-daily-config-failed'));
		}
		this._renderConfig();
	},

	_setConfigError: function (text) {
		let node = document.getElementById('pharos-daily-prefs-config-error');
		node.textContent = text || '';
		node.hidden = !text;
	},

	_renderConfig: function () {
		let limits = Zotero.Pharos.Directions.LIMITS;
		let catText = this._catDraft !== null
			? this._catDraft
			: (this._config ? this._config.categories.join(', ') : '');
		let maxText = this._maxDraft !== null
			? this._maxDraft
			: (this._config ? String(this._config.max_per_day) : '');

		if (this._catField.value !== catText) {
			this._catField.value = catText;
		}
		if (this._maxField.value !== maxText) {
			this._maxField.value = maxText;
		}
		this._catField.disabled = !this._config;
		this._maxField.disabled = !this._config;

		let enabledBox = document.getElementById('pharos-daily-prefs-enabled');
		let enabled = this._enabledDraft !== null
			? this._enabledDraft
			// Absent resolves to ON, matching the digest window: a config that
			// could not be read is not a config that is off.
			: (this._config ? this._config.enabled !== false : true);
		if (enabledBox.checked !== enabled) {
			enabledBox.checked = enabled;
		}
		enabledBox.disabled = !this._config;

		document.getElementById('pharos-daily-prefs-categories-help').textContent
			= Zotero.ftl.formatValueSync('pharos-prefs-daily-categories-help', {
				max: limits.categories,
			});
		document.getElementById('pharos-daily-prefs-max-help').textContent
			= Zotero.ftl.formatValueSync('pharos-prefs-daily-max-help', {
				min: limits.minPerDay,
				max: limits.maxPerDay,
			});

		let parsed = Zotero.Pharos.Directions.parseCategories(catText);
		let chips = document.getElementById('pharos-daily-prefs-category-chips');
		chips.replaceChildren(...parsed.categories.map((category) => {
			let chip = document.createElement('span');
			chip.className = 'pharos-daily-prefs-chip';
			chip.textContent = category;
			return chip;
		}));

		let catWarnings = [];
		// Shape-checked here for immediate feedback only, and the save stays
		// enabled: this regex is a copy, and a copy that has fallen behind arXiv
		// must not refuse a category the server would accept.
		if (parsed.invalid.length) {
			catWarnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-categories-invalid', {
				list: parsed.invalid.join(', '),
			}));
		}
		if (parsed.categories.length > limits.categories) {
			catWarnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-categories-too-many', {
				max: limits.categories,
				count: parsed.categories.length,
			}));
		}
		this._setWarnings(document.getElementById('pharos-daily-prefs-categories-warn'), catWarnings);

		let maxWarnings = [];
		let maxState = this._readMax(maxText);
		if (maxState.unparseable || maxState.outOfRange) {
			maxWarnings.push(Zotero.ftl.formatValueSync('pharos-prefs-daily-max-range', {
				min: limits.minPerDay,
				max: limits.maxPerDay,
			}));
		}
		let dirty = this._catDraft !== null || this._maxDraft !== null
			|| this._enabledDraft !== null;
		// A cleared box reads as "leave this alone", which is exactly what the
		// request will do. Saying so beats letting an empty field look broken.
		if (maxState.blank && dirty) {
			maxWarnings.push(Zotero.getString('pharos-prefs-daily-max-blank'));
		}
		this._setWarnings(document.getElementById('pharos-daily-prefs-max-warn'), maxWarnings);

		document.getElementById('pharos-daily-prefs-config-save').disabled = !dirty || !this._config;
		document.getElementById('pharos-daily-prefs-config-revert').hidden = !dirty;
	},

	/**
	 * Read the daily-limit box.
	 *
	 * Blank is checked first and on its own. `Number('')` is 0 and
	 * `Number.isInteger(0)` is true, so testing the parse alone would post
	 * `max_per_day: 0` for a box the user merely cleared -- a 400 from the
	 * backend for something they never typed.
	 */
	_readMax: function (text) {
		let limits = Zotero.Pharos.Directions.LIMITS;
		let trimmed = String(text).trim();
		if (!trimmed) {
			return { blank: true, valid: false, unparseable: false, outOfRange: false, value: null };
		}
		let value = Number(trimmed);
		if (!Number.isInteger(value)) {
			return { blank: false, valid: false, unparseable: true, outOfRange: false, value: null };
		}
		let outOfRange = value < limits.minPerDay || value > limits.maxPerDay;
		return { blank: false, valid: true, unparseable: false, outOfRange, value };
	},

	/** The checkbox has no text to diff, so its change handler records the
	 *  choice and lets _renderConfig() do the rest. */
	markConfigDirty: function () {
		this._enabledDraft = document.getElementById('pharos-daily-prefs-enabled').checked;
		this._setConfigError('');
		document.getElementById('pharos-daily-prefs-config-status').textContent = '';
		this._renderConfig();
	},

	revertConfig: function () {
		this._catDraft = null;
		this._maxDraft = null;
		this._enabledDraft = null;
		this._setConfigError('');
		document.getElementById('pharos-daily-prefs-config-status').textContent = '';
		this._renderConfig();
	},

	saveConfig: async function () {
		let button = document.getElementById('pharos-daily-prefs-config-save');
		let statusNode = document.getElementById('pharos-daily-prefs-config-status');
		let changes = {};
		if (this._catDraft !== null) {
			// The raw text again, so the server does the canonicalising and gets
			// to be the one that decides what a category is.
			changes.categories = this._catDraft;
		}
		if (this._maxDraft !== null) {
			let maxState = this._readMax(this._maxDraft);
			if (maxState.valid) {
				changes.max_per_day = maxState.value;
			}
			// Blank or unparseable: the key is omitted entirely rather than sent
			// as 0 or NaN.
		}
		if (this._enabledDraft !== null) {
			changes.enabled = this._enabledDraft;
		}
		if (!Object.keys(changes).length) {
			this.revertConfig();
			return;
		}

		let original = button.textContent;
		button.disabled = true;
		button.textContent = Zotero.getString('pharos-prefs-daily-saving');
		statusNode.textContent = '';
		this._setConfigError('');
		try {
			this._config = await Zotero.Pharos.Directions.updateConfig(changes);
			this._catDraft = null;
			this._maxDraft = null;
			this._enabledDraft = null;
			statusNode.textContent = Zotero.getString('pharos-prefs-daily-config-saved');
		}
		catch (e) {
			Zotero.logError(e);
			this._setConfigError(e.message || Zotero.getString('pharos-prefs-daily-config-failed'));
		}
		button.textContent = original;
		this._renderConfig();
	},


	//
	// AI model
	//

	_loadProvider: async function () {
		let statusNode = document.getElementById('pharos-daily-prefs-provider-status');
		statusNode.textContent = Zotero.getString('pharos-prefs-provider-loading');
		try {
			// Straight through the API client rather than a service module: this
			// is one read and one delete, used by exactly this screen. The
			// response carries no key -- ProviderStatusOut has no such field --
			// and nothing below persists any of it.
			this._provider = await Zotero.Pharos.API.request('GET', '/api/ai/provider');
			statusNode.textContent = '';
		}
		catch (e) {
			Zotero.logError(e);
			this._provider = null;
			statusNode.textContent = e.message || Zotero.getString('pharos-prefs-provider-failed');
		}
		this._renderProvider();
	},

	_renderProvider: function () {
		let panel = document.getElementById('pharos-daily-prefs-provider');
		let provider = this._provider;
		panel.hidden = !provider;
		if (!provider) {
			return;
		}

		const SOURCE_STRINGS = {
			personal: 'pharos-prefs-provider-source-personal',
			server: 'pharos-prefs-provider-source-server',
			none: 'pharos-prefs-provider-source-none',
		};
		document.getElementById('pharos-daily-prefs-provider-source').textContent
			= Zotero.getString(SOURCE_STRINGS[provider.source] || SOURCE_STRINGS.none);
		// An em dash for an unconfigured field rather than an empty cell, so the
		// row still reads as a row.
		document.getElementById('pharos-daily-prefs-provider-base-url').textContent
			= provider.baseUrl || '—';
		document.getElementById('pharos-daily-prefs-provider-model').textContent
			= provider.model || '—';
		document.getElementById('pharos-daily-prefs-provider-temperature').textContent
			= provider.temperature === undefined || provider.temperature === null
				? '—'
				: String(provider.temperature);
		document.getElementById('pharos-daily-prefs-provider-max-tokens').textContent
			= provider.maxOutputTokens === undefined || provider.maxOutputTokens === null
				? '—'
				: String(provider.maxOutputTokens);

		let keyNode = document.getElementById('pharos-daily-prefs-provider-key');
		if (provider.canStoreCredential === false) {
			keyNode.textContent = Zotero.getString('pharos-prefs-provider-key-unsupported');
		}
		else {
			keyNode.textContent = Zotero.getString(provider.hasCredential
				? 'pharos-prefs-provider-key-stored'
				: 'pharos-prefs-provider-key-none');
		}

		// Clearing removes the PERSONAL provider. Offering it against a server
		// provider would be a button that appears to do something and does not.
		let clear = document.getElementById('pharos-daily-prefs-provider-clear');
		clear.hidden = provider.source != 'personal';
		clear.disabled = false;
		clear.textContent = Zotero.getString('pharos-prefs-provider-clear');
	},

	clearProvider: async function () {
		let confirmed = Services.prompt.confirm(
			window,
			Zotero.getString('pharos-prefs-provider-header'),
			Zotero.getString('pharos-prefs-provider-clear-confirm')
		);
		if (!confirmed) {
			return;
		}

		let button = document.getElementById('pharos-daily-prefs-provider-clear');
		let message = document.getElementById('pharos-daily-prefs-provider-message');
		button.disabled = true;
		button.textContent = Zotero.getString('pharos-prefs-provider-clearing');
		message.textContent = '';
		try {
			await Zotero.Pharos.API.request('DELETE', '/api/ai/provider');
		}
		catch (e) {
			Zotero.logError(e);
			message.textContent = e.message || Zotero.getString('pharos-prefs-provider-clear-failed');
			button.disabled = false;
			button.textContent = Zotero.getString('pharos-prefs-provider-clear');
			return;
		}
		// Re-read rather than assume: clearing a personal provider can leave the
		// account on the server's model, and which of the two happened is the
		// server's answer to give.
		await this._loadProvider();
		message.textContent = Zotero.getString(
			this._provider && this._provider.source == 'server'
				? 'pharos-prefs-provider-cleared-server'
				: 'pharos-prefs-provider-cleared'
		);
	},
};
