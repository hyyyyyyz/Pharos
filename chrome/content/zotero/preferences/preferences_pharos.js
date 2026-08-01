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
 * The Pharos account pane: sign in, sign out, and point the client at a backend.
 *
 * Everything that needs the server -- translation, AI chat, the daily digest --
 * goes through the token this pane obtains, so this is the one place a user has
 * to visit before any of it works.
 */
Zotero_Preferences.Pharos = {
	init: async function () {
		document.getElementById('pharos-base-url').value = Zotero.Pharos.API.getBaseURL();

		// Optimistic first paint from the cached email, then corrected once the
		// server has confirmed the token. Without the cached value the pane
		// flashes "signed out" on every open for anyone who is signed in.
		this._render(Zotero.Prefs.get('pharos.accountEmail'));

		if (Zotero.Pharos.API.hasCredentials()) {
			try {
				let user = await Zotero.Pharos.API.verify();
				if (user) {
					Zotero.Prefs.set('pharos.accountEmail', user.email);
					this._render(user.email);
				}
				else {
					// verify() returning null means the token was rejected and
					// has already been cleared.
					Zotero.Prefs.set('pharos.accountEmail', '');
					this._render(null);
				}
			}
			catch (e) {
				// The server being unreachable is not a reason to declare the
				// user signed out -- they still hold a token, and a flight with
				// no wifi should not log anyone out.
				Zotero.logError(e);
			}
		}

		// Enter in either field submits, which is what a two-field sign-in form
		// is expected to do.
		for (let id of ['pharos-email', 'pharos-password']) {
			document.getElementById(id).addEventListener('keypress', (event) => {
				if (event.key == 'Enter') {
					this.signIn();
				}
			});
		}
	},

	_render: function (email) {
		let signedIn = !!email && Zotero.Pharos.API.hasCredentials();
		document.getElementById('pharos-signed-in').hidden = !signedIn;
		document.getElementById('pharos-signed-out').hidden = signedIn;
		if (signedIn) {
			document.getElementById('pharos-account-email').textContent = email;
		}
	},

	_setMessage: function (text) {
		document.getElementById('pharos-message').textContent = text || '';
	},

	signIn: async function () {
		let email = document.getElementById('pharos-email').value.trim();
		let password = document.getElementById('pharos-password').value;
		if (!email || !password) {
			this._setMessage(Zotero.getString('pharos-prefs-error-incomplete'));
			return;
		}

		let button = document.getElementById('pharos-sign-in');
		button.disabled = true;
		this._setMessage(Zotero.getString('pharos-prefs-signing-in'));
		try {
			let user = await Zotero.Pharos.API.login(email, password);
			Zotero.Prefs.set('pharos.accountEmail', user.email);
			// Clear the password field before anything else can go wrong, so a
			// later failure cannot leave it sitting in the DOM.
			document.getElementById('pharos-password').value = '';
			this._setMessage('');
			this._render(user.email);
		}
		catch (e) {
			Zotero.logError(e);
			// e.message carries the backend's own `detail` for a failed sign-in,
			// which distinguishes a wrong password from an unreachable server.
			this._setMessage(e.message || Zotero.getString('pharos-prefs-error-sign-in'));
		}
		finally {
			button.disabled = false;
		}
	},

	signOut: async function () {
		await Zotero.Pharos.API.logout();
		Zotero.Prefs.set('pharos.accountEmail', '');
		this._render(null);
	},

	signOutEverywhere: async function () {
		let confirmed = Services.prompt.confirm(
			window,
			Zotero.getString('pharos-prefs-sign-out-all'),
			Zotero.getString('pharos-prefs-sign-out-all-confirm')
		);
		if (!confirmed) {
			return;
		}
		try {
			await Zotero.Pharos.API.logoutAll();
		}
		catch (e) {
			// logoutAll() signs out locally even when the remote call fails, so
			// the pane still has to end up in the signed-out state.
			Zotero.logError(e);
		}
		Zotero.Prefs.set('pharos.accountEmail', '');
		this._render(null);
	},

	saveBaseURL: function () {
		let field = document.getElementById('pharos-base-url');
		let url = field.value.trim();
		if (!url) {
			field.value = Zotero.Pharos.API.getBaseURL();
			return;
		}
		Zotero.Pharos.API.setBaseURL(url);
		field.value = Zotero.Pharos.API.getBaseURL();

		// A token is only meaningful against the server that issued it, so
		// pointing the client somewhere else has to sign the user out rather
		// than send that server someone else's bearer token.
		if (Zotero.Pharos.API.hasCredentials()) {
			this.signOut();
		}
	},
};
