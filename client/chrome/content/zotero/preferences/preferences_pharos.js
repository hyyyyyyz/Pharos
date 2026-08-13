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
 * The Pharos account pane: sign in, sign out, and manage account preferences.
 *
 * Everything that needs the server -- translation, AI chat, the daily digest --
 * goes through the token this pane obtains, so this is the one place a user has
 * to visit before any of it works.
 */
Zotero_Preferences.Pharos = {
	init: async function () {
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
			// Only when the field is not being edited: _render() runs on every
			// account refresh, and overwriting what someone is halfway through
			// typing is how a rename silently loses its last few characters.
			let field = document.getElementById('pharos-display-name');
			if (field && document.activeElement != field) {
				field.value = Zotero.Pharos.API.getDisplayName();
			}
			let box = document.getElementById('pharos-pdf-translation');
			if (box) {
				box.checked = Zotero.Pharos.Translate.isEnabled();
			}
		}
	},

	/**
	 * Save the display name.
	 *
	 * Blank is a legitimate value and means "go back to showing my address" --
	 * so this is not gated on the field being non-empty, and the note below says
	 * which of the two just happened rather than only reporting success.
	 */
	/**
	 * Turn whole-PDF translation on or off for this account.
	 *
	 * Applied optimistically: the checkbox is what the user just clicked, and
	 * reverting it under them on a network blip reads as the click not landing.
	 * A failure restores it and says so instead.
	 */
	savePdfTranslation: async function () {
		let box = document.getElementById('pharos-pdf-translation');
		let note = document.getElementById('pharos-pdf-translation-note');
		let wanted = box.checked;

		box.disabled = true;
		try {
			await Zotero.Pharos.API.updateMe({ pdfTranslation: wanted });
			note.hidden = true;
			// Every surface that reads the flag -- the item pane section, the
			// item tree column, the context menu -- reads it from the pref that
			// updateMe() has just written, so nothing else needs telling.
		}
		catch (e) {
			Zotero.logError(e);
			box.checked = !wanted;
			note.textContent = e.message
				|| Zotero.getString('pharos-prefs-pdf-translation-failed');
			note.hidden = false;
		}
		finally {
			box.disabled = false;
		}
	},

	saveDisplayName: async function () {
		let field = document.getElementById('pharos-display-name');
		let button = document.getElementById('pharos-display-name-save');
		let note = document.getElementById('pharos-display-name-note');
		let name = field.value.trim();

		button.disabled = true;
		try {
			let user = await Zotero.Pharos.API.updateMe({ displayName: name });
			// From the server's answer, not from what was typed: it trims and
			// collapses whitespace, so echoing the input would show a name the
			// account does not have.
			field.value = String((user && user.display_name) || '');
			note.textContent = Zotero.getString(field.value
				? 'pharos-prefs-display-name-saved'
				: 'pharos-prefs-display-name-cleared');
			note.hidden = false;
			// The rail is the surface this exists for, and it is not rebuilt by
			// the preferences window closing.
			for (let win of Zotero.getMainWindows()) {
				win.document.getElementById('pharos-rail')?.refreshAccount?.();
			}
		}
		catch (e) {
			Zotero.logError(e);
			note.textContent = e.message
				|| Zotero.getString('pharos-prefs-display-name-failed');
			note.hidden = false;
		}
		finally {
			button.disabled = false;
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
};
