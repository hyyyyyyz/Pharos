describe("Zotero.Pharos.Theme", function () {
	// The theme's ground colours, taken from the two Sass maps. Only used to
	// measure contrast; nothing here changes them.
	const LIGHT_GROUND = '#f7f2e9';
	const DARK_GROUND = '#0b1524';

	var origAccent;

	/** WCAG relative luminance of a '#rrggbb'. */
	function luminance(hex) {
		let channels = [1, 3, 5]
			.map(i => parseInt(hex.substr(i, 2), 16) / 255)
			.map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
		return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
	}

	function contrast(a, b) {
		let x = luminance(a);
		let y = luminance(b);
		return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
	}

	before(function () {
		origAccent = Zotero.Prefs.get('pharos.appearance.accent');
		// Idempotent, and guarantees the pref observer exists even in a build
		// where startup has not been wired to call it.
		Zotero.Pharos.Theme.init();
	});

	after(async function () {
		if (origAccent === undefined) {
			try {
				Zotero.Prefs.clear('pharos.appearance.accent');
			}
			catch (e) {
				// Never had a user value
			}
		}
		else {
			Zotero.Prefs.set('pharos.appearance.accent', origAccent);
		}
		await Zotero.Promise.delay(1);
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Theme.getVars);
		assert.isFunction(Zotero.Pharos.Theme.apply);
	});

	describe("#getAccentKeys()", function () {
		it("should offer the web client's ten accents in its order", function () {
			// Same list, same order, same names as
			// frontend/src/design/tokens.ts, so that a user who moves between
			// the two surfaces is choosing from one palette. The two brand
			// accents lead.
			assert.deepEqual(Zotero.Pharos.Theme.getAccentKeys(), [
				'pharos', 'beacon', 'mint', 'sky', 'pine',
				'indigo', 'lilac', 'coral', 'amber', 'stone'
			]);
		});
	});

	describe("#getAccent()", function () {
		it("should fall back to 灯塔蓝 for a value that is not an accent", function () {
			// A pref that names nothing would otherwise produce a string that
			// is not a colour, and setProperty drops those silently -- the UI
			// would keep whatever accent it had with nothing logged.
			Zotero.Prefs.set('pharos.appearance.accent', 'not-an-accent');
			assert.equal(Zotero.Pharos.Theme.getAccent(), 'pharos');
		});

		it("should return a value that is an accent", function () {
			Zotero.Prefs.set('pharos.appearance.accent', 'coral');
			assert.equal(Zotero.Pharos.Theme.getAccent(), 'coral');
		});
	});

	describe("#getVars()", function () {
		it("should reproduce the compiled light theme for the default accent", function () {
			// These are scss/themes/_light.scss verbatim. The runtime path and
			// the compiled one have to agree on the default, or every window
			// would shift the moment the accent was applied for the first time
			// -- including before the user has chosen anything.
			let vars = Zotero.Pharos.Theme.getVars('pharos', false);
			assert.equal(vars['--accent-blue'], '#182e4e');
			assert.equal(vars['--accent-blue10'], '#182e4e1a');
			assert.equal(vars['--accent-blue30'], '#182e4e4d');
			assert.equal(vars['--accent-blue50'], '#182e4e80');
			assert.equal(vars['--accent-text'], '#fffcf7');
		});

		it("should reproduce the compiled dark theme for the default accent", function () {
			// scss/themes/_dark.scss verbatim. Note the heavier alphas: a light
			// accent at a tenth of its strength over a near-black ground is not
			// a tint, so the dark theme's 10/30/50 are really 30/45/60.
			let vars = Zotero.Pharos.Theme.getVars('pharos', true);
			assert.equal(vars['--accent-blue'], '#82a6dd');
			assert.equal(vars['--accent-blue10'], '#82a6dd4d');
			assert.equal(vars['--accent-blue30'], '#82a6dd73');
			assert.equal(vars['--accent-blue50'], '#82a6dd99');
			assert.equal(vars['--accent-text'], '#08111d');
		});

		it("should set --color-accent alongside --accent-blue", function () {
			// scss/base/_base.scss aliases these inside its -moz-platform
			// blocks. They are set explicitly so the selected row -- the most
			// prominent surface in the app -- cannot be left on the compiled
			// accent by a platform without a block.
			for (let dark of [false, true]) {
				let vars = Zotero.Pharos.Theme.getVars('lilac', dark);
				assert.equal(vars['--color-accent'], vars['--accent-blue']);
				assert.equal(vars['--color-accent-text'], vars['--accent-text']);
			}
		});

		it("should keep the alpha ramp on the accent's own colour", function () {
			for (let key of Zotero.Pharos.Theme.getAccentKeys()) {
				for (let dark of [false, true]) {
					let vars = Zotero.Pharos.Theme.getVars(key, dark);
					for (let name of ['--accent-blue10', '--accent-blue30', '--accent-blue50']) {
						assert.equal(vars[name].length, 9, `${name} for ${key} is not #rrggbbaa`);
						assert.equal(vars[name].slice(0, 7), vars['--accent-blue'],
							`${name} for ${key} is not the accent`);
					}
				}
			}
		});

		it("should keep every accent legible on its ground and under its label", function () {
			// The reason light accents are taken down to a foreground-safe
			// lightness: the client has one accent slot and uses it both as a
			// fill and as the colour of text and icons. Both readings have to
			// clear WCAG AA, in both themes.
			for (let key of Zotero.Pharos.Theme.getAccentKeys()) {
				let light = Zotero.Pharos.Theme.getVars(key, false);
				assert.isAtLeast(contrast(light['--accent-blue'], LIGHT_GROUND), 4.5,
					`${key} is not readable on the light ground`);
				assert.isAtLeast(contrast(light['--accent-text'], light['--accent-blue']), 4.5,
					`${key}'s light label is not readable on it`);

				let dark = Zotero.Pharos.Theme.getVars(key, true);
				assert.isAtLeast(contrast(dark['--accent-blue'], DARK_GROUND), 4.5,
					`${key} is not readable on the dark ground`);
				assert.isAtLeast(contrast(dark['--accent-text'], dark['--accent-blue']), 4.5,
					`${key}'s dark label is not readable on it`);
			}
		});

		it("should lift the accent in the dark theme rather than reuse the light one", function () {
			// The brand navy at its own value disappears into the navy ground.
			for (let key of Zotero.Pharos.Theme.getAccentKeys()) {
				assert.notEqual(
					Zotero.Pharos.Theme.getVars(key, true)['--accent-blue'],
					Zotero.Pharos.Theme.getVars(key, false)['--accent-blue'],
					`${key} uses one colour for both themes`
				);
			}
		});

		it("should fall back rather than emit something that is not a colour", function () {
			let vars = Zotero.Pharos.Theme.getVars('not-an-accent', false);
			assert.match(vars['--accent-blue'], /^#[0-9a-f]{6}$/);
			assert.equal(vars['--accent-blue'],
				Zotero.Pharos.Theme.getVars('pharos', false)['--accent-blue']);
		});
	});

	describe("#accentSwatch()", function () {
		it("should be the colour that will actually be applied", function () {
			// The picker must not offer a brightness the light theme's
			// lightness ceiling then takes away.
			for (let key of Zotero.Pharos.Theme.getAccentKeys()) {
				for (let dark of [false, true]) {
					assert.equal(
						Zotero.Pharos.Theme.accentSwatch(key, dark),
						Zotero.Pharos.Theme.getVars(key, dark)['--accent-blue']
					);
				}
			}
		});
	});

	describe("#apply()", function () {
		function registeredCSS() {
			let uri = Zotero.Pharos.Theme._sheetURIForTests();
			if (!uri) {
				return null;
			}
			let sss = Cc['@mozilla.org/content/style-sheet-service;1']
				.getService(Ci.nsIStyleSheetService);
			if (!sss.sheetRegistered(uri, sss.USER_SHEET)) {
				return null;
			}
			// The sheet is a data: URI, so its own text is what to assert on.
			return decodeURIComponent(
				uri.spec.replace(/^data:text\/css;charset=utf-8,/, '')
			);
		}

		it("should register a user stylesheet", function () {
			Zotero.Pharos.Theme.apply();
			assert.isNotNull(registeredCSS(), "a sheet is registered");
		});

		it("should scope itself away from ordinary web pages", function () {
			// An unscoped USER_SHEET reaches every document in the process,
			// including the arbitrary pages HiddenBrowser and basicViewer load
			// with JavaScript enabled. --color-accent is a generic enough name to
			// collide with a page's own, and a script that knows the name can
			// read the computed value.
			Zotero.Pharos.Theme.apply();
			let css = registeredCSS();
			assert.include(css, '@-moz-document');
			assert.include(css, 'url-prefix("chrome://")');
			assert.include(css, 'url-prefix("resource://zotero/reader/")');
			assert.include(css, 'url-prefix("resource://zotero/note-editor/")');
		});

		it("should carry both colour schemes", function () {
			// The sheet expresses the condition rather than being rebuilt when
			// the OS theme flips, so there is nothing to listen for.
			Zotero.Pharos.Theme.apply();
			let css = registeredCSS();
			let key = Zotero.Pharos.Theme.getAccent();
			assert.include(css, '@media (prefers-color-scheme: dark)');
			assert.include(css, Zotero.Pharos.Theme.getVars(key, false)['--accent-blue']);
			assert.include(css, Zotero.Pharos.Theme.getVars(key, true)['--accent-blue']);
		});

		it("should declare every accent property", function () {
			Zotero.Prefs.set('pharos.appearance.accent', 'mint');
			Zotero.Pharos.Theme.apply();
			let css = registeredCSS();
			for (let [name, value] of Object.entries(
					Zotero.Pharos.Theme.getVars('mint', false))) {
				assert.include(css, name + ': ' + value + ' !important', name);
			}
		});

		it("should replace rather than stack on an accent change", function () {
			// Registering without unregistering would leave the old sheet in
			// place; which one wins then becomes a question about sheet order
			// rather than about what the user chose.
			Zotero.Prefs.set('pharos.appearance.accent', 'coral');
			Zotero.Pharos.Theme.apply();
			let coral = registeredCSS();
			Zotero.Prefs.set('pharos.appearance.accent', 'pine');
			Zotero.Pharos.Theme.apply();
			let pine = registeredCSS();

			assert.notEqual(coral, pine);
			assert.include(pine, Zotero.Pharos.Theme.getVars('pine', false)['--accent-blue']);
			assert.notInclude(pine, Zotero.Pharos.Theme.getVars('coral', false)['--accent-blue']);
		});

		it("should re-register when the pref changes", async function () {
			// The observer registered in init() is the point: a window that did
			// not make the change still has to receive it, and with one
			// process-wide stylesheet that means rebuilding the sheet.
			Zotero.Prefs.set('pharos.appearance.accent', 'sky');
			await Zotero.Promise.delay(10);
			let sky = Zotero.Pharos.Theme._sheetURIForTests();

			Zotero.Prefs.set('pharos.appearance.accent', 'lilac');
			await Zotero.Promise.delay(10);
			let lilac = Zotero.Pharos.Theme._sheetURIForTests();

			assert.ok(lilac);
			assert.notEqual(sky && sky.spec, lilac.spec);
		});
	});
});
