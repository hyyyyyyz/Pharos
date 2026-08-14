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
 * Desktop update detection.
 *
 * Pharos owns its update channel end to end rather than reusing Mozilla's
 * [AppUpdate] machinery: that path pointed at Zotero's update server, which
 * would serve an official Zotero build as an "update" and silently replace
 * Pharos (DECISIONS.md 6). The check is therefore a small public API call the
 * backend answers from the newest desktop-v* GitHub release, and the client's
 * job is to notice, tell the user, and hand them the installer.
 *
 * The download itself is deliberate: installing is the user's action. Pharos
 * downloads nothing in the background and never touches the running app --
 * a build that replaces itself mid-session is a support problem, and an
 * unsigned macOS bundle cannot stage an update anyway.
 *
 * The check is anonymous: a signed-out user is the one most likely to be on an
 * old build, and the payload is the same public release page anyone can read.
 *
 * UI surfaces subscribe to the observer topics below; none of them has to know
 * when the check runs or where the answer came from.
 */
Zotero.Pharos.Updates = new function () {
	const CHECK_PATH = '/api/updates/desktop/latest';
	const CHECK_TIMEOUT = 15000;
	//: First check after a window opens, so startup work wins the connection.
	const STARTUP_DELAY = 20000;
	//: Re-check while the app stays open. Cheap and anonymous.
	const CHECK_INTERVAL = 6 * 60 * 60 * 1000;

	/** Version the user has dismissed; a newer one clears it implicitly. */
	const IGNORED_VERSION_PREF = 'pharos.update.ignoredVersion';

	/** Fired after every finished check, with a serialised state as `data`. */
	this.TOPIC_CHECKED = 'pharos-update-checked';

	/** Fired only when a check finds an update the user has not ignored. */
	this.TOPIC_AVAILABLE = 'pharos-update-available';

	// States: "checking", "latest", "available", "ignored", "unavailable",
	// "error". "unavailable" means the server answered without advertising a
	// version -- an explicit no, not a failure.
	let _lastState = null;
	let _started = false;
	let _checking = false;
	let _timer = null;

	/**
	 * The build's own version, as shipped in application.ini.
	 *
	 * Source builds carry a `.SOURCE` suffix that the comparison strips: a
	 * development build and a release of the same number are the same version
	 * for update purposes, and neither nags about the other.
	 *
	 * @return {String}
	 */
	this.currentVersion = function () {
		return String(Zotero.version || '');
	};

	/**
	 * Numeric parts of a version string, or null when nothing numeric is there.
	 *
	 * Strips a prerelease suffix ("1.3.1-beta") and the source-build marker
	 * ("1.3.1.SOURCE") before splitting, because neither moves the update
	 * decision.
	 *
	 * @param {String} version
	 * @return {Array<String>|null}
	 */
	function _versionParts(version) {
		let cleaned = String(version || '').split('-')[0].split('.SOURCE')[0].trim();
		let parts = cleaned.split('.');
		if (!parts.some(part => /\d/.test(part))) {
			return null;
		}
		return parts;
	}

	/**
	 * Compare two versions numerically, segment by segment.
	 *
	 * @param {String} a
	 * @param {String} b
	 * @return {Integer|null} 1 when a is newer, -1 when older, 0 when equal,
	 *     null when either side has no numeric part (incomparable, so callers
	 *     must answer "no update" rather than invent one)
	 */
	this.compareVersions = function (a, b) {
		let pa = _versionParts(a);
		let pb = _versionParts(b);
		if (!pa || !pb) {
			return null;
		}
		let length = Math.max(pa.length, pb.length);
		for (let i = 0; i < length; i++) {
			let na = parseInt(pa[i]) || 0;
			let nb = parseInt(pb[i]) || 0;
			if (na != nb) {
				return na > nb ? 1 : -1;
			}
		}
		return 0;
	};

	/**
	 * The last finished check, or null before one has run.
	 *
	 * Synchronous, so a surface can paint its current best answer without
	 * waiting for the network -- the same pattern the rail uses for accounts.
	 *
	 * @return {Object|null} { status, version, url, notes, error }
	 */
	this.getState = function () {
		return _lastState;
	};

	/**
	 * Whether the newest advertised version has been dismissed.
	 *
	 * @param {String} version
	 * @return {Boolean}
	 */
	this.isIgnored = function (version) {
		return Zotero.Prefs.get(IGNORED_VERSION_PREF) == version;
	};

	/**
	 * Forget that a version was dismissed, so the banner may show it again.
	 *
	 * Clearing rather than storing a sentinel means an unset pref and a clear
	 * are the same thing -- a stale "ignored" marker from an uninstalled old
	 * version is harmless, and nothing downstream has to decode it.
	 */
	this.clearIgnored = function () {
		if (Zotero.Prefs.prefHasUserValue(IGNORED_VERSION_PREF)) {
			Zotero.Prefs.clear(IGNORED_VERSION_PREF);
		}
	};

	/**
	 * Dismiss a version and republish the state.
	 *
	 * Called by the rail's ignore button. The check itself is not re-run: the
	 * version is still available, it is just not advertised to this profile
	 * again.
	 *
	 * @param {String} version
	 */
	this.ignore = function (version) {
		Zotero.Prefs.set(IGNORED_VERSION_PREF, version);
		if (_lastState && _lastState.version == version) {
			_lastState.status = 'ignored';
			_notify(this.TOPIC_CHECKED, _lastState);
		}
	};

	/**
	 * Begin the startup-and-then-hourly schedule.
	 *
	 * Idempotent and module-scoped: every main window's rail calls this, but
	 * the timers live once. Never throws, and a failed check only logs -- an
	 * unreachable update endpoint must not put anything in the user's face.
	 *
	 * In tests the timers stay off: a 20-second fuse would fire a real network
	 * request in the middle of another file's stubbed world and announce a
	 * phantom update. Test files drive check() directly.
	 */
	this.start = function () {
		if (_started || Zotero.test) {
			return;
		}
		_started = true;
		setTimeout(() => {
			this.check({ reason: 'startup' }).catch(e => Zotero.logError(e));
		}, STARTUP_DELAY);
		_timer = setInterval(() => {
			this.check({ reason: 'interval' }).catch(e => Zotero.logError(e));
		}, CHECK_INTERVAL);
	};

	/**
	 * Ask the backend what the newest desktop build is and publish the answer.
	 *
	 * One check at a time: overlapping checks would race on _lastState. The
	 * caller waits for nothing new -- it gets the state the last finished
	 * check produced, which is the same answer the observer topics carry.
	 *
	 * @param {Object} [options]
	 * @param {String} [options.reason] - for logs only
	 * @return {Promise<Object>} the resulting state
	 */
	this.check = async function ({ reason } = {}) {
		if (_checking) {
			return _lastState;
		}
		_checking = true;
		let state;
		try {
			let payload = await Zotero.Pharos.API.request(
				'GET', CHECK_PATH, { anon: true, timeout: CHECK_TIMEOUT }
			);
			let latest = payload && typeof payload.version == 'string'
				? payload.version.trim()
				: '';
			if (!latest) {
				// The server answered but advertises nothing: no release has
				// been published, or the operator has not announced one yet.
				state = { status: 'unavailable', version: null, url: null, notes: null };
			}
			else {
				let compared = this.compareVersions(latest, this.currentVersion());
				if (compared === null || compared <= 0) {
					state = {
						status: 'latest',
						version: latest,
						url: payload.url || null,
						notes: null,
					};
				}
				else if (this.isIgnored(latest)) {
					state = {
						status: 'ignored',
						version: latest,
						url: payload.url || null,
						notes: null,
					};
				}
				else {
					state = {
						status: 'available',
						version: latest,
						url: payload.url || null,
						notes: payload.notes || null,
					};
				}
			}
		}
		catch (e) {
			Zotero.logError(new Error(`Pharos update check failed (${reason || 'manual'}): ${e}`));
			state = {
				status: 'error',
				version: null,
				url: null,
				notes: null,
				error: e && e.message ? String(e.message) : 'check failed',
			};
		}
		_lastState = state;
		_checking = false;
		_notify(this.TOPIC_CHECKED, state);
		if (state.status == 'available') {
			_notify(this.TOPIC_AVAILABLE, state);
		}
		return state;
	};

	function _notify(topic, state) {
		Services.obs.notifyObservers(null, topic, JSON.stringify(state));
	}

	/**
	 * Open the release page for a state.
	 *
	 * The one user-facing action this module offers. Everything else -- the
	 * download, the mount, the install -- is the user's own.
	 *
	 * @param {Object} state - an available/ignored state with a url
	 */
	this.openRelease = function (state) {
		if (!state || !state.url) {
			return;
		}
		Zotero.launchURL(state.url);
	};
};
