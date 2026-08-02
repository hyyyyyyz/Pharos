describe("Pharos sign-in gate", function () {
	var origLogin, origRequest, origSetToken;
	var origEmailPref, origSkippedPref;
	var requests;
	var tokens;
	/** Answers keyed by API path, each either a value to resolve or an Error. */
	var responses;

	/**
	 * Swap out the API layer and record what the window would have sent.
	 *
	 * Zotero.Pharos.API is a singleton shared by every window, so replacing it
	 * here reaches the gate. The point of most of these assertions is the shape
	 * of the request: a password that never made it into the body, or a
	 * display_name sent as "" to a backend declared extra="forbid", is a failure
	 * the return value cannot show.
	 */
	function stubAPI() {
		requests = [];
		tokens = [];
		responses = {};

		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			let answer = responses[path];
			if (answer instanceof Error) {
				return Promise.reject(answer);
			}
			return Promise.resolve(answer === undefined ? null : answer);
		};

		origLogin = Zotero.Pharos.API.login;
		Zotero.Pharos.API.login = function (email, password) {
			requests.push({ method: 'POST', path: '/api/auth/login', options: { body: { email, password } } });
			let answer = responses['/api/auth/login'];
			if (answer instanceof Error) {
				return Promise.reject(answer);
			}
			return Promise.resolve(answer === undefined ? { email } : answer);
		};

		origSetToken = Zotero.Pharos.API.setToken;
		Zotero.Pharos.API.setToken = function (token) {
			tokens.push(token);
			return Promise.resolve();
		};
	}

	function restoreAPI() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
		if (origLogin) {
			Zotero.Pharos.API.login = origLogin;
			origLogin = null;
		}
		if (origSetToken) {
			Zotero.Pharos.API.setToken = origSetToken;
			origSetToken = null;
		}
	}

	function httpError(status, message) {
		let e = new Error(message || `HTTP ${status}`);
		e.status = status;
		return e;
	}

	function sentTo(path) {
		return requests.filter(r => r.path == path);
	}

	/** Passes in any locale: en-US throws on a missing id, others return it. */
	function assertLocalized(id) {
		var value = Zotero.getString(id);
		assert.isNotEmpty(value, `missing string ${id}`);
		assert.notEqual(value, id, `missing string ${id}`);
	}

	/**
	 * Open the gate and wait for init to finish.
	 *
	 * loadWindow resolves on the load event, but init() is async and keeps going
	 * after it -- in particular the registration probe. Asserting without
	 * waiting passes alone and fails whenever the machine is busier.
	 */
	async function openGate(io) {
		var win = await loadWindow("chrome://zotero/content/pharosAuth.xhtml", io);
		await win.Zotero_Pharos_Auth.initialized;
		return win;
	}

	function fill(win, { email, password, name }) {
		if (email !== undefined) {
			win.document.getElementById('pharos-auth-email').value = email;
		}
		if (password !== undefined) {
			win.document.getElementById('pharos-auth-password').value = password;
		}
		if (name !== undefined) {
			win.document.getElementById('pharos-auth-name').value = name;
		}
	}

	before(function () {
		origEmailPref = Zotero.Prefs.get('pharos.accountEmail');
		origSkippedPref = Zotero.Prefs.get('pharos.auth.skipped');
	});

	beforeEach(function () {
		stubAPI();
	});

	afterEach(function () {
		restoreAPI();
	});

	after(function () {
		restoreAPI();
		Zotero.Prefs.set('pharos.accountEmail', origEmailPref || '');
		Zotero.Prefs.set('pharos.auth.skipped', !!origSkippedPref);
	});

	describe("the window", function () {
		it("should open and show the sign-in form", async function () {
			// Opening it at all covers the localization ids in pharosAuth.xhtml:
			// one missing from the window's own resource list stops it
			// appearing, with no error anywhere.
			var win = await openGate();
			try {
				assert.ok(win.document.getElementById('pharos-auth-root'));
				assert.ok(win.document.getElementById('pharos-auth-email'));
				assert.ok(win.document.getElementById('pharos-auth-password'));
				assert.equal(win.Zotero_Pharos_Auth.getMode(), 'login');
				assert.equal(
					win.document.getElementById('pharos-auth-submit')
						.getAttribute('data-l10n-id'),
					'pharos-auth-submit-sign-in'
				);
			}
			finally {
				win.close();
			}
		});

		it("should have every string it names", function () {
			for (let id of [
				'pharos-auth-tagline',
				'pharos-auth-poster-sub',
				'pharos-auth-mode-login',
				'pharos-auth-mode-register',
				'pharos-auth-email',
				'pharos-auth-email-required',
				'pharos-auth-email-invalid',
				'pharos-auth-password',
				'pharos-auth-password-required',
				'pharos-auth-display-name',
				'pharos-auth-display-name-optional',
				'pharos-auth-submit-sign-in',
				'pharos-auth-submit-register',
				'pharos-auth-submitting-sign-in',
				'pharos-auth-submitting-register',
				'pharos-auth-register-note',
				'pharos-auth-registration-closed',
				'pharos-auth-skip',
				'pharos-auth-skip-note',
			]) {
				assertLocalized(id);
			}
			for (let i = 0; i < 8; i++) {
				assertLocalized('pharos-auth-greeting-' + i);
			}
			// Takes an argument, so it is Fluent's own formatter rather than
			// getString(), which routes params to the .properties bundle.
			assert.isNotEmpty(Zotero.ftl.formatValueSync('pharos-auth-password-short', { min: 8 }));
		});

		it("should ship the poster artwork", async function () {
			var win = await openGate();
			try {
				let img = win.document.getElementById('pharos-auth-poster-img');
				if (!img.complete) {
					await new Promise((resolve) => {
						img.addEventListener('load', resolve, { once: true });
						img.addEventListener('error', resolve, { once: true });
					});
				}
				// naturalWidth is 0 for an image that never resolved, which is
				// exactly what the brand fallback covers -- but the fallback is
				// not what should ship.
				assert.isAbove(img.naturalWidth, 0,
					'chrome://zotero/skin/pharos-poster.png resolves');
				assert.isNull(img.getAttribute('data-failed'));
				// The fallback stays in the document either way; it is simply
				// underneath.
				assert.ok(win.document.getElementById('pharos-auth-poster-fallback'));
			}
			finally {
				win.close();
			}
		});
	});

	describe("the greeting", function () {
		it("should pick one of the lines that exist", async function () {
			var win = await openGate();
			try {
				let id = win.document.getElementById('pharos-auth-foot')
					.getAttribute('data-l10n-id');
				assert.match(id, /^pharos-auth-greeting-[0-7]$/);
			}
			finally {
				win.close();
			}
		});

		it("should turn over at the reader's midnight, not UTC's", async function () {
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				// Two instants on the same local day must give the same line,
				// and consecutive days must not. Deriving the day from
				// toISOString() instead would move the line at 08:00 for a
				// reader in UTC+8.
				let morning = new Date(2026, 0, 15, 0, 30);
				let evening = new Date(2026, 0, 15, 23, 30);
				let next = new Date(2026, 0, 16, 12, 0);
				assert.equal(auth.greetingID(morning), auth.greetingID(evening));
				assert.notEqual(auth.greetingID(morning), auth.greetingID(next));
			}
			finally {
				win.close();
			}
		});
	});

	describe("registration availability", function () {
		it("should offer registration when the backend allows it", async function () {
			responses['/api/auth/status'] = { allow_registration: true };
			var win = await openGate();
			try {
				let modes = win.document.getElementById('pharos-auth-modes');
				assert.isFalse(modes.hidden);
				// The row is only invisible while the probe is in flight.
				assert.equal(modes.getAttribute('data-probing'), 'false');
				assert.isTrue(win.document.getElementById('pharos-auth-closed').hidden);
				assert.isTrue(win.Zotero_Pharos_Auth.registrationOpen());
			}
			finally {
				win.close();
			}
		});

		it("should hide the register tab when the backend has it switched off", async function () {
			responses['/api/auth/status'] = { allow_registration: false };
			var win = await openGate();
			try {
				assert.isTrue(win.document.getElementById('pharos-auth-modes').hidden);
				assert.isFalse(win.document.getElementById('pharos-auth-closed').hidden);
				assert.isFalse(win.Zotero_Pharos_Auth.registrationOpen());
			}
			finally {
				win.close();
			}
		});

		it("should refuse to switch to a mode it has hidden", async function () {
			responses['/api/auth/status'] = { allow_registration: false };
			var win = await openGate();
			try {
				win.Zotero_Pharos_Auth.setMode('register');
				assert.equal(win.Zotero_Pharos_Auth.getMode(), 'login');
			}
			finally {
				win.close();
			}
		});

		it("should ask the backend anonymously", async function () {
			responses['/api/auth/status'] = { allow_registration: true };
			var win = await openGate();
			try {
				let probes = sentTo('/api/auth/status');
				assert.lengthOf(probes, 1);
				assert.equal(probes[0].method, 'GET');
				// Signed out is the whole reason this window is open, so a probe
				// that required a token would throw before it ever asked.
				assert.isTrue(probes[0].options.anon);
			}
			finally {
				win.close();
			}
		});

		it("should still offer registration when the status endpoint is missing", async function () {
			// An older self-hosted backend has no /api/auth/status. Unknown is
			// not closed: the sign-up form must survive, and a 403 on submit is
			// what settles it -- which is how the web client discovers this.
			responses['/api/auth/status'] = httpError(404, 'Not Found');
			var win = await openGate();
			try {
				let modes = win.document.getElementById('pharos-auth-modes');
				assert.isFalse(modes.hidden);
				assert.equal(modes.getAttribute('data-probing'), 'false');
				assert.isNull(win.Zotero_Pharos_Auth.registrationOpen());
			}
			finally {
				win.close();
			}
		});

		it("should hide the register tab after the backend refuses a sign-up", async function () {
			responses['/api/auth/status'] = httpError(404, 'Not Found');
			responses['/api/auth/register'] = httpError(403, 'Registration is closed on this instance');
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				auth.setMode('register');
				fill(win, { email: 'someone@example.org', password: 'longenough' });
				await auth.submit();

				assert.isFalse(auth.registrationOpen());
				assert.equal(auth.getMode(), 'login', 'put back on sign-in');
				assert.isTrue(win.document.getElementById('pharos-auth-modes').hidden);
				assert.isFalse(win.document.getElementById('pharos-auth-closed').hidden);
				// The server's own words, not copy of ours.
				assert.include(
					win.document.getElementById('pharos-auth-alert').textContent,
					'Registration is closed'
				);
			}
			finally {
				win.close();
			}
		});
	});

	describe("validation", function () {
		it("should say nothing until something is submitted", async function () {
			var win = await openGate();
			try {
				assert.isTrue(win.document.getElementById('pharos-auth-email-error').hidden);
				assert.isTrue(win.document.getElementById('pharos-auth-password-error').hidden);
			}
			finally {
				win.close();
			}
		});

		it("should not send an empty form to the server", async function () {
			var win = await openGate();
			try {
				await win.Zotero_Pharos_Auth.submit();
				assert.isFalse(win.document.getElementById('pharos-auth-email-error').hidden);
				assert.isFalse(win.document.getElementById('pharos-auth-password-error').hidden);
				assert.lengthOf(sentTo('/api/auth/login'), 0);
			}
			finally {
				win.close();
			}
		});

		it("should catch a typo in the email", async function () {
			var win = await openGate();
			try {
				fill(win, { email: 'not-an-email', password: 'whatever' });
				await win.Zotero_Pharos_Auth.submit();
				assert.equal(
					win.document.getElementById('pharos-auth-email-error').textContent,
					Zotero.getString('pharos-auth-email-invalid')
				);
				assert.lengthOf(sentTo('/api/auth/login'), 0);
			}
			finally {
				win.close();
			}
		});

		it("should apply the length rule to registration only", async function () {
			responses['/api/auth/status'] = { allow_registration: true };
			// Fails at the server, so the window stays open for the second half
			// of the test rather than closing on a successful sign-in.
			responses['/api/auth/login'] = httpError(401, 'Incorrect email or password');
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;

				// Sign-in does NOT check the length. Rejecting a short password
				// locally while a wrong one fails at the server tells an
				// attacker their guess was at least the right shape.
				fill(win, { email: 'someone@example.org', password: 'short' });
				await auth.submit();
				assert.lengthOf(sentTo('/api/auth/login'), 1);

				auth.setMode('register');
				await auth.submit();
				assert.lengthOf(sentTo('/api/auth/register'), 0);
				assert.isFalse(win.document.getElementById('pharos-auth-password-error').hidden);
			}
			finally {
				win.close();
			}
		});
	});

	describe("signing in", function () {
		it("should hand the password to the API and to nothing else", async function () {
			const PASSWORD = 'correct horse battery staple';
			responses['/api/auth/login'] = { email: 'reader@example.org' };
			var win = await openGate();
			try {
				fill(win, { email: 'reader@example.org', password: PASSWORD });
				let field = win.document.getElementById('pharos-auth-password');
				await win.Zotero_Pharos_Auth.submit();

				let sent = sentTo('/api/auth/login');
				assert.lengthOf(sent, 1);
				assert.equal(sent[0].options.body.password, PASSWORD);

				// Cleared as soon as the token arrives, so a later failure
				// cannot leave it sitting in the DOM.
				assert.equal(field.value, '');
				// The property carries the value; the attribute must not, or
				// the password would be visible to anything that serialises
				// the document.
				assert.isNull(field.getAttribute('value'));
				assert.notEqual(Zotero.Prefs.get('pharos.accountEmail'), PASSWORD);
			}
			finally {
				win.close();
			}
		});

		it("should remember the account email for the preferences pane", async function () {
			Zotero.Prefs.set('pharos.accountEmail', '');
			responses['/api/auth/login'] = { email: 'reader@example.org' };
			var win = await openGate();
			try {
				fill(win, { email: '  reader@example.org  ', password: 'whatever' });
				await win.Zotero_Pharos_Auth.submit();
				// Trimmed: a stray space pasted with the address must not become
				// the address the server is asked about.
				assert.equal(sentTo('/api/auth/login')[0].options.body.email, 'reader@example.org');
				assert.equal(Zotero.Prefs.get('pharos.accountEmail'), 'reader@example.org');
			}
			finally {
				win.close();
			}
		});

		it("should clear an earlier skip so signing out shows the gate again", async function () {
			Zotero.Prefs.set('pharos.auth.skipped', true);
			responses['/api/auth/login'] = { email: 'reader@example.org' };
			var win = await openGate();
			try {
				fill(win, { email: 'reader@example.org', password: 'whatever' });
				await win.Zotero_Pharos_Auth.submit();
				assert.isFalse(Zotero.Prefs.get('pharos.auth.skipped'));
			}
			finally {
				win.close();
			}
		});

		it("should report the server's own message and stay open", async function () {
			responses['/api/auth/login'] = httpError(401, 'Incorrect email or password');
			var win = await openGate();
			try {
				fill(win, { email: 'reader@example.org', password: 'wrong' });
				await win.Zotero_Pharos_Auth.submit();
				assert.equal(
					win.document.getElementById('pharos-auth-alert').textContent,
					'Incorrect email or password'
				);
				assert.isFalse(win.document.getElementById('pharos-auth-alert').hidden);
				// The field keeps its value so the user can correct it, and the
				// gate is still there to correct it in.
				assert.equal(win.document.getElementById('pharos-auth-password').value, 'wrong');
				assert.isFalse(win.closed);
			}
			finally {
				win.close();
			}
		});

		it("should not show a bare status code for an unreachable server", async function () {
			// status 0 is a connection that never completed. Its message names
			// the URL and "status code 0", which nobody can act on.
			responses['/api/auth/login'] = httpError(0, 'HTTP POST https://example/api failed with status code 0');
			var win = await openGate();
			try {
				fill(win, { email: 'reader@example.org', password: 'whatever' });
				await win.Zotero_Pharos_Auth.submit();
				assert.equal(
					win.document.getElementById('pharos-auth-alert').textContent,
					Zotero.getString('pharos-error-unreachable')
				);
			}
			finally {
				win.close();
			}
		});
	});

	describe("registering", function () {
		beforeEach(function () {
			responses['/api/auth/status'] = { allow_registration: true };
			responses['/api/auth/register'] = {
				token: 'new-token',
				user: { email: 'new@example.org' },
			};
		});

		it("should post to the register endpoint and store the token", async function () {
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				auth.setMode('register');
				fill(win, { email: 'new@example.org', password: 'longenough' });
				await auth.submit();

				let sent = sentTo('/api/auth/register');
				assert.lengthOf(sent, 1);
				assert.equal(sent[0].method, 'POST');
				// Anonymous, because this is one of the two calls that mint a
				// token rather than use one.
				assert.isTrue(sent[0].options.anon);
				assert.equal(sent[0].options.body.email, 'new@example.org');
				assert.equal(sent[0].options.body.password, 'longenough');
				assert.deepEqual(tokens, ['new-token']);
				assert.equal(Zotero.Prefs.get('pharos.accountEmail'), 'new@example.org');
			}
			finally {
				win.close();
			}
		});

		it("should omit an empty display name rather than send it", async function () {
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				auth.setMode('register');
				fill(win, { email: 'new@example.org', password: 'longenough', name: '   ' });
				await auth.submit();
				// RegisterRequest is declared extra="forbid" and treats a
				// missing display_name as "use the email"; sending "" would
				// store a blank name instead.
				assert.notProperty(sentTo('/api/auth/register')[0].options.body, 'display_name');
			}
			finally {
				win.close();
			}
		});

		it("should send a display name that was given", async function () {
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				auth.setMode('register');
				fill(win, { email: 'new@example.org', password: 'longenough', name: '  Ada  ' });
				await auth.submit();
				assert.equal(sentTo('/api/auth/register')[0].options.body.display_name, 'Ada');
			}
			finally {
				win.close();
			}
		});

		it("should show the name field only in register mode", async function () {
			var win = await openGate();
			try {
				let auth = win.Zotero_Pharos_Auth;
				let row = win.document.getElementById('pharos-auth-name-row');
				assert.isTrue(row.hidden);
				auth.setMode('register');
				assert.isFalse(row.hidden);
				auth.setMode('login');
				assert.isTrue(row.hidden);
			}
			finally {
				win.close();
			}
		});
	});

	describe("skipping", function () {
		it("should offer a way past the gate", async function () {
			// Everything local -- library, reader, annotations -- works with no
			// account at all, so the gate must never be a wall.
			var win = await openGate();
			try {
				assert.ok(win.document.getElementById('pharos-auth-skip'));
				assert.isFalse(win.document.getElementById('pharos-auth-skip').hidden);
			}
			finally {
				win.close();
			}
		});

		it("should record the choice and report it to the opener", async function () {
			Zotero.Prefs.set('pharos.auth.skipped', false);
			var io = {};
			var win = await openGate(io);
			win.Zotero_Pharos_Auth.skip();
			// Recorded, so the gate stops blocking every launch for someone who
			// does not want an account.
			assert.isTrue(Zotero.Prefs.get('pharos.auth.skipped'));
			assert.deepEqual(io.dataOut, { signedIn: false, skipped: true });
			assert.lengthOf(sentTo('/api/auth/login'), 0);
		});

		it("should not sign anyone in on the way out", async function () {
			var win = await openGate();
			win.Zotero_Pharos_Auth.skip();
			assert.lengthOf(tokens, 0);
		});
	});
});
