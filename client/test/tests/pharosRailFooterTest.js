describe("Pharos module rail account footer", function () {
	var win, doc, rail, footer, name, sub;
	var origEmail, origCollapsed;
	var origOpenPreferences = null;
	var opened;

	/** Record which preference pane the footer asked for, and open nothing. */
	function captureOpenPreferences() {
		opened = [];
		origOpenPreferences = Zotero.Utilities.Internal.openPreferences;
		Zotero.Utilities.Internal.openPreferences = function (paneID) {
			opened.push(paneID);
			return null;
		};
	}

	function restoreOpenPreferences() {
		if (origOpenPreferences) {
			Zotero.Utilities.Internal.openPreferences = origOpenPreferences;
			origOpenPreferences = null;
		}
	}

	before(async function () {
		origEmail = Zotero.Prefs.get('pharos.accountEmail');
		origCollapsed = Zotero.Prefs.get('pharos.rail.collapsed');

		win = await loadZoteroPane();
		doc = win.document;
		rail = doc.getElementById('pharos-rail');
		footer = rail.querySelector('.pharos-rail-acct');
		name = rail.querySelector('.pharos-rail-acct-name');
		sub = rail.querySelector('.pharos-rail-acct-sub');
	});

	afterEach(function () {
		restoreOpenPreferences();
		rail.railCollapsed = false;
	});

	after(async function () {
		restoreOpenPreferences();
		// A real token, so hasCredentials() answers for real rather than through
		// a stub. It has to go again, or the next test file starts signed in.
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.accountEmail', origEmail || '');
		Zotero.Prefs.set('pharos.rail.collapsed', !!origCollapsed);
		win.close();
	});

	it("should sit at the bottom of the rail", function () {
		var inner = rail.querySelector('.pharos-rail-inner');
		assert.ok(footer, "the account button is present");
		assert.equal(inner.lastElementChild, footer,
			"the account is the last thing in the column");
		assert.ok(footer.previousElementSibling.classList.contains('pharos-rail-spacer'),
			"a spacer is what pushes it down there");
		// The spacer taking the leftover height is the whole mechanism: without
		// it the footer sits directly under the last module and the rail looks
		// like it lost half its content.
		assert.isAtLeast(
			footer.getBoundingClientRect().top,
			rail.querySelector('.pharos-rail-nav').getBoundingClientRect().bottom,
			"below the modules, not among them"
		);
	});

	describe("signed in", function () {
		beforeEach(async function () {
			await Zotero.Pharos.API.setToken('test-token-footer');
		});

		it("should show the account's address", function () {
			rail._paintAccount('reader@example.edu');
			assert.equal(name.textContent, 'reader@example.edu');
			assert.isNotOk(name.getAttribute('data-l10n-id'),
				"an address is not a translatable string, and a leftover id would"
				+ " let DOM localization paint over it");
			assert.equal(sub.getAttribute('data-l10n-id'), 'pharos-rail-account-settings');
			assert.equal(footer.getAttribute('data-l10n-id'), 'pharos-rail-account-tooltip');
		});

		it("should paint from the cached address with no round trip", function () {
			// The pref is what the preferences pane caches and what this reads, so
			// a signed-in user never sees "not signed in" while a request is in
			// flight. Breaking the API proves the first paint never needed it.
			var origRequest = Zotero.Pharos.API.request;
			Zotero.Pharos.API.request = function () {
				throw new Error("the footer must not need the server to paint");
			};
			try {
				Zotero.Prefs.set('pharos.accountEmail', 'cached@example.edu');
				rail._paintAccount(Zotero.Prefs.get('pharos.accountEmail'));
			}
			finally {
				Zotero.Pharos.API.request = origRequest;
			}
			assert.equal(name.textContent, 'cached@example.edu');
		});

		it("should never paint the credential itself", function () {
			// The address identifies the account; the token is what opens it. Only
			// one of the two belongs in the DOM.
			rail._paintAccount('reader@example.edu');
			assert.notInclude(rail.innerHTML, 'test-token-footer');
		});
	});

	describe("signed out", function () {
		beforeEach(async function () {
			await Zotero.Pharos.API.setToken(null);
		});

		it("should say so", function () {
			rail._paintAccount(null);
			assert.equal(name.getAttribute('data-l10n-id'), 'pharos-rail-account-none');
			assert.equal(sub.getAttribute('data-l10n-id'), 'pharos-rail-account-sign-in');
			assert.equal(
				footer.getAttribute('data-l10n-id'),
				'pharos-rail-account-sign-in-tooltip'
			);
		});

		it("should drop an address the token no longer backs", function () {
			// The cached address outlives the token: a 401 clears the token from
			// under it, and painting the pref alone would keep showing a signed-out
			// user as signed in until they next opened the preferences pane.
			rail._paintAccount('stale@example.edu');
			assert.notInclude(footer.textContent, 'stale@example.edu');
			assert.equal(name.getAttribute('data-l10n-id'), 'pharos-rail-account-none');
		});

		it("should still be the way in", function () {
			rail._paintAccount(null);
			assert.isAbove(footer.getBoundingClientRect().width, 0,
				"signing in is what the footer is for when there is no account");
		});
	});

	describe("opening the account pane", function () {
		beforeEach(function () {
			captureOpenPreferences();
		});

		it("should open the Pharos preferences pane", function () {
			footer.click();
			assert.deepEqual(opened, ['zotero-prefpane-pharos']);
		});

		it("should name a pane that exists", function () {
			// openPreferences() hands an unknown id to navigateToPane, which finds
			// nothing and leaves the window on whichever pane it was already
			// showing. A renamed pane would therefore break this button silently.
			footer.click();
			assert.lengthOf(opened, 1);
			assert.ok(
				Zotero.PreferencePanes.builtInPanes.some(pane => pane.id == opened[0]),
				`${opened[0]} is a registered preference pane`
			);
		});

		it("should be reachable from the keyboard", function () {
			// This is the only route from the main window to signing in, and a
			// control the keyboard cannot reach is not a route. The collapse
			// toggle above it is a tab stop for the same reason; only the module
			// list is roving, so the rail costs three stops rather than seven.
			assert.notEqual(footer.getAttribute('tabindex'), '-1');
			footer.focus();
			assert.equal(doc.activeElement, footer);
			footer.dispatchEvent(new win.KeyboardEvent('keydown', {
				key: 'ArrowUp',
				bubbles: true,
			}));
			// The rail's arrow-key handler belongs to the module list. Reaching the
			// footer must not put arrow keys in charge of switching modules.
			assert.deepEqual(opened, [], "no pane opened by an arrow key");
		});
	});

	describe("collapsing", function () {
		it("should keep the avatar and drop the text", function () {
			rail.railCollapsed = true;
			assert.isAbove(footer.getBoundingClientRect().width, 0,
				"the account is still there");
			assert.isAbove(
				footer.querySelector('.pharos-rail-acct-avatar').getBoundingClientRect().width,
				0,
				"the avatar is what identifies it when collapsed"
			);
			assert.equal(
				footer.querySelector('.pharos-rail-acct-text').getBoundingClientRect().width,
				0,
				"the labels are hidden, as they are for the modules"
			);
		});

		it("should still open the account pane", function () {
			captureOpenPreferences();
			rail.railCollapsed = true;
			footer.click();
			assert.deepEqual(opened, ['zotero-prefpane-pharos']);
		});

		it("should become a square the size of a collapsed module", function () {
			// The web client swaps in a bare 30px circle here. This keeps one
			// element in both states so the tooltip, the l10n ids and the click
			// handler stay on a single node -- which is what the tests above
			// address -- and matches the collapsed modules instead.
			rail.railCollapsed = true;
			var module = rail.querySelector('.pharos-rail-item');
			var box = footer.getBoundingClientRect();
			assert.equal(Math.round(box.width),
				Math.round(module.getBoundingClientRect().width),
				"the same square the modules become");
			// The rows are fixed-height chrome and the spacer is what takes the
			// column's spare height. As ordinary flex items they were compressed
			// instead, and a 32px row rendering at 25px looks like a slightly
			// tight rail rather than like a bug.
			assert.equal(Math.round(box.width), Math.round(box.height),
				"square, so it centres like the modules do");
		});
	});
});
