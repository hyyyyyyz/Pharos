describe("Pharos Daily Preferences", function () {
	var origBaseURL;

	async function openPane() {
		var win = await loadWindow("chrome://zotero/content/preferences/preferences.xhtml", {
			pane: 'zotero-subpane-pharos-daily'
		});
		await win.Zotero_Preferences.waitForFirstPaneLoad();
		return win;
	}

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	beforeEach(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
	});

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
	});

	describe("Zotero.Pharos.Directions", function () {
		it("should be loaded onto the Zotero namespace", function () {
			assert.isFunction(Zotero.Pharos.Directions.list);
			assert.isFunction(Zotero.Pharos.Directions.reorder);
			assert.isFunction(Zotero.Pharos.Directions.getConfig);
		});

		describe("#parseKeywords()", function () {
			it("should split on newlines and commas", function () {
				assert.deepEqual(
					Zotero.Pharos.Directions.parseKeywords('world model\nvla, agent'),
					['world model', 'vla', 'agent']
				);
			});

			it("should lower-case and drop blanks", function () {
				assert.deepEqual(
					Zotero.Pharos.Directions.parseKeywords('VLA\n\n  ,  \nOpenVLA'),
					['vla', 'openvla']
				);
			});

			it("should de-duplicate preserving first-seen order", function () {
				// Order is user-visible in the settings list, so re-sorting would
				// silently rewrite what they typed.
				assert.deepEqual(
					Zotero.Pharos.Directions.parseKeywords('b\na\nB\nc\nA'),
					['b', 'a', 'c']
				);
			});

			it("should keep the quotes on a whole-word term", function () {
				// The quotes ARE the syntax. Stripping them turns a whole-word
				// match into a substring match, which is how "dit" starts firing
				// on edit, audit, credit and condition.
				assert.deepEqual(
					Zotero.Pharos.Directions.parseKeywords('"wam"\n"dit"'),
					['"wam"', '"dit"']
				);
			});

			it("should keep interior spaces and punctuation", function () {
				assert.deepEqual(
					Zotero.Pharos.Directions.parseKeywords('vision-language-action\nrt-2\nc++'),
					['vision-language-action', 'rt-2', 'c++']
				);
			});

			it("should return nothing for empty input", function () {
				assert.lengthOf(Zotero.Pharos.Directions.parseKeywords(''), 0);
				assert.lengthOf(Zotero.Pharos.Directions.parseKeywords('  \n , '), 0);
			});
		});

		describe("#isWholeWord()", function () {
			it("should recognise a quoted term", function () {
				assert.isTrue(Zotero.Pharos.Directions.isWholeWord('"wam"'));
			});

			it("should not treat a plain or half-quoted term as whole-word", function () {
				assert.isFalse(Zotero.Pharos.Directions.isWholeWord('wam'));
				assert.isFalse(Zotero.Pharos.Directions.isWholeWord('"wam'));
				assert.isFalse(Zotero.Pharos.Directions.isWholeWord('wam"'));
				// Empty quotes have nothing to match; the backend's regex needs
				// at least one character inside.
				assert.isFalse(Zotero.Pharos.Directions.isWholeWord('""'));
			});
		});

		describe("#displayKeyword()", function () {
			it("should make leading and trailing spaces visible", function () {
				// Legacy padded terms like "wam " rely on the padding, and a chip
				// that collapses it shows a word the user did not write.
				assert.equal(Zotero.Pharos.Directions.displayKeyword('wam '), 'wam␣');
				assert.equal(Zotero.Pharos.Directions.displayKeyword(' dit '), '␣dit␣');
			});

			it("should leave an ordinary term alone", function () {
				assert.equal(Zotero.Pharos.Directions.displayKeyword('world model'), 'world model');
				assert.equal(Zotero.Pharos.Directions.displayKeyword('"wam"'), '"wam"');
			});
		});

		describe("#parseCategories()", function () {
			it("should canonicalise casing the way arXiv publishes it", function () {
				var parsed = Zotero.Pharos.Directions.parseCategories('CS.ro, cs.cv');
				assert.deepEqual(parsed.categories, ['cs.RO', 'cs.CV']);
				assert.lengthOf(parsed.invalid, 0);
			});

			it("should accept hyphenated archives and long subject classes", function () {
				var parsed = Zotero.Pharos.Directions.parseCategories(
					'cond-mat.stat-mech quant-ph econ.EM'
				);
				assert.deepEqual(parsed.categories, ['cond-mat.stat-mech', 'quant-ph', 'econ.EM']);
			});

			it("should surface what it could not parse rather than dropping it", function () {
				// Only 'category!' is rejected. A bare word IS a valid category
				// shape -- quant-ph and hep-th are archives with no subject class
				// -- so 'not' and 'a' are accepted here exactly as the backend's
				// _CATEGORY_RE accepts them. The client mirrors that grammar
				// rather than inventing a stricter one the server would disagree
				// with.
				var parsed = Zotero.Pharos.Directions.parseCategories('cs.RO, not a category!');
				// Canonical form keeps the two-letter subject upper-cased, as arXiv writes it.
				assert.include(parsed.categories, 'cs.RO');
				assert.include(parsed.invalid, 'category!');
				assert.notInclude(parsed.categories, 'category!');
			});

			it("should de-duplicate across spellings", function () {
				var parsed = Zotero.Pharos.Directions.parseCategories('cs.RO\ncs.ro, CS.RO');
				assert.deepEqual(parsed.categories, ['cs.RO']);
			});
		});

		describe("#DEFAULTS", function () {
			it("should carry the quoted acronyms rather than the padded ones", function () {
				// The padding could not survive a round trip through a text box.
				// If this ever drifts back to "wam ", every restored account gets
				// a Diffusion feed full of papers containing "edit" and "audit".
				var wam = Zotero.Pharos.Directions.DEFAULTS.find(d => d.name == 'WAM');
				assert.include(wam.keywords, '"wam"');
				var diffusion = Zotero.Pharos.Directions.DEFAULTS.find(d => d.name == 'Diffusion');
				assert.include(diffusion.keywords, '"dit"');
			});

			it("should have keywords for every direction", function () {
				for (let preset of Zotero.Pharos.Directions.DEFAULTS) {
					assert.isNotEmpty(preset.name, preset.name);
					assert.isNotEmpty(preset.keywords, preset.name);
				}
			});
		});
	});

	describe("the subpane", function () {
		it("should open", async function () {
			// Covers the registration in preferencePanes.js and, just as
			// importantly, that every data-l10n-id in the pane resolves: an id
			// missing from the window's localization resources makes DOM
			// localization reject and can stop the window opening.
			var win = await openPane();
			try {
				assert.ok(win.document.getElementById('zotero-subpane-pharos-daily'));
			}
			finally {
				win.close();
			}
		});

		it("should say so when signed out instead of sitting blank", async function () {
			var win = await openPane();
			try {
				assert.isFalse(win.document.getElementById('pharos-daily-prefs-signed-out').hidden);
				assert.isTrue(win.document.getElementById('pharos-daily-prefs-body').hidden);
			}
			finally {
				win.close();
			}
		});

		it("should show the editors when a token is stored", async function () {
			// Points at a port nothing is listening on, so every request fails at
			// the transport. The pane still has to render: an unreachable server
			// is not the same as being signed out.
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			await Zotero.Pharos.API.setToken('test-token');

			var win = await openPane();
			try {
				assert.isTrue(win.document.getElementById('pharos-daily-prefs-signed-out').hidden);
				assert.isFalse(win.document.getElementById('pharos-daily-prefs-body').hidden);
				// The failure is reported rather than swallowed.
				assert.isNotEmpty(win.document.getElementById('pharos-daily-prefs-status').textContent);
			}
			finally {
				win.close();
			}
		});

		it("should never offer a field to type an API key into", async function () {
			// The invariant: a key lives in the backend, encrypted, and nowhere
			// else. A password field here would mean the key passes through this
			// process, which is the first step towards it reaching a pref or the
			// log. The panel is read-only plus a clear button, deliberately.
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			await Zotero.Pharos.API.setToken('test-token');

			var win = await openPane();
			try {
				var pane = win.document.getElementById('zotero-subpane-pharos-daily');
				assert.lengthOf(pane.querySelectorAll('input[type="password"]'), 0);
				assert.notInclude(pane.innerHTML.toLowerCase(), 'apikey');
			}
			finally {
				win.close();
			}
		});
	});

	describe("the keyword editor", function () {
		var win;

		beforeEach(async function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			await Zotero.Pharos.API.setToken('test-token');
			win = await openPane();
			win.Zotero_Preferences.PharosDaily.startCreate();
		});

		afterEach(function () {
			if (win) {
				win.close();
				win = null;
			}
		});

		it("should render the parse back, term by term", function () {
			win.document.getElementById('pharos-daily-prefs-keywords').value
				= 'world model\nlatent dynamics, genie';
			win.Zotero_Preferences.PharosDaily._renderParse();

			var chips = [...win.document.getElementById('pharos-daily-prefs-chips').children]
				.map(chip => chip.textContent);
			assert.deepEqual(chips, ['world model', 'latent dynamics', 'genie']);
		});

		it("should mark a quoted term as a whole-word match", function () {
			win.document.getElementById('pharos-daily-prefs-keywords').value = '"wam"\nagentic';
			win.Zotero_Preferences.PharosDaily._renderParse();

			var chips = [...win.document.getElementById('pharos-daily-prefs-chips').children];
			assert.include([...chips[0].classList], 'pharos-daily-prefs-chip-word');
			assert.include([...chips[1].classList], 'pharos-daily-prefs-chip-substring');
			// The quotes stay on screen: they are the syntax, not decoration.
			assert.equal(chips[0].textContent, '"wam"');
		});

		it("should trim what the user types, as the backend does", function () {
			// Space padding used to be how a short acronym asked for whole-word
			// matching (" dit "), and it was replaced precisely because it cannot
			// survive a round trip through a text box. parse_keywords trims on the
			// server; trimming here too means the chip shows what will be stored.
			win.document.getElementById('pharos-daily-prefs-keywords').value = ' dit ';
			win.Zotero_Preferences.PharosDaily._renderParse();

			var chips = [...win.document.getElementById('pharos-daily-prefs-chips').children];
			assert.equal(chips[0].textContent, 'dit');
		});

		it("should still show the padding on a legacy stored keyword", function () {
			// displayKeyword exists for terms already in the database from before
			// the quoted syntax. Those really are padded, and rendering them
			// trimmed would show the user something other than what will match.
			assert.equal(Zotero.Pharos.Directions.displayKeyword(' dit '), '␣dit␣');
			assert.equal(Zotero.Pharos.Directions.displayKeyword('dit'), 'dit');
		});

		it("should refuse to submit with no name or no keywords", function () {
			var submit = win.document.getElementById('pharos-daily-prefs-submit');

			win.document.getElementById('pharos-daily-prefs-name').value = '';
			win.document.getElementById('pharos-daily-prefs-keywords').value = 'vla';
			win.Zotero_Preferences.PharosDaily._renderParse();
			assert.isTrue(submit.disabled);

			win.document.getElementById('pharos-daily-prefs-name').value = 'VLA';
			win.document.getElementById('pharos-daily-prefs-keywords').value = '   ';
			win.Zotero_Preferences.PharosDaily._renderParse();
			assert.isTrue(submit.disabled);

			win.document.getElementById('pharos-daily-prefs-keywords').value = 'vla policy';
			win.Zotero_Preferences.PharosDaily._renderParse();
			assert.isFalse(submit.disabled);
		});

		it("should warn about a breached limit but still allow the submit", function () {
			// The limits here are copies of the backend's. A copy that has fallen
			// behind must not be able to refuse something the server would accept,
			// so every one of them warns rather than blocks.
			var limits = Zotero.Pharos.Directions.LIMITS;
			var many = [];
			for (let i = 0; i < limits.keywords + 5; i++) {
				many.push('term' + i);
			}
			win.document.getElementById('pharos-daily-prefs-name').value = 'Too Many';
			win.document.getElementById('pharos-daily-prefs-keywords').value = many.join('\n');
			win.Zotero_Preferences.PharosDaily._renderParse();

			assert.isFalse(win.document.getElementById('pharos-daily-prefs-editor-warnings').hidden);
			assert.isFalse(win.document.getElementById('pharos-daily-prefs-submit').disabled);
		});

		it("should send the raw text, not its own parse of it", async function () {
			// The whole point of the syntax being load-bearing: the backend's
			// parse decides what is stored, and posting ours instead would make
			// the client the authority on matching.
			var sent = null;
			var stub = sinon.stub(Zotero.Pharos.Directions, 'create').callsFake(function (args) {
				sent = args;
				return Promise.resolve({});
			});
			try {
				var raw = '  "WAM" ,\nWorld Action Model\n';
				win.document.getElementById('pharos-daily-prefs-name').value = 'WAM';
				win.document.getElementById('pharos-daily-prefs-keywords').value = raw;
				win.Zotero_Preferences.PharosDaily._renderParse();
				await win.Zotero_Preferences.PharosDaily.submitEditor();

				assert.ok(sent);
				assert.equal(sent.keywords, raw);
			}
			finally {
				stub.restore();
			}
		});
	});

	describe("the fetch settings", function () {
		var win;

		beforeEach(async function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			await Zotero.Pharos.API.setToken('test-token');
			win = await openPane();
		});

		afterEach(function () {
			if (win) {
				win.close();
				win = null;
			}
		});

		it("should omit max_per_day when the box is cleared", async function () {
			// Number('') is 0 and Number.isInteger(0) is true, so a naive parse
			// posts max_per_day: 0 for a box the user merely emptied -- a 400
			// from the backend for something they never typed.
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._config = { categories: ['cs.RO'], max_per_day: 60, enabled: true, seeded: true };
			pane._catDraft = 'cs.RO, cs.CV';
			pane._maxDraft = '';
			pane._renderConfig();

			var sent = null;
			var stub = sinon.stub(Zotero.Pharos.Directions, 'updateConfig').callsFake(function (changes) {
				sent = changes;
				return Promise.resolve({
					categories: ['cs.RO', 'cs.CV'], max_per_day: 60, enabled: true, seeded: true
				});
			});
			try {
				await pane.saveConfig();
				assert.deepEqual(Object.keys(sent), ['categories']);
				assert.equal(sent.categories, 'cs.RO, cs.CV');
			}
			finally {
				stub.restore();
			}
		});

		it("should follow the server's canonical spelling after a save", async function () {
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._config = { categories: ['cs.RO'], max_per_day: 60, enabled: true, seeded: true };
			pane._catDraft = 'CS.ro';
			pane._renderConfig();

			var stub = sinon.stub(Zotero.Pharos.Directions, 'updateConfig').resolves({
				categories: ['cs.RO'], max_per_day: 60, enabled: true, seeded: true
			});
			try {
				await pane.saveConfig();
				assert.isNull(pane._catDraft);
				assert.equal(win.document.getElementById('pharos-daily-prefs-categories').value, 'cs.RO');
			}
			finally {
				stub.restore();
			}
		});

		it("should warn about an out-of-range daily limit without blocking the save", function () {
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._config = { categories: ['cs.RO'], max_per_day: 60, enabled: true, seeded: true };
			pane._maxDraft = '9999';
			pane._renderConfig();

			assert.isFalse(win.document.getElementById('pharos-daily-prefs-max-warn').hidden);
			assert.isFalse(win.document.getElementById('pharos-daily-prefs-config-save').disabled);
		});
	});

	describe("the AI model panel", function () {
		var win;

		beforeEach(async function () {
			Zotero.Prefs.set('pharos.baseURL', 'http://localhost:1');
			await Zotero.Pharos.API.setToken('test-token');
			win = await openPane();
		});

		afterEach(function () {
			if (win) {
				win.close();
				win = null;
			}
		});

		it("should show a personal provider and offer to clear it", function () {
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._provider = {
				configured: true,
				hasCredential: true,
				baseUrl: 'https://example.org/v1',
				model: 'a-model',
				temperature: 0.25,
				maxOutputTokens: 4096,
				source: 'personal',
				canStoreCredential: true,
			};
			pane._renderProvider();

			assert.isFalse(win.document.getElementById('pharos-daily-prefs-provider').hidden);
			assert.equal(
				win.document.getElementById('pharos-daily-prefs-provider-base-url').textContent,
				'https://example.org/v1'
			);
			assert.isFalse(win.document.getElementById('pharos-daily-prefs-provider-clear').hidden);
		});

		it("should not offer to clear the server's provider", function () {
			// Clearing removes the caller's PERSONAL provider. Offering it here
			// would be a button that appears to do something and does not.
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._provider = {
				configured: true,
				hasCredential: false,
				baseUrl: 'https://example.org/v1',
				model: 'a-model',
				temperature: 0.25,
				maxOutputTokens: 4096,
				source: 'server',
				canStoreCredential: true,
			};
			pane._renderProvider();

			assert.isTrue(win.document.getElementById('pharos-daily-prefs-provider-clear').hidden);
		});

		it("should say when the server cannot hold a personal key at all", function () {
			var pane = win.Zotero_Preferences.PharosDaily;
			pane._provider = {
				configured: true,
				hasCredential: false,
				baseUrl: 'https://example.org/v1',
				model: 'a-model',
				temperature: 0.25,
				maxOutputTokens: 4096,
				source: 'server',
				canStoreCredential: false,
			};
			pane._renderProvider();

			assert.equal(
				win.document.getElementById('pharos-daily-prefs-provider-key').textContent,
				Zotero.getString('pharos-prefs-provider-key-unsupported')
			);
		});

		it("should not write any of the provider response to prefs", function () {
			var pane = win.Zotero_Preferences.PharosDaily;
			var stub = sinon.stub(Zotero.Prefs, 'set');
			try {
				pane._provider = {
					configured: true,
					hasCredential: true,
					baseUrl: 'https://example.org/v1',
					model: 'a-model',
					temperature: 0.25,
					maxOutputTokens: 4096,
					source: 'personal',
					canStoreCredential: true,
				};
				pane._renderProvider();
				assert.isFalse(stub.called);
			}
			finally {
				stub.restore();
			}
		});
	});
});
