describe("Pharos Preferences", function () {
	var origBaseURL;

	async function openPane() {
		var win = await loadWindow("chrome://zotero/content/preferences/preferences.xhtml", {
			pane: 'zotero-prefpane-pharos'
		});
		await win.Zotero_Preferences.waitForFirstPaneLoad();
		return win;
	}

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	beforeEach(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.accountEmail', '');
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
	});

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.accountEmail', '');
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
	});

	it("should open the pane", async function () {
		// Covers the registration in preferencePanes.js and, just as importantly,
		// that every data-l10n-id in the pane resolves: an id missing from the
		// window's own localization resources stops the whole window opening.
		var win = await openPane();
		try {
			assert.ok(win.document.getElementById('zotero-prefpane-pharos'));
		}
		finally {
			win.close();
		}
	});

	it("should show the sign-in form when signed out", async function () {
		var win = await openPane();
		try {
			assert.isFalse(win.document.getElementById('pharos-signed-out').hidden);
			assert.isTrue(win.document.getElementById('pharos-signed-in').hidden);
		}
		finally {
			win.close();
		}
	});

	it("should show the account when a token is stored", async function () {
		// Points at a port nothing is listening on so that init()'s verify()
		// fails at the transport. An unreachable server must NOT read as signed
		// out -- the user still holds a token, and losing wifi should not log
		// anyone out.
		Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
		await Zotero.Pharos.API.setToken('test-token');
		Zotero.Prefs.set('pharos.accountEmail', 'someone@example.org');

		var win = await openPane();
		try {
			assert.isFalse(win.document.getElementById('pharos-signed-in').hidden);
			assert.equal(
				win.document.getElementById('pharos-account-email').textContent,
				'someone@example.org'
			);
		}
		finally {
			win.close();
		}
	});

	it("should refuse to sign in with an empty form", async function () {
		var win = await openPane();
		try {
			await win.Zotero_Preferences.Pharos.signIn();
			assert.isNotEmpty(win.document.getElementById('pharos-message').textContent);
			// Nothing should have been stored from an empty submission.
			assert.isFalse(Zotero.Pharos.API.hasCredentials());
		}
		finally {
			win.close();
		}
	});

	it("should not expose a server address control", async function () {
		var win = await openPane();
		try {
			assert.isNull(win.document.getElementById('pharos-base-url'));
		}
		finally {
			win.close();
		}
	});

	it("should show the build version", async function () {
		var win = await openPane();
		try {
			var about = win.document.getElementById('pharos-about-version');
			assert.ok(about, "the about block exists");
			assert.equal(about.textContent, Zotero.Pharos.Updates.currentVersion());
			assert.ok(
				win.document.getElementById('pharos-check-updates'),
				"the manual check button exists"
			);
		}
		finally {
			win.close();
		}
	});

	it("should report a newer version from the manual check", async function () {
		var origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function () {
			return Promise.resolve({
				version: '99.0.0',
				url: 'https://example.test/releases',
				notes: null,
			});
		};
		var win = await openPane();
		try {
			await win.Zotero_Preferences.Pharos.checkForUpdates();
			var row = win.document.getElementById('pharos-update-available');
			assert.isFalse(row.hidden, "a newer build must draw the download row");
			assert.equal(
				win.document.getElementById('pharos-update-download').textContent,
				Zotero.ftl.formatValueSync('pharos-prefs-update-download', { version: '99.0.0' })
			);
		}
		finally {
			Zotero.Pharos.API.request = origRequest;
			win.close();
		}
	});

	it("should say it is up to date when nothing newer exists", async function () {
		var origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function () {
			return Promise.resolve({ version: '0.0.1', url: null, notes: null });
		};
		var win = await openPane();
		try {
			await win.Zotero_Preferences.Pharos.checkForUpdates();
			assert.isTrue(
				win.document.getElementById('pharos-update-available').hidden,
				"no download row for an older server version"
			);
			assert.equal(
				win.document.getElementById('pharos-update-message').textContent,
				Zotero.getString('pharos-prefs-update-latest')
			);
		}
		finally {
			Zotero.Pharos.API.request = origRequest;
			win.close();
		}
	});
});
