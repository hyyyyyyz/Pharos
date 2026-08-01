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
Zotero.Pharos = {};

Zotero.Pharos.API = new function () {
	/**
	 * Where the login manager keeps the bearer token.
	 *
	 * Mirrors how Zotero stores its own Web API key (see
	 * xpcom/sync/syncLocal.js:32): the login manager, with the secret
	 * additionally encrypted through OSKeyStore rather than sitting in
	 * logins.json in the clear. A Pharos token is a live credential for the
	 * user's whole library, so it gets the same treatment as Zotero's.
	 *
	 * The host is deliberately not "chrome://zotero" -- sharing that host with
	 * Zotero's own entry would put two unrelated credentials in one bucket,
	 * where clearing one could clear the other.
	 */
	const LOGIN_HOST = 'chrome://pharos';
	const LOGIN_REALM = 'Pharos API (encrypted)';

	/**
	 * Requests get a generous timeout because translation upload is a multi-MB
	 * POST over a consumer uplink; the job itself is asynchronous and polled, so
	 * this only has to cover the transfer.
	 */
	const UPLOAD_TIMEOUT = 300000;
	const DEFAULT_TIMEOUT = 30000;

	let _cachedToken = null;

	/**
	 * Base URL of the backend, without a trailing slash.
	 *
	 * A pref rather than a constant because Pharos is open source and meant to
	 * be self-hostable -- someone running their own instance changes this and
	 * everything else follows.
	 */
	this.getBaseURL = function () {
		let url = Zotero.Prefs.get('pharos.baseURL') || 'https://pharos.selab.top';
		return url.replace(/\/+$/, '');
	};

	this.setBaseURL = function (url) {
		Zotero.Prefs.set('pharos.baseURL', String(url || '').replace(/\/+$/, ''));
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

	/**
	 * Whether a token is stored. Synchronous, so menus and UI state can consult
	 * it without awaiting a decrypt.
	 */
	this.hasCredentials = function () {
		return !!this._getLoginInfo();
	};

	this.getToken = async function () {
		if (_cachedToken !== null) {
			return _cachedToken;
		}
		let login = this._getLoginInfo();
		if (!login) {
			return null;
		}
		try {
			_cachedToken = await Zotero.OSKeyStore.decrypt(login.password);
		}
		catch (e) {
			// A token that cannot be decrypted is not recoverable, and leaving it
			// in place would make every request 401 forever with no way out from
			// the UI. Drop it so the user is asked to sign in again.
			Zotero.logError(e);
			await this.setToken(null);
			return null;
		}
		return _cachedToken;
	};

	this.setToken = async function (token) {
		let oldLogin = this._getLoginInfo();

		if (!token) {
			if (oldLogin) {
				Services.logins.removeLogin(oldLogin);
			}
			_cachedToken = null;
			return;
		}

		let encrypted = await Zotero.OSKeyStore.encrypt(token);
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
	 * @param {String} [options.responseType] - e.g. "arraybuffer" for PDF bytes
	 * @param {Number} [options.timeout]
	 * @return {Promise<Object|ArrayBuffer>} Parsed JSON, or the raw response when
	 *     responseType is set
	 */
	this.request = async function (method, path, options = {}) {
		let headers = Object.assign({}, options.headers || {});
		let body = options.body;

		if (body && typeof body == 'object' && !(body instanceof FormData)) {
			body = JSON.stringify(body);
			headers['Content-Type'] = 'application/json';
		}
		// No Content-Type for FormData, deliberately: the platform has to set it
		// so that it can append the multipart boundary.

		if (!options.anon) {
			let token = await this.getToken();
			if (!token) {
				throw new Zotero.Pharos.API.SignedOutError();
			}
			headers.Authorization = 'Bearer ' + token;
		}

		let xhr;
		try {
			xhr = await Zotero.HTTP.request(method, this.getBaseURL() + path, {
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

	this.login = async function (email, password) {
		let res = await this.request('POST', '/api/auth/login', {
			anon: true,
			body: { email, password },
		});
		await this.setToken(res.token);
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
		return this.request('GET', '/api/auth/me');
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
