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
 * The administrator console.
 *
 * A module beside the library rather than a separate app: an operator is also a
 * researcher, so promoting a colleague must not mean leaving the workbench.
 *
 * Nothing here is a security boundary. Every request the console makes is gated
 * on `require_admin` server-side; hiding the module from ordinary accounts, and
 * the check in _init(), only keep people from being shown a screen where every
 * button would 403.
 */
var Zotero_Pharos_Admin = new function () {
	let _resolveInit;
	let _searchTimer = null;
	let _dialog = null;

	/** The signed-in operator. Their own row withholds the actions the backend
	 *  refuses anyway, rather than offering buttons that always error. */
	let _me = null;

	/** See pharosDaily.js for why this is created here rather than in onload. */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});

	/**
	 * A Fluent string with arguments.
	 *
	 * Not Zotero.getString(): handed params, that routes to the .properties
	 * bundle, where none of these ids exist. Fluent's own formatter is what
	 * reads pharos.ftl.
	 */
	function _fmt(id, args) {
		return Zotero.ftl.formatValueSync(id, args);
	}

	/** "2026-07-29" -- the console cares about the day, not the minute. */
	function _day(iso) {
		return iso ? String(iso).slice(0, 10) : Zotero.getString('pharos-admin-none');
	}

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		this._status = document.getElementById('pharos-admin-status');
		this._body = document.getElementById('pharos-admin-body');
		this._search = document.getElementById('pharos-admin-search');
		this._tabs = {
			users: document.getElementById('pharos-admin-tab-users'),
			providers: document.getElementById('pharos-admin-tab-providers'),
		};

		for (let [name, button] of Object.entries(this._tabs)) {
			button.addEventListener('click', () => this.selectTab(name));
		}

		// Typing filters as you go, but one request per keystroke would hammer
		// the server for results nobody reads; Enter skips the wait.
		this._search.addEventListener('input', () => {
			clearTimeout(_searchTimer);
			_searchTimer = setTimeout(() => this.loadUsers(), 250);
		});
		this._search.addEventListener('keydown', (event) => {
			if (event.key == 'Enter') {
				event.preventDefault();
				clearTimeout(_searchTimer);
				this.loadUsers();
			}
		});

		this._selectTabButton('users');

		if (!Zotero.Pharos.API.hasCredentials()) {
			this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
			this._setEnabled(false);
			return;
		}

		// A second gate on top of not showing the module: this window has a
		// chrome URL, so it can be opened directly. Cheaper than letting every
		// panel render a wall of 403s.
		try {
			_me = await Zotero.Pharos.Admin.identify();
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-admin-error'));
			this._setEnabled(false);
			return;
		}
		if (!_me) {
			this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
			this._setEnabled(false);
			return;
		}
		if (!_me.is_admin) {
			this._setStatus(Zotero.getString('pharos-admin-forbidden'));
			this._setEnabled(false);
			return;
		}

		await this.loadUsers();
	};

	this.destroy = function () {
		clearTimeout(_searchTimer);
		_searchTimer = null;
		// Otherwise the dialog's document-level key handler outlives the window.
		this._closeDialog();
	};

	this._setStatus = function (text) {
		this._status.textContent = text || '';
		this._status.hidden = !text;
	};

	this._setEnabled = function (enabled) {
		this._search.disabled = !enabled;
		for (let button of Object.values(this._tabs)) {
			button.disabled = !enabled;
		}
	};

	this._selectTabButton = function (name) {
		for (let [key, button] of Object.entries(this._tabs)) {
			button.classList.toggle('is-on', key == name);
			button.setAttribute('aria-selected', key == name);
		}
		// The search box belongs to the user list; leaving it visible over the
		// provider list would suggest it filters that too.
		this._search.hidden = name != 'users';
	};

	this.selectTab = function (name) {
		this._selectTabButton(name);
		return name == 'users' ? this.loadUsers() : this.loadProviders();
	};


	//
	// Users
	//

	this.loadUsers = async function () {
		this._setStatus(Zotero.getString('pharos-admin-loading'));
		try {
			// Stats and the page together: the header counts are the context the
			// list is read in, and fetching them separately would let the two
			// disagree after a deletion.
			let [stats, page] = await Promise.all([
				Zotero.Pharos.Admin.getStats(),
				Zotero.Pharos.Admin.listUsers({ query: this._search.value }),
			]);
			this._renderUsers(stats, page);
		}
		catch (e) {
			Zotero.logError(e);
			this._body.replaceChildren();
			this._setStatus(e.message || Zotero.getString('pharos-admin-error'));
		}
	};

	this._renderUsers = function (stats, page) {
		this._setStatus('');
		this._body.replaceChildren();
		this._body.append(this._renderStats(stats));

		if (!page.users.length) {
			let empty = document.createElement('div');
			empty.className = 'pharos-admin-empty';
			empty.textContent = Zotero.getString(this._search.value.trim()
				? 'pharos-admin-users-none-matched'
				: 'pharos-admin-users-empty');
			this._body.append(empty);
			return;
		}

		let table = document.createElement('table');
		table.className = 'pharos-admin-table';

		const COLUMNS = [
			['pharos-admin-column-user', ''],
			['pharos-admin-column-created', ''],
			['pharos-admin-column-last-login', ''],
			['pharos-admin-column-role', ''],
			['pharos-admin-column-actions', ''],
		];
		let head = document.createElement('thead');
		let headRow = document.createElement('tr');
		for (let [id, className] of COLUMNS) {
			let cell = document.createElement('th');
			cell.textContent = Zotero.getString(id);
			if (className) {
				cell.className = className;
			}
			headRow.append(cell);
		}
		head.append(headRow);
		table.append(head);

		let tbody = document.createElement('tbody');
		for (let user of page.users) {
			tbody.append(this._renderUserRow(user));
		}
		table.append(tbody);
		this._body.append(table);

		// The backend caps a page at 200. Saying so beats letting an operator
		// conclude the 201st account does not exist.
		if (page.total > page.users.length) {
			let note = document.createElement('div');
			note.className = 'pharos-admin-empty';
			note.textContent = _fmt('pharos-admin-users-truncated', {
				shown: page.users.length,
				total: page.total,
			});
			this._body.append(note);
		}
	};

	this._renderStats = function (stats) {
		let wrap = document.createElement('div');
		wrap.className = 'pharos-admin-stats';

		let card = (label, value) => {
			let box = document.createElement('div');
			box.className = 'pharos-admin-stat';
			let number = document.createElement('div');
			number.className = 'pharos-admin-stat-value';
			number.textContent = value;
			let name = document.createElement('div');
			name.className = 'pharos-admin-stat-label';
			name.textContent = label;
			box.append(number, name);
			return box;
		};

		wrap.append(
			card(Zotero.getString('pharos-admin-stat-users'), stats.users),
			card(Zotero.getString('pharos-admin-stat-admins'), stats.admins),
			card(Zotero.getString('pharos-admin-stat-inactive'), stats.inactive_users)
		);

		let registration = document.createElement('div');
		registration.className = 'pharos-admin-registration';
		registration.textContent = Zotero.getString(stats.allow_registration
			? 'pharos-admin-registration-open'
			: 'pharos-admin-registration-closed');
		let hint = document.createElement('span');
		hint.className = 'pharos-admin-registration-hint';
		hint.textContent = Zotero.getString('pharos-admin-registration-hint');
		registration.append(hint);
		wrap.append(registration);

		return wrap;
	};

	this._renderUserRow = function (user) {
		let row = document.createElement('tr');
		if (!user.is_active) {
			row.classList.add('is-suspended');
		}

		let identity = document.createElement('td');
		let name = document.createElement('div');
		name.className = 'pharos-admin-username';
		// An account that never set a display name is still a person; the local
		// part of the address is the least misleading stand-in.
		name.textContent = (user.display_name || '').trim() || user.email.split('@')[0];
		if (_me && user.id == _me.id) {
			let badge = document.createElement('span');
			badge.className = 'pharos-admin-self';
			badge.textContent = Zotero.getString('pharos-admin-self');
			name.append(badge);
		}
		let email = document.createElement('div');
		email.className = 'pharos-admin-useremail';
		email.textContent = user.email;
		identity.append(name, email);
		row.append(identity);

		for (let date of [user.created_at, user.last_login_at]) {
			let cell = document.createElement('td');
			cell.className = 'pharos-admin-date';
			cell.textContent = _day(date);
			row.append(cell);
		}

		let role = document.createElement('td');
		let badge = document.createElement('span');
		badge.className = 'pharos-admin-badge '
			+ (user.is_admin ? 'is-admin' : 'is-plain');
		badge.textContent = Zotero.getString(user.is_admin
			? 'pharos-admin-role-admin'
			: 'pharos-admin-role-user');
		role.append(badge);
		if (!user.is_active) {
			let suspended = document.createElement('span');
			suspended.className = 'pharos-admin-badge is-suspended';
			suspended.textContent = Zotero.getString('pharos-admin-suspended');
			role.append(suspended);
		}
		row.append(role);

		row.append(this._renderUserActions(user));
		return row;
	};

	this._renderUserActions = function (user) {
		let cell = document.createElement('td');
		let actions = document.createElement('div');
		actions.className = 'pharos-admin-actions';

		// Withheld on your own row rather than shown and rejected: the backend
		// refuses all three, and a button that always errors is worse than no
		// button.
		if (_me && user.id == _me.id) {
			let note = document.createElement('span');
			note.className = 'pharos-admin-selfnote';
			note.textContent = Zotero.getString('pharos-admin-self-note');
			actions.append(note);
			cell.append(actions);
			return cell;
		}

		let role = document.createElement('button');
		role.textContent = Zotero.getString(user.is_admin
			? 'pharos-admin-demote'
			: 'pharos-admin-promote');
		role.addEventListener('click', () => {
			this.patch(user, { isAdmin: !user.is_admin }, actions);
		});

		let active = document.createElement('button');
		active.textContent = Zotero.getString(user.is_active
			? 'pharos-admin-deactivate'
			: 'pharos-admin-activate');
		if (user.is_active) {
			active.className = 'pharos-admin-danger';
		}
		active.addEventListener('click', () => {
			this.patch(user, { isActive: !user.is_active }, actions);
		});

		let remove = document.createElement('button');
		remove.className = 'pharos-admin-danger';
		remove.textContent = Zotero.getString('pharos-admin-delete');
		// Which account this deletes is the row's most important fact and the
		// button's label cannot carry it, so the tooltip does.
		remove.title = _fmt('pharos-admin-delete-tooltip', { email: user.email }) || '';
		remove.addEventListener('click', () => this.confirmDelete(user));

		actions.append(role, active, remove);
		cell.append(actions);
		return cell;
	};

	/**
	 * Apply a change to one account and reload.
	 *
	 * @param {Object} user
	 * @param {Object} patch - see Zotero.Pharos.Admin.updateUser
	 * @param {Element} actions - the row's action group, disabled while in flight
	 */
	this.patch = async function (user, patch, actions) {
		let buttons = Array.from(actions.querySelectorAll('button'));
		buttons.forEach(button => (button.disabled = true));
		try {
			await Zotero.Pharos.Admin.updateUser(user.id, patch);
			await this.loadUsers();
		}
		catch (e) {
			Zotero.logError(e);
			// The backend refuses the lockout cases with a 409 and an explanatory
			// message; showing it verbatim is more useful than a generic failure.
			this._setStatus(e.message || Zotero.getString('pharos-admin-update-failed'));
			buttons.forEach(button => (button.disabled = false));
		}
	};


	//
	// Deletion
	//

	/**
	 * Ask for the account's email address before deleting it.
	 *
	 * Deleting an account destroys its Pharos server-side account data, and that
	 * cannot be undone. It does not inspect or alter the user's local Zotero or
	 * Pharos library. The confirmation asks the operator to type the address
	 * rather than click a second button: retyping is the cheapest available proof
	 * that they read *which* account they are about to erase, and it is the same
	 * string the backend independently verifies.
	 */
	this.confirmDelete = function (user) {
		this._closeDialog();

		let overlay = document.createElement('div');
		overlay.className = 'pharos-admin-overlay';

		let dialog = document.createElement('div');
		dialog.className = 'pharos-admin-dialog';
		dialog.setAttribute('role', 'dialog');
		dialog.setAttribute('aria-modal', 'true');

		let title = document.createElement('div');
		title.className = 'pharos-admin-dialog-title';
		title.textContent = Zotero.getString('pharos-admin-delete-title');

		let body = document.createElement('div');
		body.className = 'pharos-admin-dialog-body';
		body.textContent = _fmt('pharos-admin-delete-body', { email: user.email });

		let local = document.createElement('div');
		local.className = 'pharos-admin-dialog-local';
		local.textContent = Zotero.getString('pharos-admin-delete-local');

		let warning = document.createElement('div');
		warning.className = 'pharos-admin-dialog-warning';
		warning.textContent = Zotero.getString('pharos-admin-delete-irreversible');

		let prompt = document.createElement('label');
		prompt.className = 'pharos-admin-dialog-prompt';
		prompt.textContent = Zotero.getString('pharos-admin-delete-prompt');

		let input = document.createElement('input');
		input.type = 'text';
		input.className = 'pharos-admin-dialog-input';
		input.placeholder = user.email;
		prompt.append(input);

		let error = document.createElement('div');
		error.className = 'pharos-admin-dialog-error';
		error.hidden = true;

		let buttons = document.createElement('div');
		buttons.className = 'pharos-admin-dialog-actions';
		let cancel = document.createElement('button');
		cancel.textContent = Zotero.getString('pharos-admin-cancel');
		cancel.addEventListener('click', () => this._closeDialog());
		let confirm = document.createElement('button');
		confirm.className = 'pharos-admin-danger';
		confirm.textContent = Zotero.getString('pharos-admin-delete-confirm');
			// The one control in Pharos that destroys another account's server-side
			// data stays unusable until the typed address matches.
		confirm.disabled = true;
		buttons.append(cancel, confirm);

		let submit = () => {
			if (confirm.disabled) {
				return;
			}
			this.deleteUser(user, input.value, { confirm, cancel, input, error });
		};
		input.addEventListener('input', () => {
			confirm.disabled = !Zotero.Pharos.Admin.confirmationMatches(
				input.value, user.email
			);
		});
		input.addEventListener('keydown', (event) => {
			if (event.key == 'Enter') {
				event.preventDefault();
				submit();
			}
		});
		confirm.addEventListener('click', submit);

		// Clicking the backdrop dismisses; clicking inside must not.
		overlay.addEventListener('click', (event) => {
			if (event.target == overlay) {
				this._closeDialog();
			}
		});

		dialog.append(title, body, local, warning, prompt, error, buttons);
		overlay.append(dialog);
		document.getElementById('pharos-admin-root').append(overlay);

		let onKey = (event) => {
			if (event.key == 'Escape') {
				this._closeDialog();
			}
		};
		document.addEventListener('keydown', onKey);
		_dialog = { overlay, onKey };
		input.focus();
	};

	this._closeDialog = function () {
		if (!_dialog) {
			return;
		}
		document.removeEventListener('keydown', _dialog.onKey);
		_dialog.overlay.remove();
		_dialog = null;
	};

	/**
	 * @param {Object} user
	 * @param {String} typed - what the operator entered, sent as confirm_email
	 * @param {Object} controls - the dialog's own elements
	 */
	this.deleteUser = async function (user, typed, { confirm, cancel, input, error }) {
		confirm.disabled = true;
		cancel.disabled = true;
		input.disabled = true;
		error.hidden = true;
		confirm.textContent = Zotero.getString('pharos-admin-deleting');
		try {
			await Zotero.Pharos.Admin.deleteUser(user.id, typed);
			this._closeDialog();
			await this.loadUsers();
		}
		catch (e) {
			Zotero.logError(e);
			// Inside the dialog, not the window status line: the overlay covers
			// the status line, and an error nobody can see reads as a hang.
			error.textContent = e.message || Zotero.getString('pharos-admin-delete-failed');
			error.hidden = false;
			confirm.textContent = Zotero.getString('pharos-admin-delete-confirm');
			confirm.disabled = false;
			cancel.disabled = false;
			input.disabled = false;
		}
	};


	//
	// Providers
	//

	this.loadProviders = async function () {
		this._setStatus(Zotero.getString('pharos-admin-loading'));
		this._body.replaceChildren();
		try {
			let providers = await Zotero.Pharos.Admin.getProviders();
			this._renderProviders(providers);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-admin-error'));
		}
	};

	this._renderProviders = function (data) {
		this._setStatus('');
		this._body.replaceChildren();

		let note = document.createElement('div');
		note.className = 'pharos-admin-note';
		note.textContent = Zotero.getString('pharos-admin-providers-note');
		this._body.append(note);

		if (Zotero.Pharos.Admin.isTranslationDegraded(data)) {
			let warning = document.createElement('div');
			warning.className = 'pharos-admin-warning';
			warning.textContent = _fmt('pharos-admin-providers-degraded', {
				configured: data.translator,
				effective: data.effective_translator,
			});
			this._body.append(warning);
		}

		let roles = document.createElement('div');
		roles.className = 'pharos-admin-roles';
		roles.append(
			this._renderRole('pharos-admin-role-translate', data.effective_translator),
			this._renderRole('pharos-admin-role-chat', data.chat_provider)
		);
		this._body.append(roles);

		if (!data.providers.length) {
			let empty = document.createElement('div');
			empty.className = 'pharos-admin-empty';
			empty.textContent = Zotero.getString('pharos-admin-providers-empty');
			this._body.append(empty);
			return;
		}

		let list = document.createElement('div');
		list.className = 'pharos-admin-providers';
		for (let provider of data.providers) {
			list.append(this._renderProvider(provider));
		}
		this._body.append(list);
	};

	this._renderRole = function (labelID, value) {
		let role = document.createElement('div');
		role.className = 'pharos-admin-role';
		let label = document.createElement('span');
		label.className = 'pharos-admin-role-label';
		label.textContent = Zotero.getString(labelID);
		let text = document.createElement('span');
		text.className = 'pharos-admin-role-value';
		text.textContent = value || Zotero.getString('pharos-admin-none');
		role.append(label, text);
		return role;
	};

	this._renderProvider = function (provider) {
		let none = Zotero.getString('pharos-admin-none');
		let card = document.createElement('div');
		card.className = 'pharos-admin-provider'
			+ (provider.configured ? ' is-on' : '');

		let head = document.createElement('div');
		head.className = 'pharos-admin-provider-head';
		let name = document.createElement('span');
		name.className = 'pharos-admin-provider-name';
		name.textContent = provider.label || provider.name;
		let state = document.createElement('span');
		state.className = 'pharos-admin-badge '
			+ (provider.configured ? 'is-ready' : 'is-plain');
		state.textContent = Zotero.getString(provider.configured
			? 'pharos-admin-provider-configured'
			: 'pharos-admin-provider-unconfigured');
		head.append(name, state);
		card.append(head);

		let fields = document.createElement('dl');
		fields.className = 'pharos-admin-kv';
		let field = (labelID, value, className) => {
			let term = document.createElement('dt');
			term.textContent = Zotero.getString(labelID);
			let definition = document.createElement('dd');
			definition.textContent = value;
			if (className) {
				definition.className = className;
			}
			fields.append(term, definition);
		};
		field('pharos-admin-provider-model', provider.model || none);
		field('pharos-admin-provider-url', provider.base_url || none, 'pharos-admin-url');
		field('pharos-admin-provider-key', provider.key_hint
			? _fmt('pharos-admin-provider-key-set', { hint: provider.key_hint })
			: Zotero.getString('pharos-admin-provider-key-unset'));
		card.append(fields);

		if (provider.roles && provider.roles.length) {
			let roles = document.createElement('div');
			roles.className = 'pharos-admin-provider-roles';
			for (let role of provider.roles) {
				let chip = document.createElement('span');
				chip.className = 'pharos-admin-rolechip';
				chip.textContent = Zotero.getString(role == 'translate'
					? 'pharos-admin-provider-used-translate'
					: 'pharos-admin-provider-used-chat');
				roles.append(chip);
			}
			card.append(roles);
		}

		let foot = document.createElement('div');
		foot.className = 'pharos-admin-provider-foot';
		let button = document.createElement('button');
		button.textContent = Zotero.getString('pharos-admin-probe');
		// A probe on an unconfigured provider is answered, correctly, with a
		// failure. Offering the button anyway would just teach operators to
		// ignore red.
		button.disabled = !provider.configured;
		let result = document.createElement('span');
		result.className = 'pharos-admin-probe';
		button.addEventListener('click', () => this.probe(provider, button, result));
		foot.append(button, result);
		card.append(foot);

		return card;
	};

	this.probe = async function (provider, button, result) {
		button.disabled = true;
		button.textContent = Zotero.getString('pharos-admin-probing');
		result.textContent = '';
		result.className = 'pharos-admin-probe';
		try {
			let probed = await Zotero.Pharos.Admin.probeProvider(provider.name);
			result.className = 'pharos-admin-probe ' + (probed.ok ? 'is-ok' : 'is-bad');
			result.textContent = probed.ok
				? _fmt('pharos-admin-probe-ok', { ms: probed.latency_ms })
				: probed.detail || Zotero.getString('pharos-admin-probe-failed');
		}
		catch (e) {
			Zotero.logError(e);
			result.className = 'pharos-admin-probe is-bad';
			result.textContent = e.message || Zotero.getString('pharos-admin-probe-failed');
		}
		finally {
			button.disabled = false;
			button.textContent = Zotero.getString('pharos-admin-probe');
		}
	};
};
