describe("Zotero.Pharos.API", function () {
	var origBaseURL;
	var origIsSourceBuild;

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
		origIsSourceBuild = Zotero.isSourceBuild;
	});

	beforeEach(async function () {
		Zotero.isSourceBuild = origIsSourceBuild;
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		await Zotero.Pharos.API.setToken(null);
	});

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		Zotero.isSourceBuild = origIsSourceBuild;
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

		it("should honour the hidden developer override in source builds", function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:8848');
			assert.equal(Zotero.Pharos.API.getBaseURL(), 'http://localhost:8848');
		});

		it("should pin official builds to the hosted product", function () {
			Zotero.Prefs.set('pharos.baseURL', 'https://untrusted.example');
			Zotero.isSourceBuild = false;
			assert.equal(Zotero.Pharos.API.getBaseURL(), 'https://pharos.selab.top');
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
			assert.equal(
				Zotero.Prefs.get('pharos.credentialBaseURL'),
				Zotero.Pharos.API.getBaseURL()
			);
		});

		it("should discard a token when a source build changes origin", async function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:8848');
			await Zotero.Pharos.API.setToken('dev-origin-token');
			Zotero.Prefs.set('pharos.accountEmail', 'developer@example.org');
			Zotero.Prefs.set('pharos.isAdmin', true);

			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:8849');

			assert.isFalse(Zotero.Pharos.API.hasCredentials());
			assert.isFalse(!!Zotero.Pharos.API._getLoginInfo());
			assert.equal(Zotero.Prefs.get('pharos.accountEmail'), '');
			assert.isFalse(!!Zotero.Prefs.get('pharos.isAdmin'));
		});

		it("should never replay a legacy custom-origin token to the official service",
			async function () {
				Zotero.Prefs.set('pharos.baseURL', 'https://legacy.example');
				await Zotero.Pharos.API.setToken('legacy-custom-token');
				// 1.3.0 had no trustworthy origin marker. The old baseURL is only
				// enough to clean up the profile, never enough to reuse this token.
				Zotero.Prefs.clear('pharos.credentialBaseURL');
				Zotero.Prefs.set('pharos.accountEmail', 'legacy@example.org');
				Zotero.isSourceBuild = false;

				assert.isFalse(Zotero.Pharos.API.hasCredentials());
				assert.isNull(await Zotero.Pharos.API.getToken());
				assert.isFalse(!!Zotero.Pharos.API._getLoginInfo());
				assert.equal(Zotero.Prefs.get('pharos.accountEmail'), '');
				assert.equal(
					Zotero.Prefs.get('pharos.baseURL'),
					'https://pharos.selab.top'
				);
			}
		);

		it("should discard an unbound legacy token even when its old pref is official",
			async function () {
			Zotero.Prefs.set('pharos.baseURL', 'https://pharos.selab.top/');
			await Zotero.Pharos.API.setToken('legacy-official-token');
			Zotero.Prefs.clear('pharos.credentialBaseURL');
			Zotero.isSourceBuild = false;

			assert.isFalse(Zotero.Pharos.API.hasCredentials());
			assert.isNull(await Zotero.Pharos.API.getToken());
			assert.isFalse(!!Zotero.Pharos.API._getLoginInfo());
			}
		);

		it("should fail closed when the credential store refuses deletion", async function () {
			Zotero.Prefs.set('pharos.baseURL', 'https://legacy.example');
			await Zotero.Pharos.API.setToken('undeletable-token');
			Zotero.Prefs.clear('pharos.credentialBaseURL');
			Zotero.isSourceBuild = false;

			var removeLogin = sinon.stub(Zotero.Pharos.API, '_removeLogin')
				.throws(new Error('credential store locked'));
			var request = sinon.stub(Zotero.HTTP, 'request');
			try {
				assert.isFalse(Zotero.Pharos.API.hasCredentials());
				var e = await getPromiseError(
					Zotero.Pharos.API.request('GET', '/api/auth/me')
				);
				assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
				assert.isFalse(request.called);
				assert.equal(
					Zotero.Prefs.get('pharos.credentialBaseURL'),
					'invalid://credential-removal-failed'
				);
				assert.equal(
					Zotero.Prefs.get('pharos.baseURL'),
					'https://legacy.example'
				);
			}
			finally {
				request.restore();
				removeLogin.restore();
				await Zotero.Pharos.API.setToken(null);
			}
		});

		it("should not let an old async decrypt overwrite a new account", async function () {
			Zotero.Prefs.set('pharos.baseURL', 'https://first.example');
			var nsLoginInfo = new Components.Constructor(
				'@mozilla.org/login-manager/loginInfo;1',
				Components.interfaces.nsILoginInfo,
				'init'
			);
			var login = new nsLoginInfo(
				'chrome://pharos', null, 'Pharos API', '', 'old-token', '', ''
			);
			await Services.logins.addLoginAsync(login);
			Zotero.Prefs.set('pharos.credentialBaseURL', 'https://first.example');

			var deferred = Zotero.Promise.defer();
			var hadOSKeyStore = Object.prototype.hasOwnProperty.call(Zotero, 'OSKeyStore');
			var originalOSKeyStore = Zotero.OSKeyStore;
			Zotero.OSKeyStore = {
				encrypt: secret => Promise.resolve(secret),
				decrypt: () => deferred.promise,
			};
			try {
				var oldRead = Zotero.Pharos.API.getToken('https://first.example');
				Zotero.Prefs.set('pharos.baseURL', 'https://second.example');
				await Zotero.Pharos.API.setToken('new-token', 'https://second.example');
				deferred.resolve('old-token');

				assert.isNull(await oldRead);
				assert.equal(
					await Zotero.Pharos.API.getToken('https://second.example'),
					'new-token'
				);
				assert.equal(
					Zotero.Prefs.get('pharos.credentialBaseURL'),
					'https://second.example'
				);
			}
			finally {
				if (hadOSKeyStore) {
					Zotero.OSKeyStore = originalOSKeyStore;
				}
				else {
					delete Zotero.OSKeyStore;
				}
			}
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

		it("should reject an unbound legacy token without sending an HTTP request",
			async function () {
				await Zotero.Pharos.API.setToken('legacy-token');
				Zotero.Prefs.clear('pharos.credentialBaseURL');
				Zotero.isSourceBuild = false;
				var request = sinon.stub(Zotero.HTTP, 'request');
				try {
					var e = await getPromiseError(
						Zotero.Pharos.API.request('GET', '/api/auth/me')
					);
					assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
					assert.isFalse(request.called);
				}
				finally {
					request.restore();
				}
			}
		);

		it("should bind a login token to the origin that answered", async function () {
			Zotero.Prefs.set('pharos.baseURL', 'https://first.example');
			var originalRequest = Zotero.Pharos.API.request;
			Zotero.Pharos.API.request = async function (_method, _path, options) {
				options.captureOrigin('https://first.example');
				Zotero.Prefs.set('pharos.baseURL', 'https://second.example');
				return { token: 'first-origin-token', user: { email: 'reader@example.org' } };
			};
			try {
				var e = await getPromiseError(
					Zotero.Pharos.API.login('reader@example.org', 'password')
				);
				assert.match(e.message, /service changed/i);
				assert.isFalse(Zotero.Pharos.API.hasCredentials());
				assert.isFalse(!!Zotero.Pharos.API._getLoginInfo());
			}
			finally {
				Zotero.Pharos.API.request = originalRequest;
			}
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
