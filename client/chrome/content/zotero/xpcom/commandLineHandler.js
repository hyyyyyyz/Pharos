/*
    ***** BEGIN LICENSE BLOCK *****
    
    Copyright © 2009 Center for History and New Media
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
    
	
	Based on nsChromeExtensionHandler example code by Ed Anuff at
	http://kb.mozillazine.org/Dev_:_Extending_the_Chrome_Protocol
	
    ***** END LICENSE BLOCK *****
*/

Zotero.CommandLineIngester = {
	ingest: async function () {
		const { CommandLineOptions } = ChromeUtils.importESModule("chrome://zotero/content/modules/commandLineOptions.mjs");

		var mainWindow = Zotero.getMainWindow();
		var fileToOpen;
		// Handle zotero:// and file URIs
		var uri = CommandLineOptions.url;
		if (uri) {
			if (uri.schemeIs("zotero")) {
				// Check for existing window and focus it
				if (mainWindow) {
					mainWindow.focus();
					mainWindow.ZoteroPane.loadURI(uri.spec);
				}
			}
			// See below
			else if (uri.schemeIs("file")) {
				fileToOpen = OS.Path.fromFileURI(uri.spec);
			}
			else {
				Zotero.debug(`Not handling URL: ${uri.spec}\n\n`);
			}
		}


		fileToOpen = fileToOpen || CommandLineOptions.file;
		if (fileToOpen) {
			// PHAROS: on a first run the sign-in window is the only window, so
			// there is nothing to import into yet -- and `mainWindow` below would
			// be null. Return with the options still set rather than clearing
			// them: pharosAuth.js re-ingests once it has opened the library, and
			// dropping the path here would silently lose an Open With.
			//
			// Before the gate existed this could not happen, because the library
			// was always the first window.
			if (!mainWindow) {
				return;
			}

			var file = Zotero.File.pathToFile(fileToOpen);

			if (file.leafName.substr(-4).toLowerCase() === ".csl"
				|| file.leafName.substr(-8).toLowerCase() === ".csl.txt") {
				// Install CSL file
				Zotero.Styles.install({ file: file.path }, file.path);
			}
			else {
				// Ask before importing
				var checkState = {
					value: Zotero.Prefs.get('import.createNewCollection.fromFileOpenHandler')
				};
				if (Services.prompt.confirmCheck(null, Zotero.getString('ingester.importFile.title'),
					Zotero.getString('ingester.importFile.text', [file.leafName]),
					Zotero.getString('ingester.importFile.intoNewCollection'),
					checkState)) {
					Zotero.Prefs.set(
						'import.createNewCollection.fromFileOpenHandler', checkState.value
					);

					mainWindow.Zotero_File_Interface.importFile({
						file,
						createNewCollection: checkState.value
					});
				}
			}
		}

		CommandLineOptions.url = false;
		CommandLineOptions.file = false;
	},
};

/**
 * The object representing the Zotero command line handler.
 * It is only active after Zotero is initialized and there is initial handling
 * in app/assets/commandLineHandler.js
 */
var ZoteroCommandLineHandler = {
	/* nsICommandLineHandler */
	handle: async function (cmdLine) {
		const { Zotero } = ChromeUtils.importESModule("chrome://zotero/content/zotero.mjs");
		// handler for Zotero integration commands
		// this is typically used on Windows only, via WM_COPYDATA rather than the command line
		var agent = cmdLine.handleFlagWithParam("ZoteroIntegrationAgent", false);
		if (agent) {
			var command = cmdLine.handleFlagWithParam("ZoteroIntegrationCommand", false);
			var docId = cmdLine.handleFlagWithParam("ZoteroIntegrationDocument", false);
			var templateVersion = parseInt(cmdLine.handleFlagWithParam("ZoteroIntegrationTemplateVersion", false));
			templateVersion = isNaN(templateVersion) ? 0 : templateVersion;
			
			Zotero.Integration.execCommand(agent, command, docId, templateVersion);
		}
		// Only open main window if we aren't handling an integration command
		else if (!Zotero.getMainWindow()) {
			// Sign-in comes FIRST, as its own window, and opens the main window
			// itself once it is done. It used to be a modal thrown over an
			// already-visible library, which is the wrong shape twice over: the
			// library is what the user is being asked to unlock, so showing it
			// behind the question answers it; and a modal cannot be moved out of
			// the way, so the one thing on screen that might explain what Pharos
			// is was covered by the box asking to sign in to it.
			//
			// The main window is opened by whichever path finishes -- see
			// pharosAuth.js `_finish()`. Closing the sign-in window without
			// answering opens nothing, which is what a sign-in window does.
			//
			// This branch does NOT cover the first run. This handler registers
			// itself under `m-zotero` at the bottom of this file, so it exists
			// only once Zotero.init() has loaded it -- and Zotero is first loaded
			// BY the library window. At initial launch the library is already up,
			// so `!Zotero.getMainWindow()` is false and shouldGate() would refuse
			// anyway. 1.0.0 shipped with the gate here and nothing could open it.
			// The first-run trigger lives in app/assets/commandLineHandler.js,
			// which runs before any of this exists.
			//
			// It stays because it IS load-bearing for the case it can reach:
			// app/scripts/fetch_xulrunner patches dch_handle to return
			// immediately on STATE_REMOTE_AUTO, so for a dock-icon click on an
			// already-running application with no window, m-zotero is the only
			// handler that acts.
			if (Zotero.Pharos?.Auth?.shouldGate()) {
				Zotero.Pharos.Auth.openGate();
			}
			else {
				Zotero.openMainWindow();
			}
		}
		
		await Zotero.CommandLineIngester.ingest();
	},
	
	classID: Components.ID("{531828f8-a16c-46be-b9aa-14845c3b010f}"),
	contractID: "@zotero.org/command-line-handler;1",
	QueryInterface: ChromeUtils.generateQI(["nsISupports", "nsICommandLineHandler"]),
	createInstance(iid) {
		return this.QueryInterface(iid);
	},
};

const Cm = Components.manager.QueryInterface(Ci.nsIComponentRegistrar);
// Don't register if already registered (e.g., after a reinit() in tests)
if (!Cm.isCIDRegistered(ZoteroCommandLineHandler.classID)) {
	Cm.registerFactory(
		ZoteroCommandLineHandler.classID,
		"command-line-handler",
		ZoteroCommandLineHandler.contractID,
		ZoteroCommandLineHandler
	);
	const catman = Cc["@mozilla.org/categorymanager;1"].getService(Ci.nsICategoryManager);
	
	catman.addCategoryEntry("command-line-handler",
		"m-zotero",
		ZoteroCommandLineHandler.contractID, false, true);
}
