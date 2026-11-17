/*
    ***** BEGIN LICENSE BLOCK *****
    
    Copyright © 2016 Center for History and New Media
                     George Mason University, Fairfax, Virginia, USA
                     http://zotero.org
    
    This file is part of Zotero.
    
    Zotero is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
    
    Zotero is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.
    
    You should have received a copy of the GNU Affero General Public License
    along with Zotero.  If not, see <http://www.gnu.org/licenses/>.
    
    ***** END LICENSE BLOCK *****
*/

"use strict";
var { OS } = ChromeUtils.importESModule("chrome://zotero/content/osfile.mjs");

Zotero.Profile = {
	dir: OS.Constants.Path.profileDir,
	
	getDefaultInProfilesDir: async function (profilesDir) {
		var profilesIni = OS.Path.join(profilesDir, "profiles.ini");
		
		try {
			var iniContents = await Zotero.File.getContentsAsync(profilesIni);
		}
		catch (e) {
			if (e.name == 'NotFoundError') {
				return false;
			}
			throw e;
		}
		
		// cheap and dirty ini parser
		var curSection = null;
		var defaultSection = null;
		var nSections = 0;
		for (let line of iniContents.split(/(?:\r?\n|\r)/)) {
			let tline = line.trim();
			if(tline[0] == "[" && tline[tline.length-1] == "]") {
				curSection = {};
				if(tline != "[General]") nSections++;
			} else if(curSection && tline != "") {
				let equalsIndex = tline.indexOf("=");
				let key = tline.substr(0, equalsIndex);
				let val = tline.substr(equalsIndex+1);
				curSection[key] = val;
				if(key == "Default" && val == "1") {
					defaultSection = curSection;
				}
			}
		}
		if (!defaultSection && curSection) defaultSection = curSection;
		
		if (!defaultSection || !defaultSection.Path) return false;
		
		var defaultProfile = defaultSection.IsRelative === "1"
			? OS.Path.join(profilesDir, ...defaultSection.Path.split("/"))
			: defaultSection.Path;
		
		try {
			// Note: exists() returns false on no access, so use stat() instead
			await OS.File.stat(defaultProfile);
		}
		catch (e) {
			if (e instanceof OS.File.Error) {
				if (e.becauseNoSuchFile) {
					return false;
				}
				throw e;
			}
		}
		return [defaultProfile, nSections > 1];
	},
	
	
	getProfilesDir: function () {
		return PathUtils.parent(this.dir);
	},


	/**
	 * Get the profile root used by an installed Zotero application.
	 *
	 * Pharos has its own Gecko profile root, so `getProfilesDir()` cannot find
	 * the profile that owns the shared library. Keep this lookup separate from
	 * `getOtherAppProfilesDir()`, which is the Firefox-for-Zotero migration path.
	 *
	 * Unit tests deliberately return no external profile root by default. A test
	 * that needs to exercise discovery stubs this method with a temporary root;
	 * this prevents the test runner from ever inspecting a user's real Zotero
	 * profile or library.
	 *
	 * @return {String|null} - The directory containing Zotero profiles.ini
	 */
	getOfficialZoteroProfilesDir: function () {
		if (CommandLineOptions.test) {
			return null;
		}

		if (Zotero.isWin) {
			let appData = Services.dirsvc.get("AppData", Components.interfaces.nsIFile).path;
			return this._getOfficialZoteroProfilesDir({ isWin: true, appDataDir: appData });
		}
		return this._getOfficialZoteroProfilesDir({
			isMac: Zotero.isMac,
			homeDir: OS.Constants.Path.homeDir
		});
	},


	/**
	 * Resolve the platform-specific Zotero profile root without touching the
	 * filesystem. Keeping this pure makes the OS layout auditable and lets unit
	 * tests use synthetic roots instead of a user's real profile.
	 *
	 * @param {Object} platform
	 * @param {Boolean} [platform.isWin]
	 * @param {Boolean} [platform.isMac]
	 * @param {String} [platform.appDataDir]
	 * @param {String} [platform.homeDir]
	 * @return {String}
	 */
	_getOfficialZoteroProfilesDir: function (platform) {
		if (platform.isWin) {
			return OS.Path.join(platform.appDataDir, "Zotero", "Zotero");
		}
		if (platform.isMac) {
			return OS.Path.join(platform.homeDir, "Library", "Application Support", "Zotero");
		}

		// On Linux, Zotero stores profile directories directly below
		// ~/.zotero/zotero rather than in a Profiles subdirectory.
		return OS.Path.join(platform.homeDir, ".zotero", "zotero");
	},


	/**
	 * Find the default profile of the installed Zotero application.
	 *
	 * @return {Promise<Array|false>} - [profileDir, hasMultipleProfiles] or false
	 */
	getOfficialZoteroProfile: async function () {
		let profilesDir = this.getOfficialZoteroProfilesDir();
		if (!profilesDir) {
			return false;
		}
		return this.getDefaultInProfilesDir(profilesDir);
	},
	
	
	/**
	 * Find other Zotero profile directories using the given data directory
	 *
	 * @param {String} dataDir
	 * @return {String[]}
	 */
	findOtherProfilesUsingDataDirectory: async function (dataDir) {
		let otherProfiles = await this._findOtherProfiles();
		// Backslashes are escaped in prefs.js, so escape them here for the substring match
		let escapedDataDir = dataDir.replace(/\\/g, '\\\\');

		for (let i = 0; i < otherProfiles.length; i++) {
			let dir = otherProfiles[i];
			let prefs = await Zotero.File.getContentsAsync(OS.Path.join(dir, "prefs.js"));
			prefs = prefs.trim().split(/(?:\r\n|\r|\n)/);

			let keep = prefs.some(line => line.includes("extensions.zotero.useDataDir") && line.includes("true"))
				&& prefs.some(line => line.match(/extensions\.zotero\.(lastD|d)ataDir/) && line.includes(escapedDataDir));
			if (!keep) {
				otherProfiles.splice(i, 1);
				i--;
			}
		}
		
		if (otherProfiles.length) {
			Zotero.debug("Found other profiles pointing to " + dataDir);
			Zotero.debug(otherProfiles);
		}
		else {
			Zotero.debug("No other profiles point to " + dataDir);
		}
		
		return otherProfiles;
	},
	
	
	updateProfileDataDirectory: async function (profileDir, oldDir, newDir) {
		let prefsFile = OS.Path.join(profileDir, "prefs.js");
		let prefsFileTmp = OS.Path.join(profileDir, "prefs.js.tmp");
		Zotero.debug("Updating " + prefsFile + " to point to new data directory");
		let contents = await Zotero.File.getContentsAsync(prefsFile);
		contents = contents
			.trim()
			.split(/(?:\r\n|\r|\n)/)
			// Remove existing lines
			.filter(line => !line.match(/extensions\.zotero\.(useD|lastD|d)ataDir/));
		// Shouldn't happen, but let's make sure we don't corrupt the prefs file
		let safeVal = newDir.replace(/["]/g, "");
		contents.push(
			`user_pref("extensions.zotero.dataDir", "${safeVal}");`,
			`user_pref("extensions.zotero.lastDataDir", "${safeVal}");`,
			'user_pref("extensions.zotero.useDataDir", true);'
		);
		let lineSep = Zotero.isWin ? "\r\n" : "\n";
		contents = contents.join(lineSep) + lineSep;
		await OS.File.writeAtomic(
			prefsFile,
			contents,
			{
				tmpPath: prefsFileTmp,
				encoding: 'utf-8'
			}
		);
	},
	
	
	//
	// Private methods
	//
	
	/**
	 * Get all profile directories within the given directory
	 *
	 * @return {String[]} - Array of paths
	 */
	_getProfilesInDir: async function (profilesDir) {
		var dirs = [];
		await Zotero.File.iterateDirectory(profilesDir, async function (entry) {
			// entry.isDir can be false for some reason on Travis, causing spurious test failures
			if (Zotero.automatedTest && !entry.isDir && (await OS.File.stat(entry.path)).isDir) {
				Zotero.debug("Overriding isDir for " + entry.path);
				entry.isDir = true;
			}
			if (entry.isDir && (await OS.File.exists(OS.Path.join(entry.path, "prefs.js")))) {
				dirs.push(entry.path);
			}
		});
		return dirs;
	},
	
	
	/**
	 * Find other Zotero profile directories
	 *
	 * @return {Promise<String[]>} - Array of paths
	 */
	_findOtherProfiles: async function () {
		var profileDir = this.dir;
		var profilesDir = this.getProfilesDir();
		return (await this._getProfilesInDir(profilesDir)).filter(dir => dir != profileDir);
	}
};
