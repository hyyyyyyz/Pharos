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

	describe("server address", function () {
		it("should save a self-hosted URL", async function () {
			var win = await openPane();
			try {
				win.document.getElementById('pharos-base-url').value = 'http://localhost:8848';
				win.Zotero_Preferences.Pharos.saveBaseURL();
				assert.equal(Zotero.Pharos.API.getBaseURL(), 'http://localhost:8848');
			}
			finally {
				win.close();
			}
		});

		it("should sign out when the server changes", async function () {
			// A token is only meaningful against the server that issued it.
			// Keeping it across a server change would send one deployment a
			// bearer token minted by another.
			await Zotero.Pharos.API.setToken('test-token');
			Zotero.Prefs.set('pharos.accountEmail', 'someone@example.org');

			var win = await openPane();
			try {
				win.document.getElementById('pharos-base-url').value = 'http://localhost:8848';
				await win.Zotero_Preferences.Pharos.saveBaseURL();
				assert.isFalse(Zotero.Pharos.API.hasCredentials());
			}
			finally {
				win.close();
			}
		});

		it("should restore the current address when cleared", async function () {
			var win = await openPane();
			try {
				var field = win.document.getElementById('pharos-base-url');
				field.value = '   ';
				win.Zotero_Preferences.Pharos.saveBaseURL();
				assert.equal(field.value, Zotero.Pharos.API.getBaseURL());
			}
			finally {
				win.close();
			}
		});
	});
});
