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
 * The sign-in gate: the boundary between "anonymous" and "signed in".
 *
 * The web client (frontend/src/auth/AuthGate.tsx) wraps its whole app in this
 * and renders nothing else until a session exists. It can do that because it
 * owns its root. Here it cannot: Zotero's main window IS the app, its startup
 * depends on it opening, and a client that will not open its own library
 * because a remote backend is down is worse than one that lets you in. So the
 * same two-column page is a MODAL WINDOW over the main window instead, opened
 * at startup when there is no token and dismissed as soon as there is one.
 *
 * THE WAY PAST IT. Everything that makes this a usable reader is local: the
 * library, the PDF reader, annotations, notes, collections, tags. None of it
 * touches the Pharos backend, and someone who has no account -- or is on a
 * plane, or is self-hosting and has not brought their server up yet -- must
 * still get all of it. "Skip for now" closes the gate and records the choice,
 * so it stops blocking startup rather than asking again every launch; sign-in
 * stays available in Settings → Pharos. Only the server-backed features
 * (translation, AI chat, the daily digest, discovery, projects) stay dark, and
 * each of those already says so on its own.
 */
var Zotero_Pharos_Auth = new function () {
	/**
	 * Mirrors MIN_PASSWORD_LENGTH in backend/pharos/auth/passwords.py. Checked
	 * here only to save a round trip; the backend is still the authority.
	 */
	const MIN_PASSWORD = 8;

	/**
	 * Deliberately loose. Strict email regexes reject valid addresses, and the
	 * only real test is whether the server accepts it -- this catches typos.
	 */
	const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

	/**
	 * The registration probe is a courtesy, not a gate: it decides whether to
	 * offer a control, so it must never make the user wait 30 seconds to be
	 * offered a sign-in form.
	 */
	const STATUS_TIMEOUT = 8000;

	/** How many pharos-auth-greeting-N strings exist. */
	const GREETING_COUNT = 8;

	let _resolveInit;
	let _mode = 'login';

	/**
	 * Validation messages appear only after a submit attempt: flagging an empty
	 * field the instant it is focused reads as scolding.
	 */
	let _submitted = false;
	let _pending = false;

	/**
	 * Set the moment the gate is dismissed. The registration probe can still be
	 * in flight when the user presses Escape, and painting a window that is on
	 * its way out is how a dismissal turns into an error in the console.
	 */
	let _closing = false;

	/**
	 * null until GET /api/auth/status answers. Tri-state on purpose -- see
	 * _probeRegistration() for why "not yet known" and "closed" cannot share a
	 * value.
	 */
	let _registrationOpen = null;

	/**
	 * Whether the probe is still in flight.
	 *
	 * Separate from _registrationOpen, which is null for BOTH "still asking" and
	 * "asked, and the backend has no such endpoint". Conflating them left the tab
	 * row stuck at visibility:hidden forever against an older self-hosted backend
	 * -- exactly the case the unknown state exists to serve.
	 */
	let _probing = true;

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

	/**
	 * What to show the user for a failed request.
	 *
	 * The server's own `detail` wherever there is one -- Zotero.Pharos.API
	 * unwraps it -- because rewriting it would mean guessing which failure
	 * occurred, and guessing wrong on a 409 or a rate limit leaves the user with
	 * no idea what to change. A connection that never completed is the
	 * exception: its message names the URL and "status code 0", which is not
	 * something anyone can act on.
	 */
	function _errorText(e) {
		if (e instanceof Zotero.HTTP.TimeoutException || !e || !e.message || e.status === 0) {
			return Zotero.getString('pharos-error-unreachable');
		}
		return e.message;
	}

	/**
	 * The greeting id for today.
	 *
	 * By date rather than at random: a line that changed on every keystroke
	 * would be noise, and one that never changed would go stale. Local date, so
	 * it turns over at the reader's midnight rather than UTC's.
	 */
	this.greetingID = function (now = new Date()) {
		let days = Math.floor((now.getTime() - now.getTimezoneOffset() * 60000) / 86400000);
		let index = ((days % GREETING_COUNT) + GREETING_COUNT) % GREETING_COUNT;
		return 'pharos-auth-greeting-' + index;
	};

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		// Present when opened through openDialog with an argument; absent when
		// the window is opened directly by its chrome URL, which is what the
		// tests do.
		this.io = (window.arguments && window.arguments[0]) || null;

		this._modes = document.getElementById('pharos-auth-modes');
		this._closedNote = document.getElementById('pharos-auth-closed');
		this._email = document.getElementById('pharos-auth-email');
		this._emailError = document.getElementById('pharos-auth-email-error');
		this._password = document.getElementById('pharos-auth-password');
		this._passwordError = document.getElementById('pharos-auth-password-error');
		this._nameRow = document.getElementById('pharos-auth-name-row');
		this._name = document.getElementById('pharos-auth-name');
		this._alert = document.getElementById('pharos-auth-alert');
		this._submit = document.getElementById('pharos-auth-submit');
		this._registerNote = document.getElementById('pharos-auth-register-note');
		this._skip = document.getElementById('pharos-auth-skip');
		this._foot = document.getElementById('pharos-auth-foot');

		this._initPoster();

		document.l10n.setAttributes(this._foot, this.greetingID());

		for (let button of this._modes.querySelectorAll('.pharos-auth-mode')) {
			button.addEventListener('click', () => this.setMode(button.dataset.mode));
		}
		this._submit.addEventListener('click', () => this.submit());
		this._skip.addEventListener('click', () => this.skip());

		// Enter in any field submits, which is what a sign-in form is expected
		// to do. Same wiring as the preferences pane.
		for (let field of [this._email, this._password, this._name]) {
			field.addEventListener('keypress', (event) => {
				if (event.key == 'Enter') {
					this.submit();
				}
			});
		}

		// Escape dismisses for this session only, and deliberately does NOT
		// record the skip: closing a window by reflex is not the same as saying
		// "stop asking me".
		window.addEventListener('keydown', (event) => {
			if (event.key == 'Escape' && !_pending) {
				this._finish({ signedIn: false, skipped: false });
			}
		});

		this._render();
		this._email.focus();

		await this._probeRegistration();
	};

	this.destroy = function () {
		// The field dies with the window anyway; clearing it says that the
		// password is not meant to outlive the request that used it.
		if (this._password) {
			this._password.value = '';
		}
	};

	/**
	 * Show the brand panel instead of the poster when the artwork is missing.
	 *
	 * Same fallback as the web client, for the same reason: the panel then reads
	 * as a deliberate brand field rather than as a broken image. It cannot be
	 * decided in the markup because whether a chrome:// image resolves is only
	 * known once the load has been attempted -- and that attempt can finish
	 * either before or after this runs, so both paths have to be handled.
	 */
	this._initPoster = function () {
		let img = document.getElementById('pharos-auth-poster-img');
		let fail = () => img.setAttribute('data-failed', 'true');
		img.addEventListener('error', fail);
		if (img.complete && !img.naturalWidth) {
			fail();
		}
	};

	/**
	 * Ask the backend whether registration is open.
	 *
	 * Three outcomes, and they are not two: open, closed, and unknown.
	 *
	 * - open: offer the register tab.
	 * - closed: hide it. Showing a door that will not open is worse than not
	 *   showing one, because the user only finds out after typing a password.
	 * - unknown (no /api/auth/status on this backend, or it is unreachable):
	 *   offer it anyway and let a 403 on submit be the answer, which is how the
	 *   web client discovers this. Unknown is not closed, and an older
	 *   self-hosted instance must not lose its sign-up form.
	 *
	 * The tabs stay invisible while the probe is in flight rather than
	 * appearing and being retracted. On a reachable server this settles long
	 * before anyone has finished typing an email address.
	 */
	this._probeRegistration = async function () {
		_probing = true;
		try {
			let status = await Zotero.Pharos.API.request('GET', '/api/auth/status', {
				anon: true,
				timeout: STATUS_TIMEOUT,
			});
			_registrationOpen = status && typeof status.allow_registration == 'boolean'
				? status.allow_registration
				: null;
		}
		catch (e) {
			// Not logError: a backend without this endpoint, or no network, is
			// an ordinary state for this window, not a fault worth a report.
			Zotero.debug('Pharos: could not read registration status: ' + e.message, 2);
			_registrationOpen = null;
		}
		finally {
			_probing = false;
		}
		if (_registrationOpen === false && _mode == 'register') {
			// The tabs are invisible while the probe runs, so nobody should be
			// on this tab yet; leaving the form on a mode with no way to submit
			// is the kind of state that only shows up once.
			_mode = 'login';
		}
		this._render();
	};

	this.setMode = function (mode) {
		if (mode != 'login' && mode != 'register') {
			return;
		}
		if (mode == 'register' && _registrationOpen === false) {
			return;
		}
		_mode = mode;
		// Switching tabs clears what the previous attempt said: the errors
		// belonged to a different form.
		_submitted = false;
		this._setAlert('');
		this._render();
	};

	this.getMode = function () {
		return _mode;
	};

	this.registrationOpen = function () {
		return _registrationOpen;
	};

	this._render = function () {
		if (_closing) {
			return;
		}
		let isRegister = _mode == 'register';

		// data-probing rather than hidden: the row keeps its height while the
		// probe runs, so the panel does not jump when the answer arrives.
		this._modes.setAttribute('data-probing', _probing ? 'true' : 'false');
		this._modes.hidden = _registrationOpen === false;
		this._closedNote.hidden = _registrationOpen !== false;

		for (let button of this._modes.querySelectorAll('.pharos-auth-mode')) {
			let on = button.dataset.mode == _mode;
			button.classList.toggle('is-active', on);
			button.setAttribute('aria-selected', on);
		}

		this._nameRow.hidden = !isRegister;

		document.l10n.setAttributes(
			this._password,
			isRegister
				? 'pharos-auth-password-placeholder-register'
				: 'pharos-auth-password-placeholder',
			isRegister ? { min: MIN_PASSWORD } : undefined
		);
		// A password manager offering the stored password on a registration
		// form, or a new one on sign-in, is the wrong suggestion either way.
		this._password.setAttribute(
			'autocomplete', isRegister ? 'new-password' : 'current-password'
		);

		let label;
		if (_pending) {
			label = isRegister
				? 'pharos-auth-submitting-register'
				: 'pharos-auth-submitting-sign-in';
		}
		else {
			label = isRegister ? 'pharos-auth-submit-register' : 'pharos-auth-submit-sign-in';
		}
		document.l10n.setAttributes(this._submit, label);
		this._submit.disabled = _pending;
		this._skip.disabled = _pending;

		this._registerNote.hidden = !isRegister;

		this._renderValidation();
	};

	/**
	 * @return {String|null} the reason the email is unusable, or null
	 */
	this._emailProblem = function () {
		let value = this._email.value.trim();
		if (!value) {
			return Zotero.getString('pharos-auth-email-required');
		}
		if (!EMAIL_RE.test(value)) {
			return Zotero.getString('pharos-auth-email-invalid');
		}
		return null;
	};

	/**
	 * @return {String|null} the reason the password is unusable, or null
	 *
	 * The length rule applies to registration only. Enforcing it on sign-in
	 * would reject a short password locally while a wrong one fails at the
	 * server, which tells an attacker their guess was at least the right shape.
	 * The backend declines to validate login passwords for exactly this reason;
	 * so does this.
	 */
	this._passwordProblem = function () {
		let value = this._password.value;
		if (!value) {
			return Zotero.getString('pharos-auth-password-required');
		}
		if (_mode == 'register' && value.length < MIN_PASSWORD) {
			return _fmt('pharos-auth-password-short', { min: MIN_PASSWORD });
		}
		return null;
	};

	this._renderValidation = function () {
		let pairs = [
			[this._email, this._emailError, _submitted ? this._emailProblem() : null],
			[this._password, this._passwordError, _submitted ? this._passwordProblem() : null],
		];
		for (let [field, node, problem] of pairs) {
			node.textContent = problem || '';
			node.hidden = !problem;
			if (problem) {
				field.setAttribute('aria-invalid', 'true');
			}
			else {
				field.removeAttribute('aria-invalid');
			}
		}
	};

	this._setAlert = function (text) {
		this._alert.textContent = text || '';
		this._alert.hidden = !text;
	};

	this.submit = async function () {
		if (_pending) {
			return;
		}
		_submitted = true;
		this._renderValidation();
		if (this._emailProblem() || this._passwordProblem()) {
			return;
		}

		let isRegister = _mode == 'register';
		let email = this._email.value.trim();

		_pending = true;
		this._setAlert('');
		this._render();
		// The window is closed after the finally rather than inside the try, so
		// the last _render() is not painting a window that is already going.
		let signedIn = false;
		try {
			let user;
			if (isRegister) {
				user = await this._register(email);
			}
			else {
				// The ONLY place the password is handed to anything: straight
				// from the field into the API call. It is never stored in a
				// pref, written to an attribute, or passed to Zotero.debug.
				user = await Zotero.Pharos.API.login(email, this._password.value);
			}
			// Cleared before anything else can go wrong, so a later failure
			// cannot leave it sitting in the DOM.
			this._password.value = '';

			Zotero.Prefs.set('pharos.accountEmail', user.email);
			// A user who signs in has answered the question the gate asks, so
			// an earlier "skip" stops applying: if they sign out later, the
			// gate is theirs to see again.
			Zotero.Prefs.set('pharos.auth.skipped', false);

			signedIn = true;
		}
		catch (e) {
			// Logs the failure, not the credential: Zotero.HTTP never puts a
			// request body in the log, and e.message is the server's `detail`.
			Zotero.logError(e);
			if (e.status == 403 && isRegister) {
				// Registration is off on this instance after all -- either this
				// backend has no /api/auth/status, or it was switched off since
				// the probe. Hide the tab rather than leave a control that can
				// only ever fail, and put the user back on sign-in.
				_registrationOpen = false;
				_mode = 'login';
			}
			this._setAlert(_errorText(e));
		}
		finally {
			_pending = false;
			this._render();
		}

		if (signedIn) {
			this._finish({ signedIn: true, skipped: false });
		}
	};

	/**
	 * POST /api/auth/register, then store the token it mints.
	 *
	 * Written here rather than called through an API method because
	 * Zotero.Pharos.API has login() but no register() -- the client had no
	 * sign-up surface before this window. If one is added, this should become a
	 * call to it; the shape is identical to login().
	 *
	 * @return {Promise<Object>} the new user
	 */
	this._register = async function (email) {
		let body = { email, password: this._password.value };
		let displayName = this._name.value.trim();
		if (displayName) {
			// Omitted rather than sent as null or "": RegisterRequest is
			// declared extra="forbid" and treats a missing display_name as
			// "use the email", which is what an empty field means.
			body.display_name = displayName;
		}
		let res = await Zotero.Pharos.API.request('POST', '/api/auth/register', {
			anon: true,
			body,
		});
		await Zotero.Pharos.API.setToken(res.token);
		return res.user;
	};

	/**
	 * Close the gate without signing in, and stop it blocking startup.
	 *
	 * Everything local keeps working -- see the note at the top of this file --
	 * so this is a real answer to the question, not a postponement. Recording
	 * it is the difference between a gate and a nag: a user who does not want a
	 * Pharos account should not have to dismiss this on every launch. Settings
	 * → Pharos is where they sign in if they change their mind.
	 */
	this.skip = function () {
		Zotero.Prefs.set('pharos.auth.skipped', true);
		this._finish({ signedIn: false, skipped: true });
	};

	this._finish = function (result) {
		if (_closing) {
			return;
		}
		_closing = true;
		if (this.io) {
			this.io.dataOut = result;
			window.close();
			return;
		}
		// Standalone: this window IS the application right now, so it opens the
		// library before letting go. Opened first so the user never sees an
		// empty screen between the two, and because a window count of zero is
		// what tells the platform to quit.
		Zotero.openMainWindow();
		window.close();
	};
};
