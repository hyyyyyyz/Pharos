describe("Zotero.Pharos.Admin", function () {
	var origBaseURL;
	var origIsAdmin;
	var origRequest = null;
	var requests;

	/**
	 * Swap out the API layer and record what the module would have sent.
	 *
	 * The point of most of these assertions is the shape of the request -- a
	 * camelCase key reaching a backend declared `extra="forbid"` is a 422, and a
	 * confirm_email that never made it into the query string is a deletion that
	 * silently fails. Neither is visible from the return value.
	 */
	function captureRequests(response) {
		requests = [];
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			return Promise.resolve(response === undefined ? null : response);
		};
	}

	function restoreRequests() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	}

	/** Passes in any locale: en-US throws on a missing id, others return it. */
	function assertLocalized(id) {
		var value = Zotero.getString(id);
		assert.isNotEmpty(value, `missing string ${id}`);
		assert.notEqual(value, id, `missing string ${id}`);
	}

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
		origIsAdmin = Zotero.Prefs.get('pharos.isAdmin');
	});

	afterEach(function () {
		restoreRequests();
	});

	after(async function () {
		restoreRequests();
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		Zotero.Prefs.set('pharos.isAdmin', !!origIsAdmin);
		await Zotero.Pharos.API.setToken(null);
	});

	it("should be loaded onto the Zotero namespace", function () {
		// Guards the registration in zotero.mjs as much as the file: a module
		// that exists but was never added to xpcomFilesLocal stays invisible
		// until something calls it.
		assert.isFunction(Zotero.Pharos.Admin.listUsers);
		assert.isFunction(Zotero.Pharos.Admin.deleteUser);
	});

	describe("#isAdmin()", function () {
		it("should be false when signed out, whatever was cached", async function () {
			// The cached flag outlives a sign-out, so consulting it alone would
			// keep offering the console to nobody.
			await Zotero.Pharos.API.setToken(null);
			Zotero.Prefs.set('pharos.isAdmin', true);
			assert.isFalse(Zotero.Pharos.Admin.isAdmin());
		});

		it("should be false for a signed-in ordinary account", async function () {
			await Zotero.Pharos.API.setToken('test-token-admin');
			Zotero.Prefs.set('pharos.isAdmin', false);
			assert.isFalse(Zotero.Pharos.Admin.isAdmin());
		});

		it("should be true only for a signed-in administrator", async function () {
			await Zotero.Pharos.API.setToken('test-token-admin');
			Zotero.Prefs.set('pharos.isAdmin', true);
			assert.isTrue(Zotero.Pharos.Admin.isAdmin());
		});
	});

	describe("#refresh()", function () {
		it("should clear the cached flag when signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			Zotero.Prefs.set('pharos.isAdmin', true);
			assert.isFalse(await Zotero.Pharos.Admin.refresh());
			assert.isFalse(!!Zotero.Prefs.get('pharos.isAdmin'));
		});

		it("should keep the cached answer when the server is unreachable", async function () {
			// An offline laptop is not evidence that anyone was demoted. Losing
			// the console on the train and not getting it back until the next
			// successful call would be the wrong failure.
			await Zotero.Pharos.API.setToken('test-token-admin');
			Zotero.Prefs.set('pharos.isAdmin', true);
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			assert.isTrue(await Zotero.Pharos.Admin.refresh());
			assert.isTrue(!!Zotero.Prefs.get('pharos.isAdmin'));
		});
	});

	describe("#confirmationMatches()", function () {
		it("should accept the address as stored", function () {
			assert.isTrue(Zotero.Pharos.Admin.confirmationMatches(
				'ada@example.org', 'ada@example.org'
			));
		});

		it("should ignore case and surrounding space", function () {
			// The backend strips and casefolds what it receives, and stores
			// addresses casefolded, so accepting these cannot enable the button
			// for something the server will then reject.
			assert.isTrue(Zotero.Pharos.Admin.confirmationMatches(
				'  Ada@Example.ORG  ', 'ada@example.org'
			));
		});

		it("should refuse a different account", function () {
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches(
				'alan@example.org', 'ada@example.org'
			));
		});

		it("should refuse a prefix of the address", function () {
			// Deletion is irreversible; "close enough" is not a confirmation.
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches(
				'ada@example.or', 'ada@example.org'
			));
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches(
				'ada', 'ada@example.org'
			));
		});

		it("should refuse nothing at all", function () {
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches('', 'ada@example.org'));
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches(undefined, 'ada@example.org'));
			assert.isFalse(Zotero.Pharos.Admin.confirmationMatches('', ''));
		});
	});

	describe("#listUsers()", function () {
		it("should send the search term and a bounded page", async function () {
			captureRequests({ users: [], total: 0, limit: 200, offset: 0 });
			await Zotero.Pharos.Admin.listUsers({ query: '  ada  ' });
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'GET');
			assert.include(requests[0].path, 'q=ada');
			assert.include(requests[0].path, 'limit=200');
		});

		it("should clamp a larger page to what the backend allows", async function () {
			// The backend caps at 200 and 422s above it, which would turn a
			// bigger number into an empty console rather than a bigger list.
			captureRequests({ users: [], total: 0, limit: 200, offset: 0 });
			await Zotero.Pharos.Admin.listUsers({ limit: 5000 });
			assert.include(requests[0].path, 'limit=200');
		});

		it("should escape a search term with URL syntax in it", async function () {
			captureRequests({ users: [], total: 0, limit: 200, offset: 0 });
			await Zotero.Pharos.Admin.listUsers({ query: 'a&b=c' });
			assert.include(requests[0].path, 'q=a%26b%3Dc');
		});

		it("should omit the search term when there is none", async function () {
			captureRequests({ users: [], total: 0, limit: 200, offset: 0 });
			await Zotero.Pharos.Admin.listUsers({ query: '   ' });
			assert.notInclude(requests[0].path, 'q=');
		});
	});

	describe("#updateUser()", function () {
		it("should send the backend's snake_case fields", async function () {
			captureRequests({});
			await Zotero.Pharos.Admin.updateUser('u1', { isAdmin: true });
			assert.equal(requests[0].method, 'PATCH');
			assert.equal(requests[0].path, '/api/admin/users/u1');
			assert.deepEqual(requests[0].options.body, { is_admin: true });
		});

		it("should send only the fields it was given", async function () {
			// UserPatch is declared extra="forbid" and acts on exclude_unset, so
			// sending is_active alongside is_admin would change two things when
			// the operator asked for one.
			captureRequests({});
			await Zotero.Pharos.Admin.updateUser('u1', { isActive: false });
			assert.deepEqual(requests[0].options.body, { is_active: false });
		});

		it("should refuse an empty patch without calling the server", function () {
			captureRequests({});
			assert.throws(() => Zotero.Pharos.Admin.updateUser('u1', {}));
			assert.lengthOf(requests, 0);
		});

		it("should escape a user id", async function () {
			captureRequests({});
			await Zotero.Pharos.Admin.updateUser('a/b', { isAdmin: false });
			assert.equal(requests[0].path, '/api/admin/users/a%2Fb');
		});
	});

	describe("#deleteUser()", function () {
		it("should send the typed address as confirm_email", async function () {
			// The backend refuses the deletion without it. Dropping it here would
			// turn every deletion into a 400 -- or, worse, would have deleted the
			// wrong account had the backend not checked.
			captureRequests();
			await Zotero.Pharos.Admin.deleteUser('u1', 'ada@example.org');
			assert.equal(requests[0].method, 'DELETE');
			assert.include(requests[0].path, '/api/admin/users/u1?');
			assert.include(requests[0].path, 'confirm_email=ada%40example.org');
		});

		it("should escape an address with a plus in it", async function () {
			// "+" is a legal address character and a space in a query string.
			captureRequests();
			await Zotero.Pharos.Admin.deleteUser('u1', 'ada+pharos@example.org');
			assert.include(requests[0].path, 'confirm_email=ada%2Bpharos%40example.org');
		});
	});

	describe("#probeProvider()", function () {
		it("should escape the provider name", async function () {
			captureRequests({ name: 'x', ok: true, latency_ms: 1, detail: null });
			await Zotero.Pharos.Admin.probeProvider('a b');
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/admin/providers/a%20b/probe');
		});

		it("should outlast the backend's own probe timeout", async function () {
			// The backend gives a vendor ten seconds. A client timeout at or
			// below that reports a network failure for what is really a slow but
			// successful probe.
			captureRequests({ name: 'deepseek', ok: true, latency_ms: 1, detail: null });
			await Zotero.Pharos.Admin.probeProvider('deepseek');
			assert.isAbove(requests[0].options.timeout, 10000);
		});
	});

	describe("#isTranslationDegraded()", function () {
		it("should not warn when the configured engine is what runs", function () {
			assert.isFalse(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'deepseek',
				effective_translator: 'deepseek',
			}));
		});

		it("should not warn when the free engine was chosen deliberately", function () {
			assert.isFalse(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'bing',
				effective_translator: 'bing',
			}));
		});

		it("should not warn for a relay, whose effective name is the wire format", function () {
			// Settings.translator_config() reports "openai_compatible" for both
			// the openai and custom providers, so a plain inequality test warns
			// of a degradation that has not happened. This is the case the web
			// client gets wrong.
			assert.isFalse(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'openai',
				effective_translator: 'openai_compatible',
			}));
			assert.isFalse(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'custom',
				effective_translator: 'openai_compatible',
			}));
		});

		it("should warn when an LLM translator fell back to the free engine", function () {
			// The only way translator_config() yields "bing" for a non-Bing
			// selection is the missing-credential fallback.
			assert.isTrue(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'deepseek',
				effective_translator: 'bing',
			}));
			assert.isTrue(Zotero.Pharos.Admin.isTranslationDegraded({
				translator: 'custom',
				effective_translator: 'bing',
			}));
		});

		it("should say nothing without an answer from the server", function () {
			assert.isFalse(Zotero.Pharos.Admin.isTranslationDegraded(null));
		});
	});

	describe("strings", function () {
		it("should have a label for every column and control", function () {
			for (let id of [
				'pharos-admin-tab-users',
				'pharos-admin-tab-providers',
				'pharos-admin-forbidden',
				'pharos-admin-column-user',
				'pharos-admin-column-papers',
				'pharos-admin-column-projects',
				'pharos-admin-column-highlights',
				'pharos-admin-column-created',
				'pharos-admin-column-last-login',
				'pharos-admin-column-role',
				'pharos-admin-column-actions',
				'pharos-admin-role-admin',
				'pharos-admin-role-user',
				'pharos-admin-promote',
				'pharos-admin-demote',
				'pharos-admin-activate',
				'pharos-admin-deactivate',
				'pharos-admin-delete',
				'pharos-admin-delete-title',
				'pharos-admin-delete-prompt',
				'pharos-admin-delete-confirm',
				'pharos-admin-delete-irreversible',
				'pharos-admin-cancel',
				'pharos-admin-probe',
				'pharos-admin-provider-model',
				'pharos-admin-provider-url',
				'pharos-admin-provider-key',
				'pharos-admin-providers-note',
			]) {
				assertLocalized(id);
			}
		});

		it("should substitute the arguments the console passes", function () {
			// Fluent leaves an unknown argument as a literal "{ $name }", so a
			// renamed placeholder shows the operator the source of the string
			// instead of the account they are about to erase. Zotero.getString()
			// cannot catch this: handed params it reads the .properties bundle,
			// where none of these ids exist.
			assert.include(
				Zotero.ftl.formatValueSync('pharos-admin-delete-body', { email: 'ada@example.org' }),
				'ada@example.org'
			);
			var owns = Zotero.ftl.formatValueSync('pharos-admin-delete-owns', {
				papers: 3,
				projects: 2,
				highlights: 1,
			});
			assert.notInclude(owns, '$papers');
			assert.notInclude(owns, '$projects');
			assert.notInclude(owns, '$highlights');
			assert.include(
				Zotero.ftl.formatValueSync('pharos-admin-delete-tooltip', { email: 'ada@example.org' }),
				'ada@example.org'
			);
			assert.include(
				Zotero.ftl.formatValueSync('pharos-admin-provider-key-set', { hint: 'abcd' }),
				'abcd'
			);
			assert.include(
				Zotero.ftl.formatValueSync('pharos-admin-probe-ok', { ms: 42 }),
				'42'
			);
			assert.include(
				Zotero.ftl.formatValueSync('pharos-admin-providers-degraded', {
					configured: 'deepseek',
					effective: 'bing',
				}),
				'deepseek'
			);
		});
	});

	describe("the console window", function () {
		var ME = { id: 'u1', email: 'me@example.org', display_name: 'Ada', is_admin: true };
		var OTHER = {
			id: 'u2',
			email: 'alan@example.org',
			display_name: null,
			is_admin: false,
			is_active: true,
			created_at: '2026-01-02T03:04:05Z',
			last_login_at: null,
			papers: 3,
			projects: 2,
			searches: 1,
			highlights: 4,
		};
		var STATS = {
			users: 2,
			admins: 1,
			inactive_users: 0,
			papers: 3,
			translated_papers: 1,
			projects: 2,
			searches: 1,
			daily_papers: 0,
			allow_registration: true,
		};
		var saved;

		/**
		 * Replace the service layer for the window's lifetime.
		 *
		 * The window shares the one Zotero namespace with the test, so swapping
		 * the methods here is what the view will call. Stubs rather than a fake
		 * server because what these tests are about is the view's own behaviour
		 * -- who gets which buttons, and what the confirmation demands.
		 */
		function stubAdmin(overrides) {
			saved = {};
			for (let [name, fn] of Object.entries(overrides)) {
				saved[name] = Zotero.Pharos.Admin[name];
				Zotero.Pharos.Admin[name] = fn;
			}
		}

		function restoreAdmin() {
			for (let [name, fn] of Object.entries(saved || {})) {
				Zotero.Pharos.Admin[name] = fn;
			}
			saved = null;
		}

		afterEach(async function () {
			restoreAdmin();
			await Zotero.Pharos.API.setToken(null);
		});

		it("should open and report being signed out", async function () {
			// Opening it at all covers the localization ids in pharosAdmin.xhtml:
			// one missing from the window's own resource list stops it appearing,
			// and the failure surfaces as a bare undefined with no stack.
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosAdmin.xhtml");
			try {
				// loadWindow resolves on the load event, but init() keeps going
				// after it. See pharosDaily.js on why this promise exists.
				await win.Zotero_Pharos_Admin.initialized;
				assert.ok(win.document.getElementById('pharos-admin-root'));
				assert.isNotEmpty(
					win.document.getElementById('pharos-admin-status').textContent
				);
				// Nothing here works signed out, so nothing here is offered.
				assert.isTrue(win.document.getElementById('pharos-admin-tab-users').disabled);
				assert.isTrue(win.document.getElementById('pharos-admin-search').disabled);
				// And no list was rendered against a server it never called.
				assert.lengthOf(win.document.getElementById('pharos-admin-body').children, 0);
			}
			finally {
				win.close();
			}
		});

		it("should refuse the console to an ordinary account", async function () {
			// The module is hidden from non-administrators, but this window has a
			// chrome URL and can be opened directly. Every request it would make
			// 403s, so it says so rather than rendering a wall of failures.
			await Zotero.Pharos.API.setToken('test-token-plain');
			stubAdmin({
				identify: async () => Object.assign({}, ME, { is_admin: false }),
				getStats: async () => assert.fail('must not query as a non-admin'),
				listUsers: async () => assert.fail('must not query as a non-admin'),
			});
			var win = await loadWindow("chrome://zotero/content/pharosAdmin.xhtml");
			try {
				await win.Zotero_Pharos_Admin.initialized;
				assert.equal(
					win.document.getElementById('pharos-admin-status').textContent,
					Zotero.getString('pharos-admin-forbidden')
				);
				assert.lengthOf(win.document.getElementById('pharos-admin-body').children, 0);
			}
			finally {
				win.close();
			}
		});

		it("should withhold the destructive actions on the operator's own row", async function () {
			// The backend refuses to let anyone demote, deactivate or delete
			// themselves, so offering the buttons would only produce 409s.
			await Zotero.Pharos.API.setToken('test-token-admin');
			var mine = Object.assign({}, OTHER, {
				id: ME.id,
				email: ME.email,
				display_name: ME.display_name,
				is_admin: true,
			});
			stubAdmin({
				identify: async () => ME,
				getStats: async () => STATS,
				listUsers: async () => ({
					users: [mine, OTHER],
					total: 2,
					limit: 200,
					offset: 0,
				}),
			});
			var win = await loadWindow("chrome://zotero/content/pharosAdmin.xhtml");
			try {
				await win.Zotero_Pharos_Admin.initialized;
				var rows = win.document.querySelectorAll('.pharos-admin-table tbody tr');
				assert.lengthOf(rows, 2);
				assert.lengthOf(rows[0].querySelectorAll('button'), 0);
				// Promote/demote, deactivate/restore, delete.
				assert.lengthOf(rows[1].querySelectorAll('button'), 3);
			}
			finally {
				win.close();
			}
		});

		it("should not delete until the account's email has been typed", async function () {
			// This is the whole point of the confirmation: retyping the address
			// is the only proof available that the operator read WHICH account
			// they are erasing, and the backend verifies the same string.
			await Zotero.Pharos.API.setToken('test-token-admin');
			var deleted = null;
			var deletedResolve;
			var deletedPromise = new Promise(resolve => (deletedResolve = resolve));
			stubAdmin({
				identify: async () => ME,
				getStats: async () => STATS,
				listUsers: async () => ({ users: [OTHER], total: 1, limit: 200, offset: 0 }),
				deleteUser: async (id, email) => {
					deleted = { id, email };
					deletedResolve();
				},
			});
			var win = await loadWindow("chrome://zotero/content/pharosAdmin.xhtml");
			try {
				await win.Zotero_Pharos_Admin.initialized;
				var buttons = win.document.querySelectorAll('.pharos-admin-actions button');
				buttons[buttons.length - 1].click();

				var input = win.document.querySelector('.pharos-admin-dialog-input');
				var confirm = win.document.querySelector(
					'.pharos-admin-dialog-actions button.pharos-admin-danger'
				);
				assert.ok(input);
				assert.isTrue(confirm.disabled, 'armed before anything was typed');

				input.value = 'ada@example.org';
				input.dispatchEvent(new win.Event('input'));
				assert.isTrue(confirm.disabled, 'armed by a different address');

				input.value = OTHER.email.toUpperCase();
				input.dispatchEvent(new win.Event('input'));
				assert.isFalse(confirm.disabled, 'still refusing the right address');

				confirm.click();
				await deletedPromise;
				assert.equal(deleted.id, OTHER.id);
				// Sent as typed: the backend compares case-insensitively, and it
				// is the value the operator saw that should be checked.
				assert.equal(deleted.email.toLowerCase(), OTHER.email);

				// Deletion is followed by a reload. Letting it finish before the
				// window closes keeps a rejected promise from landing in whatever
				// test runs next.
				await Zotero.Promise.delay(50);
				assert.isNull(win.document.querySelector('.pharos-admin-overlay'));
			}
			finally {
				win.close();
			}
		});
	});
});
