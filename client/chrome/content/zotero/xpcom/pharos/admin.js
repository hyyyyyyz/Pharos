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
 * Every call here 403s for an ordinary account: the backend gates them on
 * `require_admin`, so the client never decides who may do what. What the client
 * decides is only whether to *offer* the screen -- hiding a module the user
 * cannot use is courtesy, not enforcement, and nothing here relies on it.
 *
 * The backend's admin schemas are snake_case (plain BaseModels, like the daily
 * digest's). This module takes camelCase from its callers and maps at the wire,
 * the same way Projects does, because `UserPatch` is declared `extra="forbid"`
 * -- a stray key is a 422, not an ignored field.
 */
Zotero.Pharos.Admin = new function () {
	/**
	 * Cached answer to "is this account an administrator".
	 *
	 * A pref rather than a variable because the module rail has to decide
	 * whether to draw the entry while it renders, before any request could have
	 * come back -- the same reason preferences_pharos.js caches the account
	 * email. A stale `true` shows an operator a screen whose every request then
	 * 403s; a stale `false` hides it for one session. Both self-correct on the
	 * next refresh(), and neither grants anything.
	 */
	const IS_ADMIN_PREF = 'pharos.isAdmin';

	/** Matches the backend's _MAX_PAGE; asking for more is a 422. */
	const MAX_PAGE = 200;

	/**
	 * Whether the signed-in account is known to be an administrator.
	 *
	 * Synchronous, so the rail and the Tools menu can consult it while building.
	 * Gated on credentials as well as the cached flag: signing out has to hide
	 * the module immediately rather than at the next successful refresh().
	 *
	 * @return {Boolean}
	 */
	this.isAdmin = function () {
		return Zotero.Pharos.API.hasCredentials() && !!Zotero.Prefs.get(IS_ADMIN_PREF);
	};

	/**
	 * Who this token belongs to, caching the admin flag on the way past.
	 *
	 * The console needs the account for more than the gate -- it marks the
	 * operator's own row, where the destructive actions are withheld -- so this
	 * hands back the whole user rather than a boolean, and is the one round trip
	 * both jobs share.
	 *
	 * @return {Promise<Object|null>} the user, or null when signed out or when
	 *     the token was rejected (verify() has cleared it by then)
	 * @throws when the server cannot be reached
	 */
	this.identify = async function () {
		if (!Zotero.Pharos.API.hasCredentials()) {
			Zotero.Prefs.set(IS_ADMIN_PREF, false);
			return null;
		}
		let user = await Zotero.Pharos.API.verify();
		Zotero.Prefs.set(IS_ADMIN_PREF, !!(user && user.is_admin));
		return user;
	};

	/**
	 * Re-check the cached admin flag, for callers that only need to know whether
	 * to draw the entry.
	 *
	 * Never throws. An unreachable server is not evidence about anyone's role,
	 * so a failed check keeps the cached answer rather than demoting the user
	 * out of their own console the first time the wifi drops -- the same
	 * reasoning that keeps the preferences pane from declaring a signed-in user
	 * signed out.
	 *
	 * @return {Promise<Boolean>} the best answer available
	 */
	this.refresh = async function () {
		try {
			let user = await this.identify();
			return !!(user && user.is_admin);
		}
		catch (e) {
			Zotero.logError(e);
			return this.isAdmin();
		}
	};

	/**
	 * Instance-wide totals for the console header.
	 *
	 * @return {Promise<Object>} an AdminStats
	 */
	this.getStats = function () {
		return Zotero.Pharos.API.request('GET', '/api/admin/stats');
	};

	/**
	 * One page of accounts, newest first.
	 *
	 * @param {Object} [options]
	 * @param {String} [options.query] - matches email or display name
	 * @param {Integer} [options.limit]
	 * @param {Integer} [options.offset]
	 * @return {Promise<Object>} an AdminUserPage: { users, total, limit, offset }
	 */
	this.listUsers = function ({ query, limit, offset } = {}) {
		let params = new URLSearchParams();
		if (query && query.trim()) {
			params.set('q', query.trim());
		}
		params.set('limit', String(Math.min(limit || MAX_PAGE, MAX_PAGE)));
		if (offset) {
			params.set('offset', String(offset));
		}
		return Zotero.Pharos.API.request('GET', `/api/admin/users?${params}`);
	};

	/**
	 * Change another account's role, status, or translation setting.
	 *
	 * The backend refuses the two lockout cases -- demoting or deactivating
	 * yourself, and removing the last administrator -- with a 409 whose `detail`
	 * explains which. api.request() surfaces that as the error message, so
	 * callers should show it rather than a generic failure.
	 *
	 * @param {String} userID
	 * @param {Object} patch - any of isAdmin, isActive, pdfTranslation, displayName
	 * @return {Promise<Object>} the updated AdminUserOut
	 */
	this.updateUser = function (userID, patch = {}) {
		let body = {};
		if (patch.isAdmin !== undefined) {
			body.is_admin = !!patch.isAdmin;
		}
		if (patch.isActive !== undefined) {
			body.is_active = !!patch.isActive;
		}
		if (patch.pdfTranslation !== undefined) {
			body.pdf_translation = !!patch.pdfTranslation;
		}
		if (patch.displayName !== undefined) {
			body.display_name = patch.displayName;
		}
		if (!Object.keys(body).length) {
			// The backend answers an empty patch with a 400. Refusing here keeps
			// a no-op click from looking like a server error.
			throw new Error('No fields to update');
		}
		return Zotero.Pharos.API.request(
			'PATCH', `/api/admin/users/${encodeURIComponent(userID)}`, { body }
		);
	};

	/**
	 * Permanently delete an account and everything it owns.
	 *
	 * `confirmEmail` is not a formality the UI could skip: the backend compares
	 * it against the target's address and refuses on a mismatch. This is the one
	 * call in Pharos that destroys another person's work, so a mistyped id fails
	 * instead of erasing the wrong researcher.
	 *
	 * @param {String} userID
	 * @param {String} confirmEmail - the target's own address, as typed
	 * @return {Promise}
	 */
	this.deleteUser = function (userID, confirmEmail) {
		let params = new URLSearchParams({ confirm_email: String(confirmEmail || '') });
		return Zotero.Pharos.API.request(
			'DELETE', `/api/admin/users/${encodeURIComponent(userID)}?${params}`
		);
	};

	/**
	 * Whether what the operator typed identifies the account they are deleting.
	 *
	 * Case-insensitive and trimmed, which is exactly what the backend does to
	 * the value it receives. Addresses are stored casefolded (see
	 * `normalize_email` in the backend's auth module), so lowercasing both sides
	 * here cannot accept something the server will then reject.
	 *
	 * @param {String} typed
	 * @param {String} email
	 * @return {Boolean}
	 */
	this.confirmationMatches = function (typed, email) {
		if (!typed || !email) {
			return false;
		}
		return String(typed).trim().toLowerCase() == String(email).trim().toLowerCase();
	};

	/**
	 * Which model providers the server is configured with.
	 *
	 * Read-only, and it never carries a key -- only whether one is present and
	 * its last four characters. Changing a key means editing the server's `.env`
	 * and restarting.
	 *
	 * @return {Promise<Object>} a ProvidersOut
	 */
	this.getProviders = function () {
		return Zotero.Pharos.API.request('GET', '/api/admin/providers');
	};

	/**
	 * Send one minimal completion to a provider and report the outcome.
	 *
	 * The only way to tell "a key is configured" from "the key works": a typo'd
	 * key and a decommissioned relay both look perfectly healthy in the listing.
	 *
	 * @param {String} name
	 * @return {Promise<Object>} a ProbeResult: { name, ok, latency_ms, detail }
	 */
	this.probeProvider = function (name) {
		return Zotero.Pharos.API.request(
			'POST', `/api/admin/providers/${encodeURIComponent(name)}/probe`,
			// A probe waits on a third-party vendor, and the backend gives it ten
			// seconds before giving up. The default 30s request timeout would cut
			// in first only on a pathological connection, but it is the wrong
			// number to be relying on here.
			{ timeout: 20000 }
		);
	};

	/**
	 * Whether translation has silently fallen back to the free engine.
	 *
	 * `translator_config()` returns Bing whenever an LLM translator is selected
	 * but has no usable credentials, so translation keeps working and quietly
	 * gets worse. That fallback is the ONLY way a non-Bing selection yields an
	 * effective engine of "bing", which makes this exact rather than heuristic.
	 *
	 * Deliberately not the web client's test (`effective != configured`): for
	 * `openai` and `custom` the effective type is the engine's name for the wire
	 * format, "openai_compatible", which never equals the provider name -- so
	 * that test warns of a degradation that has not happened.
	 *
	 * @param {Object} providers - a ProvidersOut
	 * @return {Boolean}
	 */
	this.isTranslationDegraded = function (providers) {
		if (!providers) {
			return false;
		}
		let configured = String(providers.translator || '').toLowerCase();
		if (configured == 'bing' || configured == 'google') {
			return false;
		}
		return String(providers.effective_translator || '').toLowerCase() == 'bing';
	};
};
