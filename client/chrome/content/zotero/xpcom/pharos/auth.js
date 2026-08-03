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
 * When the sign-in window stands in front of the application, and how it opens.
 *
 * Here rather than in zoteroPane.js because the decision is made BEFORE any
 * window exists: `commandLineHandler.js` asks at startup, in place of opening
 * the library. That ordering is the whole point. The gate used to be a modal
 * thrown over an already-visible library, which got the shape wrong twice --
 * the library is what the user is being asked to unlock, so showing it behind
 * the question answers it; and a modal cannot be moved aside, so the one thing
 * on screen that might explain what Pharos is was covered by the box asking
 * them to sign in to it.
 */
Zotero.Pharos.Auth = new function () {
	/** Where the sign-in window lives. */
	const GATE_URL = 'chrome://zotero/content/pharosAuth.xhtml';

	/**
	 * Whether the sign-in window should open instead of the library.
	 *
	 * Every branch below is a case where asking again would be wrong rather than
	 * merely unnecessary.
	 *
	 * @return {Boolean}
	 */
	this.shouldGate = function () {
		// The test harness drives the real application, and a window nothing
		// dismisses would leave every suite waiting on a login form.
		if (Zotero.test) {
			return false;
		}
		// Already answered, either way. `skipped` is deliberately sticky: a user
		// who chose to work locally is not asked again on every start, and the
		// rail's account footer is how they change their mind.
		if (Zotero.Pharos.API.hasCredentials()
				|| Zotero.Prefs.get('pharos.auth.skipped')) {
			return false;
		}
		// A second invocation of an already-running application -- opening a
		// file, say. The library is up; the question was settled at startup.
		if (Zotero.getMainWindow()) {
			return false;
		}
		return true;
	};

	/**
	 * Open the sign-in window as the application's first window.
	 *
	 * Not modal, and not a child of anything: at this point there is no parent
	 * to be modal to. It is an ordinary top-level window, so it can be moved,
	 * and the user can read whatever else is on their screen while deciding.
	 *
	 * Whichever way it finishes -- signed in, registered, or skipped -- it opens
	 * the main window itself and closes. Closing it WITHOUT answering opens
	 * nothing and the application exits, which is what a sign-in window does;
	 * 暂不登录 is there for the user who wants in without an account.
	 *
	 * @return {ChromeWindow}
	 */
	this.openGate = function () {
		return Services.ww.openWindow(
			null,
			GATE_URL,
			'_blank',
			// No `modal`: there is nothing to be modal to, and a modal here would
			// spin a nested event loop during startup.
			'chrome,dialog=no,centerscreen,resizable',
			null
		);
	};
};
