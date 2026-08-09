describe("Zotero.Pharos.API", function () {
	var origBaseURL;

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	beforeEach(async function () {
		await Zotero.Pharos.API.setToken(null);
	});

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
	});

	it("should be loaded onto the Zotero namespace", function () {
		// Guards the registration in zotero.mjs, not just the file's contents:
		// a module that exists but was never added to xpcomFilesLocal is exactly
		// the kind of seam that stays invisible until something calls it.
		assert.isObject(Zotero.Pharos);
		assert.isFunction(Zotero.Pharos.API.request);
	});

	describe("#getBaseURL()", function () {
		it("should default to the hosted backend", function () {
			Zotero.Prefs.clear('pharos.baseURL');
			assert.equal(Zotero.Pharos.API.getBaseURL(), 'https://pharos.selab.top');
		});

		it("should honour a self-hosted URL", function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:8848');
			assert.equal(Zotero.Pharos.API.getBaseURL(), 'http://localhost:8848');
		});

		it("should strip trailing slashes", function () {
			// Paths are concatenated onto this, so a trailing slash would produce
			// "//api/auth/login" -- which some servers route and others 404.
			Zotero.Prefs.set('pharos.baseURL', 'https://example.org///');
			assert.equal(Zotero.Pharos.API.getBaseURL(), 'https://example.org');
		});
	});

	describe("token storage", function () {
		it("should report no credentials when signed out", function () {
			assert.isFalse(Zotero.Pharos.API.hasCredentials());
		});

		it("should round-trip a token through the login manager", async function () {
			await Zotero.Pharos.API.setToken('test-token-abc123');
			assert.isTrue(Zotero.Pharos.API.hasCredentials());
			assert.equal(await Zotero.Pharos.API.getToken(), 'test-token-abc123');
		});

		it("should keep the token where Zotero keeps its own API key", async function () {
			// NOT "should not store it in the clear", which is what this asserted
			// while the client was built on a branch that had Zotero.OSKeyStore.
			// That API is absent from the release it is built on now, so the
			// second encryption layer is gone and the token is stored the way
			// Zotero stores its own Web API key -- see DECISIONS.md §7, where the
			// reduction is recorded rather than left to be discovered here.
			//
			// What is still guaranteed, and is what this pins: the secret is in
			// the login manager under Pharos's OWN host, never in a pref and never
			// in Zotero's login bucket where clearing one would clear the other.
			await Zotero.Pharos.API.setToken('test-token-abc123');
			var login = Zotero.Pharos.API._getLoginInfo();
			assert.ok(login, 'the token is not in the login manager at all');
			assert.equal(login.hostname, 'chrome://pharos');
			assert.notEqual(login.hostname, 'chrome://zotero');
			assert.notInclude(
				JSON.stringify(Zotero.Prefs.rootBranch.getChildList('extensions.zotero.pharos')),
				'test-token-abc123',
				'a bearer token must never reach the preferences file'
			);
		});

		it("should replace an existing token rather than accumulate logins", async function () {
			await Zotero.Pharos.API.setToken('first');
			await Zotero.Pharos.API.setToken('second');
			assert.equal(await Zotero.Pharos.API.getToken(), 'second');
			var logins = Services.logins.findLogins('chrome://pharos', null, 'Pharos API');
			assert.lengthOf(logins, 1);
		});

		it("should clear the token and the stored login", async function () {
			await Zotero.Pharos.API.setToken('test-token-abc123');
			await Zotero.Pharos.API.setToken(null);
			assert.isFalse(Zotero.Pharos.API.hasCredentials());
			assert.isNull(await Zotero.Pharos.API.getToken());
		});

		it("should not leak a cleared token from the cache", async function () {
			await Zotero.Pharos.API.setToken('test-token-abc123');
			await Zotero.Pharos.API.getToken(); // populate the cache
			await Zotero.Pharos.API.setToken(null);
			assert.isNull(await Zotero.Pharos.API.getToken());
		});

		it("should invalidate account-scoped work when the token changes", async function () {
			var original = Zotero.Pharos.Chat._clearCache;
			var calls = 0;
			var epochs = [];
			var observer = { observe: (_subject, _topic, data) => epochs.push(Number(data)) };
			Zotero.Pharos.Chat._clearCache = () => calls++;
			Services.obs.addObserver(observer, Zotero.Pharos.API.ACCOUNT_CHANGED_TOPIC);
			var before = Zotero.Pharos.API.getTokenEpoch();
			try {
				await Zotero.Pharos.API.setToken('another-account-token');
				await Zotero.Pharos.API.setToken(null);
			}
			finally {
				Services.obs.removeObserver(observer, Zotero.Pharos.API.ACCOUNT_CHANGED_TOPIC);
				Zotero.Pharos.Chat._clearCache = original;
			}
			assert.equal(calls, 2);
			assert.deepEqual(epochs, [before + 1, before + 2]);
			assert.equal(Zotero.Pharos.API.getTokenEpoch(), before + 2);
		});
	});

	describe("#request()", function () {
		it("should refuse to make an authenticated request when signed out", async function () {
			// Rather than sending an unauthenticated request and letting the
			// server 401 -- the point is that no request leaves the machine.
			var e = await getPromiseError(Zotero.Pharos.API.request('GET', '/api/auth/me'));
			assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
		});

		it("should not require a token for anonymous requests", async function () {
			// Points at a port nothing is listening on, so the call fails at the
			// transport rather than at the credential check. Any error other than
			// SignedOutError proves the token gate was passed.
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			var e = await getPromiseError(
				Zotero.Pharos.API.request('POST', '/api/auth/login', {
					anon: true,
					body: { email: 'a@b.c', password: 'x' },
					timeout: 2000,
				})
			);
			assert.ok(e);
			assert.notInstanceOf(e, Zotero.Pharos.API.SignedOutError);
		});
	});

	describe("#logout()", function () {
		it("should forget the token locally", async function () {
			await Zotero.Pharos.API.setToken('test-token-abc123');
			await Zotero.Pharos.API.logout();
			assert.isFalse(Zotero.Pharos.API.hasCredentials());
		});
	});
});
