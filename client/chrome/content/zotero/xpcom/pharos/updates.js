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
 * The download itself: macOS builds install in place -- the installer streams
 * from the service (which resolves the release asset, private repository or
 * not), is SHA-256 verified, and replaces the running bundle, then offers a
 * one-click restart. Windows (portable archive) and Linux (tarball) cannot
 * safely replace a running layout and keep the release-page handoff.
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
	 * The fallback for platforms the in-app installer cannot serve, and for a
	 * user who prefers to install by hand.
	 *
	 * @param {Object} state - an available/ignored state with a url
	 */
	this.openRelease = function (state) {
		if (!state || !state.url) {
			return;
		}
		Zotero.launchURL(state.url);
	};

	/**
	 * Whether this build can install an update in place.
	 *
	 * macOS: the .app bundle can be swapped while running (the old process
	 * keeps its files; the next launch uses the new bundle), so the full
	 * download -> verify -> install -> restart flow is offered. Windows ships
	 * a portable archive and Linux a tarball; neither can replace a running
	 * layout safely, and neither can be tested from this repository, so both
	 * keep the release-page handoff.
	 *
	 * @return {Boolean}
	 */
	this.canSelfInstall = function () {
		return Zotero.isMac;
	};

	/**
	 * Download the platform installer with progress, then install and restart.
	 *
	 * The whole flow lives here so the rail banner and the preferences pane
	 * share one progress state machine: downloading (percent) -> verifying ->
	 * installing -> installed (restart required). Every transition publishes
	 * through TOPIC_CHECKED, which is what keeps the two surfaces in step.
	 *
	 * @param {Object} state - an available/ignored update state
	 * @return {Promise}
	 */
	this.downloadAndInstall = async function (state) {
		if (_installing) {
			return;
		}
		if (!state || !state.version || !this.canSelfInstall()) {
			this.openRelease(state);
			return;
		}
		_installing = true;
		let publish = (patch) => {
			_lastState = { ...(_lastState || state), status: 'downloading', ...patch };
			_notify(this.TOPIC_CHECKED, _lastState);
		};
		try {
			let target = await _download(state.version, (loaded, total) => {
				publish({ phase: 'downloading', percent: total ? Math.round(100 * loaded / total) : null });
			});
			publish({ phase: 'verifying' });
			let digest = await _sha256(target.buffer);
			if (digest !== target.sha256) {
				throw new Error('checksum mismatch');
			}
			publish({ phase: 'installing' });
			await _installMac(target.file, state.version);
			_lastState = { ..._lastState, status: 'installed', phase: 'installed' };
			_notify(this.TOPIC_CHECKED, _lastState);
		}
		catch (e) {
			Zotero.logError(e);
			_lastState = {
				..._lastState,
				status: 'available',
				phase: null,
				error: e && e.message ? String(e.message) : 'update failed',
			};
			_notify(this.TOPIC_CHECKED, _lastState);
		}
		finally {
			_installing = false;
		}
	};

	/**
	 * Quit this build and start the freshly installed one.
	 *
	 * The relauncher is a detached shell: it sleeps long enough for the old
	 * process to release the library lock, then opens the new bundle. The old
	 * process quits immediately after spawning it.
	 */
	this.restartAfterInstall = function () {
		if (!_lastState || _lastState.status !== 'installed' || !_appDir) {
			return;
		}
		let appPath = _appDir.path;
		// Not awaited on purpose: the promise resolves only when the shell
		// exits, and the shell outlives this process by design.
		Zotero.Utilities.Internal.exec('/bin/sh', [
			'-c', `sleep 2 && open -n "${appPath}"`,
		]).catch(e => Zotero.logError(e));
		setTimeout(() => {
			Zotero.Utilities.Internal.quit(true, false);
		}, 250);
	};

	let _installing = false;
	let _appDir = null;

	async function _download(version, onProgress) {
		let base = Zotero.Pharos.API.getBaseURL();
		let url = `${base}/api/updates/desktop/download?platform=mac&version=${encodeURIComponent(version)}`;
		let buffer = await _xhrArrayBuffer(url, onProgress);
		let targetDir = Zotero.getTempDirectory();
		targetDir.append('Pharos-update');
		await IOUtils.makeDirectory(targetDir.path, { ignoreExisting: true });
		let file = targetDir.clone();
		file.append(`Pharos-${version}-mac.zip`);
		await IOUtils.write(file.path, new Uint8Array(buffer));
		return { buffer, file, sha256: _xhrMeta.sha256 };
	}

	// XHR response headers are read once, right after load, before the
	// response is replaced; the download helper stashes them here.
	let _xhrMeta = { sha256: '' };

	function _xhrArrayBuffer(url, onProgress) {
		return new Promise((resolve, reject) => {
			let xhr = new XMLHttpRequest();
			xhr.open('GET', url);
			xhr.responseType = 'arraybuffer';
			xhr.onprogress = (event) => {
				if (event.lengthComputable) {
					onProgress(event.loaded, event.total);
				}
			};
			xhr.onload = () => {
				if (xhr.status !== 200) {
					reject(new Error(`download failed (HTTP ${xhr.status})`));
					return;
				}
				_xhrMeta.sha256 = xhr.getResponseHeader('X-Pharos-Asset-SHA256') || '';
				resolve(xhr.response);
			};
			xhr.onerror = () => reject(new Error('download failed'));
			xhr.send();
		});
	}

	async function _sha256(buffer) {
		let digest = await crypto.subtle.digest('SHA-256', buffer);
		return Array.from(new Uint8Array(digest))
			.map(byte => byte.toString(16).padStart(2, '0'))
			.join('');
	}

	async function _installMac(zipFile, version) {
		let executable = Services.dirsvc.get('XREExeF', Ci.nsIFile);
		let appDir = executable.parent.parent; // .../Pharos.app
		let appsDir = appDir.parent;
		_appDir = appDir;

		// Extract the fresh bundle beside the current one.
		let staging = Zotero.getTempDirectory();
		staging.append('Pharos-update');
		staging.append(`extract-${version}`);
		await IOUtils.remove(staging.path, { ignoreAbsent: true, recursive: true });
		await IOUtils.makeDirectory(staging.path, { ignoreExisting: true });
		await Zotero.Utilities.Internal.exec('/usr/bin/ditto', ['-x', '-k', zipFile.path, staging.path]);
		let extracted = staging.clone();
		extracted.append('Pharos.app');
		if (!(await IOUtils.exists(extracted.path))) {
			throw new Error('installer archive is missing Pharos.app');
		}

		let backup = appsDir.clone();
		backup.append('Pharos.app.old');

		if (appsDir.isWritable() && appDir.isWritable()) {
			await IOUtils.remove(backup.path, { ignoreAbsent: true, recursive: true });
			await IOUtils.move(appDir.path, backup.path);
			await IOUtils.move(extracted.path, appDir.path);
			return;
		}

		// /Applications usually needs elevation; hand the swap to the system
		// so the user gets one native password prompt.
		let shell = [
			`rm -rf "${backup.path}"`,
			`mv "${appDir.path}" "${backup.path}"`,
			`mv "${extracted.path}" "${appDir.path}"`,
			`chown -R "$(stat -f '%Su' "${backup.path}")" "${appDir.path}"`,
		].join(' && ');
		let script = `do shell script "${shell.replace(/"/g, '\\"')}" with administrator privileges`;
		await Zotero.Utilities.Internal.exec('/usr/bin/osascript', ['-e', script]);
		_appDir = appDir;
	}
};
