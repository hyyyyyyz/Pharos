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
 * Client for the Pharos backend -- the服务端 half of the platform, which holds
 * the accounts, the translation engine (BabelDOC), the LLM relay and the daily
 * arXiv digest.
 *
 * The desktop client talks to it over the same REST API the web client uses, so
 * the two stay interchangeable: an account, a library and a translation started
 * in one is visible in the other.
 */
// Not `= {}`: these modules are loaded in a list, and whichever ran second used
// to wipe what the first had attached. The order in zotero.mjs is chosen for
// other reasons -- sharedLibrary has to exist before schema.js asks it anything
// -- so no file here may assume it is the one that creates the namespace.
Zotero.Pharos = Zotero.Pharos || {};

Zotero.Pharos.API = new function () {
	/**
	 * Where the login manager keeps the bearer token.
	 *
	 * Exactly how Zotero stores its own Web API key (xpcom/sync/syncLocal.js):
	 * the login manager, which Gecko encrypts at rest and which on macOS is
	 * backed by the system keychain. A Pharos token is a live credential for the
	 * user's whole library, so it gets the same treatment as Zotero's own.
	 *
	 * An earlier version wrapped the secret in `Zotero.OSKeyStore` for a second
	 * layer. That API does not exist in the Zotero release this client is now
	 * built on -- it arrived in a later development branch -- and calling it
	 * threw on every sign-in. `_encrypt`/`_decrypt` below use it when it is
	 * present and pass the value through when it is not, so the token is stored
	 * the way Zotero stores its own either way.
	 *
	 * The host is deliberately not "chrome://zotero" -- sharing that host with
	 * Zotero's own entry would put two unrelated credentials in one bucket,
	 * where clearing one could clear the other.
	 */
	const LOGIN_HOST = 'chrome://pharos';
	const LOGIN_REALM = 'Pharos API';
	const OFFICIAL_BASE_URL = 'https://pharos.selab.top';
	const BASE_URL_PREF = 'pharos.baseURL';
	const CREDENTIAL_BASE_URL_PREF = 'pharos.credentialBaseURL';

	/**
	 * Requests get a generous timeout because translation upload is a multi-MB
	 * POST over a consumer uplink; the job itself is asynchronous and polled, so
	 * this only has to cover the transfer.
	 */
	const UPLOAD_TIMEOUT = 300000;
	const DEFAULT_TIMEOUT = 30000;

	/**
	 * The parts of the session user this client keeps a local copy of.
	 *
	 * Both are read off the server and never chosen here, which is why they are
	 * cached where the server's answer arrives rather than at each sign-in path.
	 * The address is the exception and is deliberately not written here: it is
	 * *typed* by whoever signs in, so `pharos.accountEmail` already has one
	 * writer per sign-in route and a second one would make it ambiguous which
	 * of them lost a race.
	 *
	 * Prefs rather than variables for the same reason Admin caches `isAdmin`:
	 * the rail and the preferences pane have to paint an account before any
	 * request could have come back, and a window opening must not show a
	 * signed-in user as anonymous for the length of a round trip.
	 *
	 * pharos.pdfTranslation is read by Zotero.Pharos.Translate.isEnabled(),
	 * which is where the rule for an ABSENT value is written down.
	 */
	const DISPLAY_NAME_PREF = 'pharos.accountName';
	const PDF_TRANSLATION_PREF = 'pharos.pdfTranslation';
	const ACCOUNT_CHANGED_TOPIC = 'pharos-api-account-changed';

	/** Matches _MAX_DISPLAY_NAME in backend/pharos/api/auth.py; longer is a 422. */
	const MAX_DISPLAY_NAME = 128;

	let _cachedToken = null;
	let _tokenEpoch = 0;
	let _forgettingCredential = false;

	/**
	 * A monotonically increasing identity for account-scoped async work.
	 *
	 * A token is deliberately not exposed to callers merely so they can tell
	 * whether an upload still belongs to the account that started it. The epoch
	 * answers that question without copying a credential out of this module.
	 */
	this.getTokenEpoch = function () {
		return _tokenEpoch;
	};

	/** Observer topic emitted after every token replacement or removal. */
	this.ACCOUNT_CHANGED_TOPIC = ACCOUNT_CHANGED_TOPIC;

	function _accountChanged() {
		_tokenEpoch++;
		// Clear the shared transport caches before mounted surfaces hear about the
		// new identity and begin restoring themselves.
		Zotero.Pharos.Chat?._clearCache();
		Services.obs.notifyObservers(null, ACCOUNT_CHANGED_TOPIC, String(_tokenEpoch));
	}

	function _normaliseBaseURL(url) {
		return String(url || '').trim().replace(/\/+$/, '');
	}

	/**
	 * Base URL of the backend, without a trailing slash.
	 *
	 * Official artifacts have one product/service boundary and always use the
	 * operated Pharos origin. A source checkout still needs to reach localhost
	 * and disposable test servers, so `.SOURCE` builds alone honour the hidden
	 * developer pref. There is deliberately no Settings control for it.
	 */
	this.getBaseURL = function () {
		if (Zotero.isSourceBuild) {
			let override = _normaliseBaseURL(Zotero.Prefs.get(BASE_URL_PREF));
			if (override) {
				return override;
			}
		}
		return OFFICIAL_BASE_URL;
	};


	//
	// Credentials
	//

	this._getLoginInfo = function () {
		let logins;
		try {
			logins = Services.logins.findLogins(LOGIN_HOST, null, LOGIN_REALM);
		}
		catch (e) {
			Zotero.logError(e);
			return false;
		}
		return logins.length ? logins[0] : false;
	};

	// Kept as a narrow seam so the failure path can be exercised without asking
	// the real OS credential store to malfunction in a test.
	this._removeLogin = function (login) {
		Services.logins.removeLogin(login);
	};

	/** Remove a credential and every local identity hint belonging to it. */
	function _forgetCredential(login) {
		if (_forgettingCredential) {
			return false;
		}
		_forgettingCredential = true;
		let removed = !login;
		try {
			try {
				if (login) {
					Zotero.Pharos.API._removeLogin(login);
					removed = true;
				}
			}
			catch (e) {
				Zotero.logError(e);
			}
			_cachedToken = null;
			if (removed && Zotero.Prefs.prefHasUserValue(CREDENTIAL_BASE_URL_PREF)) {
				Zotero.Prefs.clear(CREDENTIAL_BASE_URL_PREF);
			}
			else if (!removed) {
				// Fail closed if the platform credential store refuses deletion. A
				// durable, deliberately impossible marker means the surviving secret
				// can never be mistaken for an unlabelled legacy token on the next call.
				Zotero.Prefs.set(
					CREDENTIAL_BASE_URL_PREF, 'invalid://credential-removal-failed'
				);
			}
			Zotero.Prefs.set('pharos.accountEmail', '');
			Zotero.Prefs.set('pharos.isAdmin', false);
			// cacheUser() is attached by the time a credential can be read, but keep
			// this tolerant of an unusually early call during module initialisation.
			Zotero.Pharos.API.cacheUser?.(null);
			_accountChanged();
			return removed;
		}
		finally {
			_forgettingCredential = false;
		}
	}

	/**
	 * Confirm that a stored bearer token was issued by the requested origin.
	 *
	 * Version 1.3.0 stored every token in one login-manager bucket, without an
	 * origin marker. Its old baseURL pref is not reliable provenance, so every
	 * unbound legacy token means "sign in again", never "guess the issuer".
	 */
	function _credentialBelongsToOrigin(login, requestedOrigin) {
		let origin = _normaliseBaseURL(requestedOrigin);
		let credentialOrigin = _normaliseBaseURL(
			Zotero.Prefs.get(CREDENTIAL_BASE_URL_PREF)
		);
		if (credentialOrigin == 'invalid://credential-removal-failed') {
			return false;
		}

		if (!credentialOrigin) {
			// Pre-1.3.1 tokens have no trustworthy issuer marker. The legacy
			// endpoint pref is only a hint: it could have been changed before an
			// interrupted sign-out, copied from another profile, or edited by hand.
			// Discard every unbound token and require one clean sign-in.
			let removed = _forgetCredential(login);
			if (removed && !Zotero.isSourceBuild
					&& Zotero.Prefs.prefHasUserValue(BASE_URL_PREF)) {
				Zotero.Prefs.clear(BASE_URL_PREF);
			}
			return false;
		}

		if (credentialOrigin != origin) {
			let removed = _forgetCredential(login);
			if (removed && !Zotero.isSourceBuild
					&& Zotero.Prefs.prefHasUserValue(BASE_URL_PREF)) {
				Zotero.Prefs.clear(BASE_URL_PREF);
			}
			return false;
		}

		// A current, origin-bound official token can stay signed in, but the
		// obsolete user value should not imply that releases accept overrides.
		if (!Zotero.isSourceBuild && Zotero.Prefs.prefHasUserValue(BASE_URL_PREF)) {
			Zotero.Prefs.clear(BASE_URL_PREF);
		}
		return true;
	}

	/**
	 * Whether a token is stored. Synchronous, so menus and UI state can consult
	 * it without awaiting a decrypt.
	 */
	this.hasCredentials = function () {
		let login = this._getLoginInfo();
		return !!login && _credentialBelongsToOrigin(login, this.getBaseURL());
	};

	this.getToken = async function (origin = this.getBaseURL()) {
		let login = this._getLoginInfo();
		if (!login || !_credentialBelongsToOrigin(login, origin)) {
			return null;
		}
		if (_cachedToken !== null) {
			return _cachedToken;
		}
		let epoch = _tokenEpoch;
		try {
			let token = await _decrypt(login.password);
			// Decryption may cross an account or development-origin switch. Never
			// let an old async result overwrite the new account's in-memory token.
			if (epoch != _tokenEpoch) {
				return null;
			}
			_cachedToken = token;
		}
		catch (e) {
			if (epoch != _tokenEpoch) {
				return null;
			}
			// A token that cannot be decrypted is not recoverable, and leaving it
			// in place would make every request 401 forever with no way out from
			// the UI. Drop it so the user is asked to sign in again.
			Zotero.logError(e);
			await this.setToken(null);
			return null;
		}
		return _cachedToken;
	};


	/**
	 * Wrap a secret for storage, using OSKeyStore where the platform has it.
	 *
	 * Capability-detected rather than assumed: `Zotero.OSKeyStore` is absent from
	 * the release this client is built on and present in later branches, and a
	 * bare call to it fails at sign-in -- the one moment a user cannot work
	 * around. Storage is the login manager either way, which is already
	 * encrypted at rest.
	 */
	async function _encrypt(secret) {
		if (Zotero.OSKeyStore && typeof Zotero.OSKeyStore.encrypt == 'function') {
			return Zotero.OSKeyStore.encrypt(secret);
		}
		return secret;
	}

	/**
	 * The inverse, and deliberately tolerant of a value the other path wrote.
	 *
	 * A profile that was written by a build WITH OSKeyStore and is then opened by
	 * one without it holds a ciphertext this cannot read. Returning it verbatim
	 * would send an unusable token as a bearer credential and produce a 401 loop;
	 * throwing sends the caller down the "drop the token and ask again" path,
	 * which is recoverable.
	 */
	async function _decrypt(stored) {
		if (Zotero.OSKeyStore && typeof Zotero.OSKeyStore.decrypt == 'function') {
			return Zotero.OSKeyStore.decrypt(stored);
		}
		return stored;
	}

	this.setToken = async function (token, issuedOrigin = this.getBaseURL()) {
		let oldLogin = this._getLoginInfo();

		if (!token) {
			// Every route out of an account passes through here -- sign out,
			// sign out everywhere, and the 401 handler below. What the cache
			// holds describes the account that has just gone, and the next one
			// in may be a different person; a leftover "translation off" would
			// hide the feature from someone who has it.
			_forgetCredential(oldLogin);
			return;
		}

		let encrypted = await _encrypt(token);
		let nsLoginInfo = new Components.Constructor(
			'@mozilla.org/login-manager/loginInfo;1',
			Components.interfaces.nsILoginInfo,
			'init'
		);
		// username is unused -- the token is the whole credential -- but the
		// login manager requires the field, and an empty string is what Zotero
		// itself stores in the equivalent slot.
		let login = new nsLoginInfo(LOGIN_HOST, null, LOGIN_REALM, '', encrypted, '', '');

		if (oldLogin) {
			Services.logins.modifyLogin(oldLogin, login);
		}
		else {
			await Services.logins.addLoginAsync(login);
		}
		_cachedToken = token;
		let credentialOrigin = Zotero.isSourceBuild
			? _normaliseBaseURL(issuedOrigin)
			: OFFICIAL_BASE_URL;
		if (!credentialOrigin) {
			_forgetCredential(this._getLoginInfo());
			throw new Error('Missing Pharos credential origin');
		}
		Zotero.Prefs.set(CREDENTIAL_BASE_URL_PREF, credentialOrigin);
		if (!Zotero.isSourceBuild && Zotero.Prefs.prefHasUserValue(BASE_URL_PREF)) {
			Zotero.Prefs.clear(BASE_URL_PREF);
		}
		if (credentialOrigin != this.getBaseURL()) {
			_forgetCredential(this._getLoginInfo());
			throw new Error('Pharos service changed while signing in');
		}
		// Paper ids, model configuration and in-flight preparation all belong to
		// the bearer-token account. Reusing any of them after an account change
		// can point a question at an id that only existed for the previous user.
		_accountChanged();
	};


	//
	// Requests
	//

	/**
	 * Make an authenticated request against the backend.
	 *
	 * @param {String} method
	 * @param {String} path - API path beginning with "/", e.g. "/api/papers"
	 * @param {Object} [options]
	 * @param {Object|FormData|String} [options.body] - Plain objects are sent as
	 *     JSON; FormData is passed through so the boundary is set by the platform
	 * @param {Boolean} [options.anon] - Send without a token. Only login and
	 *     register, which are what mint one
	 * @param {Function} [options.captureOrigin] - Internal sign-in hook that
	 *     records the exact origin which issued a returned token
	 * @param {String} [options.responseType] - e.g. "arraybuffer" for PDF bytes
	 * @param {Number} [options.timeout]
	 * @return {Promise<Object|ArrayBuffer>} Parsed JSON, or the raw response when
	 *     responseType is set
	 */
	this.request = async function (method, path, options = {}) {
		let baseURL = this.getBaseURL();
		options.captureOrigin?.(baseURL);
		let headers = Object.assign({}, options.headers || {});
		let body = options.body;

		if (body && typeof body == 'object' && !(body instanceof FormData)) {
			body = JSON.stringify(body);
			headers['Content-Type'] = 'application/json';
		}
		// No Content-Type for FormData, deliberately: the platform has to set it
		// so that it can append the multipart boundary.

		if (!options.anon) {
			let token = await this.getToken(baseURL);
			if (!token) {
				throw new Zotero.Pharos.API.SignedOutError();
			}
			headers.Authorization = 'Bearer ' + token;
		}

		let xhr;
		try {
			xhr = await Zotero.HTTP.request(method, baseURL + path, {
				body,
				headers,
				responseType: options.responseType,
				timeout: options.timeout
					|| (body instanceof FormData ? UPLOAD_TIMEOUT : DEFAULT_TIMEOUT),
				successCodes: options.successCodes,
			});
		}
		catch (e) {
			// A 401 on a request we authenticated means the token is dead --
			// expired, revoked, or invalidated by a token-epoch bump. Clearing it
			// is what turns a permanent wall of failures back into a sign-in
			// prompt. Matches the web client's behaviour.
			if (e instanceof Zotero.HTTP.UnexpectedStatusException
					&& e.status == 401 && !options.anon) {
				await this.setToken(null);
				throw new Zotero.Pharos.API.SignedOutError();
			}
			throw this._toReadableError(e);
		}

		if (options.responseType) {
			return xhr.response;
		}
		if (!xhr.responseText) {
			return null;
		}
		return JSON.parse(xhr.responseText);
	};

	/**
	 * POST and consume a newline-delimited JSON stream, one object at a time.
	 *
	 * Separate from request() because that goes through Zotero.HTTP, which is
	 * XHR-based and only hands back a response once it is complete. Chat replies
	 * are the one place where waiting for the whole body defeats the point: the
	 * answer has to appear as it is generated.
	 *
	 * @param {String} path
	 * @param {Object} options
	 * @param {Object} options.body - serialised as JSON
	 * @param {Function} options.onEvent - called with each parsed object
	 * @param {AbortSignal} [options.signal]
	 */
	this.stream = async function (path, { body, onEvent, signal } = {}) {
		let baseURL = this.getBaseURL();
		let token = await this.getToken(baseURL);
		if (!token) {
			throw new Zotero.Pharos.API.SignedOutError();
		}

		let response;
		try {
			response = await fetch(baseURL + path, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: 'Bearer ' + token,
				},
				body: JSON.stringify(body),
				signal,
			});
		}
		catch (e) {
			// fetch rejects on transport failure with a message that says nothing
			// useful ("NetworkError when attempting to fetch resource").
			if (e.name == 'AbortError') {
				throw e;
			}
			throw new Error(Zotero.getString('pharos-error-unreachable'));
		}

		if (!response.ok) {
			// Same reasoning as in request(): a 401 here means the token is dead,
			// and clearing it is what turns a wall of failures back into a prompt.
			if (response.status == 401) {
				await this.setToken(null);
				throw new Zotero.Pharos.API.SignedOutError();
			}
			let detail;
			try {
				let parsed = JSON.parse(await response.text());
				detail = parsed && parsed.detail;
			}
			catch (e) {
				// Not JSON. Fall through to the status.
			}
			let error = new Error(detail || `HTTP ${response.status}`);
			error.status = response.status;
			throw error;
		}

		let reader = response.body.getReader();
		let decoder = new TextDecoder();
		let buffer = '';
		while (true) {
			let { done, value } = await reader.read();
			if (done) {
				break;
			}
			// stream: true because a multi-byte character can be split across
			// two chunks, and decoding each chunk independently would corrupt it.
			buffer += decoder.decode(value, { stream: true });

			// The last segment is kept: a chunk boundary rarely lands on a
			// newline, so the tail is usually half an object.
			let lines = buffer.split('\n');
			buffer = lines.pop();
			for (let line of lines) {
				line = line.trim();
				if (!line) {
					continue;
				}
				try {
					onEvent(JSON.parse(line));
				}
				catch (e) {
					Zotero.logError(new Error(`Unparseable stream line: ${line}`));
				}
			}
		}
		// A final object with no trailing newline.
		if (buffer.trim()) {
			try {
				onEvent(JSON.parse(buffer.trim()));
			}
			catch (e) {
				Zotero.logError(e);
			}
		}
	};

	/**
	 * FastAPI reports failures as {"detail": ...}. Surfacing that beats showing
	 * the caller a bare status code.
	 */
	this._toReadableError = function (e) {
		if (!(e instanceof Zotero.HTTP.UnexpectedStatusException)) {
			return e;
		}
		let detail;
		try {
			let parsed = JSON.parse(e.xmlhttp.responseText);
			detail = parsed && parsed.detail;
			if (Array.isArray(detail)) {
				// Pydantic validation errors arrive as a list of objects.
				detail = detail.map(d => d.msg || JSON.stringify(d)).join('; ');
			}
		}
		catch (parseError) {
			// Not JSON -- a proxy error page, say. Fall through to the status.
		}
		if (detail) {
			let wrapped = new Error(detail);
			wrapped.status = e.status;
			return wrapped;
		}
		return e;
	};

	/** Thrown when there is no usable token. Callers turn this into a prompt. */
	this.SignedOutError = function () {
		this.message = 'Not signed in to Pharos';
	};
	this.SignedOutError.prototype = Object.create(Error.prototype);
	this.SignedOutError.prototype.name = 'PharosSignedOutError';


	//
	// Account
	//

	/**
	 * Keep a local copy of the parts of the session user the UI paints from.
	 *
	 * Called from every point a UserOut arrives, and with null when the account
	 * goes. Tolerant of anything: a stubbed request() in a test, or a backend
	 * old enough to predate a field, must not take a sign-in down.
	 *
	 * An ABSENT pdf_translation is not written at all, so the pref stays unset
	 * and isEnabled() resolves it to on. Writing `false` for a field the server
	 * did not send would turn "this build is older than that field" into "this
	 * account has translation switched off".
	 *
	 * @param {Object|null} user - a UserOut, or null to forget the account
	 */
	this.cacheUser = function (user) {
		// Cleared rather than set to `true`: an unset pref and an explicit true
		// mean the same thing to isEnabled(), and only one of them survives a
		// later change to what the default should be.
		let forget = () => {
			if (Zotero.Prefs.prefHasUserValue(PDF_TRANSLATION_PREF)) {
				Zotero.Prefs.clear(PDF_TRANSLATION_PREF);
			}
		};

		if (!user || typeof user != 'object') {
			Zotero.Prefs.set(DISPLAY_NAME_PREF, '');
			forget();
			return;
		}
		Zotero.Prefs.set(
			DISPLAY_NAME_PREF, String(user.display_name || '').trim()
		);
		if (user.pdf_translation === undefined || user.pdf_translation === null) {
			forget();
		}
		else {
			Zotero.Prefs.set(PDF_TRANSLATION_PREF, !!user.pdf_translation);
		}
	};

	/**
	 * The label this account has chosen for itself, or '' if it has none.
	 *
	 * Synchronous, and never a substitute for the address: the address is what
	 * identifies the account, and callers that show this are expected to keep
	 * the address reachable. Mirrors the web client's `display_name?.trim() ||
	 * user.email` (frontend/src/components/Rail.tsx).
	 *
	 * @return {String}
	 */
	this.getDisplayName = function () {
		return String(Zotero.Prefs.get(DISPLAY_NAME_PREF) || '').trim();
	};

	this.login = async function (email, password) {
		let issuedOrigin;
		let res = await this.request('POST', '/api/auth/login', {
			anon: true,
			body: { email, password },
			captureOrigin: origin => issuedOrigin = origin,
		});
		await this.setToken(res.token, issuedOrigin);
		// After setToken, not before: setToken(null) clears this cache, and a
		// sign-in that raced it would leave the new account wearing nothing.
		this.cacheUser(res.user);
		return res.user;
	};

	/**
	 * Forget the token locally. The server is not told: on this API, forgetting
	 * the token IS the logout. logoutAll() is the one that revokes remotely.
	 */
	this.logout = async function () {
		await this.setToken(null);
	};

	/** Revoke every token ever issued to this account, on every device. */
	this.logoutAll = async function () {
		try {
			await this.request('POST', '/api/auth/logout-all');
		}
		finally {
			// Local sign-out has to happen even if the call failed, or the user
			// is left holding a token they asked to destroy.
			await this.setToken(null);
		}
	};

	this.me = async function () {
		let user = await this.request('GET', '/api/auth/me');
		// The one round trip every surface already makes, so the cache is
		// refreshed by simply being signed in rather than by anyone remembering
		// to refresh it. A setting changed in the web client lands here at the
		// next window open.
		this.cacheUser(user);
		return user;
	};

	/**
	 * Edit the profile. Only the fields given are sent.
	 *
	 * The backend's UpdateMeRequest is `extra="forbid"` and distinguishes
	 * "omitted" from "null", so a key must be left out to mean "leave alone" --
	 * sending null means *clear*, and for display_name that is a legitimate
	 * request (the account goes back to being known by its address). The
	 * address itself is not editable here and the backend refuses it: changing
	 * a login identifier needs proof of ownership, and Pharos sends no mail.
	 *
	 * @param {Object} patch
	 * @param {String|null} [patch.displayName] - null or blank clears it
	 * @return {Promise<Object>} the updated user
	 */
	this.updateMe = async function (patch = {}) {
		let body = {};
		if (patch.displayName !== undefined) {
			// Blank is sent as null rather than "": the backend collapses
			// whitespace and stores null for an empty result anyway, so this
			// says the same thing in the shape the schema documents.
			let name = String(patch.displayName || '').trim();
			body.display_name = name ? name.slice(0, MAX_DISPLAY_NAME) : null;
		}
		if (patch.pdfTranslation !== undefined) {
			body.pdf_translation = !!patch.pdfTranslation;
		}
		if (!Object.keys(body).length) {
			// The backend answers an empty patch with a 400. Refusing here keeps
			// a no-op save from looking like a server error.
			throw new Error('No fields to update');
		}
		let user = await this.request('PATCH', '/api/auth/me', { body });
		this.cacheUser(user);
		return user;
	};

	/**
	 * Whether the backend is reachable and the stored token still works.
	 *
	 * @return {Promise<Object|null>} the user, or null when signed out
	 */
	this.verify = async function () {
		if (!this.hasCredentials()) {
			return null;
		}
		try {
			return await this.me();
		}
		catch (e) {
			if (e instanceof this.SignedOutError) {
				return null;
			}
			throw e;
		}
	};
};
