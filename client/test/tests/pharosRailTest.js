describe("Pharos module rail", function () {
	var win, doc, rail, deck;

	before(async function () {
		win = await loadZoteroPane();
		doc = win.document;
		rail = doc.getElementById('pharos-rail');
		deck = doc.getElementById('pharos-deck');
	});

	beforeEach(function () {
		rail.module = 'library';
	});

	after(function () {
		win.close();
	});

	it("should exist in the main window", function () {
		assert.ok(rail, "the rail element is present");
		assert.ok(deck, "the module deck is present");
	});

	it("should offer every module", function () {
		var buttons = rail.querySelectorAll('.pharos-rail-item');
		assert.lengthOf(buttons, 5);
		assert.deepEqual(
			Array.from(buttons).map(b => b.dataset.module),
			['library', 'daily', 'discovery', 'projects', 'models']
		);
	});

	it("should offer the model console to ordinary accounts, not the admin console", function () {
		// The model console occupies the slot an operator's admin console
		// would: one bottom slot, never both.
		var keys = Array.from(rail.querySelectorAll('.pharos-rail-item'))
			.map(button => button.dataset.module);
		assert.include(keys, 'models');
		assert.notInclude(keys, 'admin');
	});

	it("should start on the library", function () {
		// Index 0 rather than any code running: the library panel is the deck's
		// first child, so it is what shows before anything is clicked.
		assert.equal(rail.module, 'library');
		assert.isFalse(deck.children[0].hidden);
	});

	it("should keep the rail order and the deck panels in step", function () {
		// The rail addresses panels by index into its MODULES list. If that list
		// and the deck's children ever diverge, a module quietly shows the wrong
		// panel rather than failing.
		var PharosRail = win.customElements.get('pharos-rail');
		assert.equal(PharosRail.MODULES.length, deck.children.length);
		assert.equal(PharosRail.MODULES[0].key, 'library');
		for (let i = 1; i < PharosRail.MODULES.length; i++) {
			assert.equal(
				deck.children[i].id,
				`pharos-view-${PharosRail.MODULES[i].key}`,
				`panel ${i} matches rail entry ${i}`
			);
		}
	});

	describe("switching modules", function () {
		it("should show the matching panel", function () {
			for (let [i, key] of ['library', 'daily', 'discovery', 'projects'].entries()) {
				rail.module = key;
				for (let [j, panel] of Array.from(deck.children).entries()) {
					assert.equal(panel.hidden, j != i, `${key}: panel ${j}`);
				}
			}
		});

		it("should mark the active entry", function () {
			rail.module = 'daily';
			var active = rail.querySelectorAll('.pharos-rail-item.is-active');
			assert.lengthOf(active, 1);
			assert.equal(active[0].dataset.module, 'daily');
			assert.equal(active[0].getAttribute('aria-selected'), 'true');
		});

		it("should ignore an unknown module", function () {
			rail.module = 'daily';
			rail.module = 'not-a-module';
			assert.equal(rail.module, 'daily', "the previous module is kept");
		});

		it("should load a view's document only once it is shown", function () {
			// Each view queries the backend as it loads. Giving all three a src up
			// front would fire three requests at every start, for modules the user
			// may never open.
			var browser = doc.getElementById('pharos-view-projects');
			// Reset rather than relying on a pristine panel: an earlier test in
			// this file may already have shown this module, and a test that only
			// passes in a particular order is worse than no test.
			browser.removeAttribute('src');
			rail.module = 'library';
			assert.isNotOk(browser.getAttribute('src'),
				"stays unloaded while another module is showing");
			rail.module = 'projects';
			assert.include(browser.getAttribute('src'), 'pharosProjects.xhtml');
		});

		it("should return to a working library view", async function () {
			// The point of wrapping #zotero-trees in a deck rather than replacing
			// the item tree: Zotero's own view is untouched and simply hidden.
			rail.module = 'daily';
			rail.module = 'library';
			assert.isFalse(deck.children[0].hidden);
			assert.ok(doc.getElementById('zotero-collections-tree'));
			assert.ok(doc.getElementById('zotero-items-tree'));
			assert.isFalse(doc.getElementById('zotero-trees').hidden);
		});
	});

	describe("collapsing", function () {
		afterEach(function () {
			rail.railCollapsed = false;
		});

		it("should persist the collapsed state", function () {
			rail.railCollapsed = true;
			assert.isTrue(Zotero.Prefs.get('pharos.rail.collapsed'));
			assert.equal(rail.getAttribute('rail-collapsed'), 'true');
			rail.railCollapsed = false;
			assert.isFalse(Zotero.Prefs.get('pharos.rail.collapsed'));
		});

		it("should never set XUL's reserved `collapsed` attribute", function () {
			// XUL applies `visibility: collapse` to anything carrying it, so the
			// whole rail disappeared and there was no button left to click to
			// bring it back. The state attribute is `rail-collapsed` for that
			// reason and must stay that way.
			rail.railCollapsed = true;
			assert.isNotOk(rail.getAttribute('collapsed'),
				"the rail must not collapse itself out of existence");
			assert.isAbove(rail.getBoundingClientRect().width, 0,
				"the collapsed rail is still on screen");
		});

		it("should keep the expand control reachable when collapsed", function () {
			rail.railCollapsed = true;
			var toggle = rail.querySelector('.pharos-rail-toggle');
			assert.ok(toggle);
			assert.isAbove(toggle.getBoundingClientRect().width, 0,
				"the control that expands the rail is still visible");
		});

		it("should be reachable from the keyboard", function () {
			// The toggle carried tabindex="-1", which made collapsing the rail
			// the one thing in it a keyboard could not do at all: the modules
			// have a roving tabindex and the account button keeps its own stop,
			// so this was the only gap.
			var toggle = rail.querySelector('.pharos-rail-toggle');
			assert.notEqual(toggle.getAttribute('tabindex'), '-1');
			toggle.focus();
			assert.equal(doc.activeElement, toggle, "the toggle can take focus");
			toggle.click();
			assert.isTrue(rail.railCollapsed);
		});
	});

	describe("the accent", function () {
		// A colour as the platform serializes it, so a custom property's raw
		// value can be compared with the property that resolves to it.
		function asColor(value) {
			var probe = doc.createElement('span');
			probe.style.color = value;
			doc.documentElement.append(probe);
			var color = win.getComputedStyle(probe).color;
			probe.remove();
			return color;
		}

		function token(name) {
			return asColor(win.getComputedStyle(rail).getPropertyValue(name).trim());
		}

		beforeEach(function () {
			// The rail animates its width, and the collapsed geometry below is
			// measured, so a reading taken mid-transition would be of a rail
			// part way between the two states.
			rail.style.transition = 'none';
		});

		afterEach(function () {
			rail.railCollapsed = false;
			rail.style.removeProperty('transition');
		});

		it("should paint the active module with the chosen accent", function () {
			// The rail used --color-quarternary-on-sidepane, a pure neutral mix,
			// which made the one surface that is visible at all times the single
			// place the accent picker had no effect. --accent-blue10 is the
			// theme's soft-accent surface and xpcom/pharos/theme.js rewrites it
			// when the accent changes, so pinning it here pins the whole chain.
			rail.module = 'daily';
			var active = rail.querySelector('.pharos-rail-item.is-active');
			assert.equal(
				win.getComputedStyle(active).backgroundColor,
				token('--accent-blue10'),
				"the active row is the accent at low opacity"
			);
			assert.equal(
				win.getComputedStyle(active.querySelector('.pharos-rail-icon')).color,
				token('--color-accent'),
				"and its icon is the accent itself"
			);
		});

		it("should mark the active module when collapsed", function () {
			// Collapsed there is no label, and the tint alone is 1.15:1 against
			// the rail: without the bar the active module is a slightly different
			// grey square among four identical ones.
			rail.module = 'daily';
			rail.railCollapsed = true;
			var active = rail.querySelector('.pharos-rail-item.is-active');
			var bar = win.getComputedStyle(active, '::before');
			assert.equal(bar.content, '""', "the bar is drawn");
			assert.equal(bar.backgroundColor, token('--color-accent'));
			assert.isAbove(parseFloat(bar.width), 0);
			// Inside the rail, not beside it. Rail.css offsets its own bar by the
			// expanded row's padding, which lands 3px outside a collapsed rail and
			// never paints; this one is offset by the centring gutter instead, so
			// it comes out flush with the rail's leading edge.
			assert.equal(
				Math.round(active.getBoundingClientRect().left
					+ parseFloat(bar.insetInlineStart)),
				Math.round(rail.getBoundingClientRect().left),
				"the bar is flush with the rail's edge, not outside it"
			);
		});
	});

	describe("row heights", function () {
		it("should survive the platform's cap on bare buttons", function () {
			// scss/components/_button.scss:34-39 gives every <button> on macOS
			// `max-height: 25px` and negative margins, to keep dialog buttons off
			// the non-native styling. A max-height beats a height whatever the
			// specificity -- they are different properties -- so it silently
			// overrode every row height in the rail's stylesheet: rows asking for
			// 32px rendered at 25. Nothing threw; the rail just looked tight.
			assert.equal(
				Math.round(rail.querySelector('.pharos-rail-item')
					.getBoundingClientRect().height),
				32,
				"a module row"
			);
			assert.equal(
				Math.round(rail.querySelector('.pharos-rail-acct')
					.getBoundingClientRect().height),
				40,
				"the account row"
			);
			assert.equal(
				Math.round(rail.querySelector('.pharos-rail-toggle')
					.getBoundingClientRect().height),
				26,
				"the collapse toggle"
			);
		});
	});

	describe("resizing", function () {
		var PharosRail, splitter, origPersist;

		before(function () {
			PharosRail = win.customElements.get('pharos-rail');
			splitter = doc.getElementById('pharos-rail-splitter');
			origPersist = Zotero.Prefs.get('pane.persist');
		});

		beforeEach(function () {
			// The rail animates its width, so a measurement taken straight after
			// a change would catch the transition part way rather than the value
			// under test.
			rail.style.transition = 'none';
			rail.railCollapsed = false;
			rail.resetRailWidth();
		});

		after(function () {
			rail.style.removeProperty('transition');
			rail.railCollapsed = false;
			rail.resetRailWidth();
			if (origPersist === undefined) {
				Zotero.Prefs.clear('pane.persist');
			}
			else {
				Zotero.Prefs.set('pane.persist', origPersist);
			}
		});

		it("should hold the stylesheet's limits and the element's in step", function () {
			// The stylesheet clamps what a drag can produce and PharosRail clamps
			// what a key press can. If the two ever disagree the rail stops in a
			// different place depending on how it was resized, which is not the
			// sort of thing anyone files a bug about.
			var style = win.getComputedStyle(rail);
			assert.equal(style.minWidth, `${PharosRail.MIN_WIDTH}px`);
			assert.equal(style.maxWidth, `${PharosRail.MAX_WIDTH}px`);
		});

		it("should not drag wider than the maximum", function () {
			// An inline width is what a drag leaves behind; only the stylesheet
			// stops it. There was no max-width at all before, so the rail could be
			// dragged across the whole window.
			rail.style.width = '600px';
			assert.equal(Math.round(rail.getBoundingClientRect().width),
				PharosRail.MAX_WIDTH);
		});

		it("should not drag narrower than the minimum", function () {
			// 44px is the COLLAPSED width, and it used to be the minimum too:
			// between 44 and 144 the labels are clipped by the rail's overflow
			// while the rail is not actually collapsed, which the web client
			// cannot reach because its drag clamps at 144.
			rail.style.width = '60px';
			assert.equal(Math.round(rail.getBoundingClientRect().width),
				PharosRail.MIN_WIDTH);
		});

		it("should still collapse below the minimum", function () {
			rail.style.width = '260px';
			rail.railCollapsed = true;
			assert.equal(Math.round(rail.getBoundingClientRect().width), 44,
				"the collapsed width is not subject to the drag minimum");
		});

		it("should clamp a width set in code", function () {
			rail.railWidth = 600;
			assert.equal(rail.getAttribute('width'), String(PharosRail.MAX_WIDTH));
			rail.railWidth = 10;
			assert.equal(rail.getAttribute('width'), String(PharosRail.MIN_WIDTH));
		});

		it("should persist the width", function () {
			// serializePersist() copies ATTRIBUTES into pane.persist, so a width
			// that only ever reached the inline style is lost on restart. Before
			// this the element persisted rail-collapsed and nothing else.
			assert.include(
				rail.getAttribute('zotero-persist').split(/[\s,]+/),
				'width',
				"the rail asks for its width to be persisted"
			);
			rail.railWidth = 220;
			win.ZoteroPane.serializePersist();
			var stored = JSON.parse(Zotero.Prefs.get('pane.persist'));
			assert.equal(stored['pharos-rail'].width, '220');
		});

		it("should restore a persisted width", function () {
			Zotero.Prefs.set('pane.persist',
				JSON.stringify({ 'pharos-rail': { width: 240 } }));
			win.ZoteroPane.unserializePersist();
			assert.equal(Math.round(rail.getBoundingClientRect().width), 240);
		});

		it("should clamp a persisted width that is out of range", function () {
			// pane.persist is a plain JSON pref: an older build, a hand edit or a
			// window that was dragged before the limits existed can all put a
			// number in it that the rail must not honour.
			Zotero.Prefs.set('pane.persist',
				JSON.stringify({ 'pharos-rail': { width: 900 } }));
			win.ZoteroPane.unserializePersist();
			assert.equal(Math.round(rail.getBoundingClientRect().width),
				PharosRail.MAX_WIDTH);
		});

		it("should forget a width that was reset", function () {
			rail.railWidth = 250;
			rail.resetRailWidth();
			assert.isNotOk(rail.getAttribute('width'));
			assert.isNotOk(rail.style.width, "nothing is left in the inline style");
			assert.equal(Math.round(rail.getBoundingClientRect().width), 178,
				"the stylesheet's own width is what is left");
			win.ZoteroPane.serializePersist();
			var stored = JSON.parse(Zotero.Prefs.get('pane.persist'));
			assert.isNotOk(stored['pharos-rail'].width);
		});

		it("should record the width a drag left behind", function () {
			// Which of the two places the platform writes a drag to has moved
			// between Gecko versions, and only the attribute is persisted.
			rail.style.width = '210px';
			splitter.dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true }));
			assert.equal(rail.getAttribute('width'), '210');
		});

		it("should not record a width while collapsed", function () {
			rail.railWidth = 200;
			rail.railCollapsed = true;
			splitter.dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true }));
			assert.equal(rail.getAttribute('width'), '200',
				"the collapsed 44px must not become the persisted width");
		});

		describe("the splitter", function () {
			function key(name, shiftKey) {
				splitter.dispatchEvent(new win.KeyboardEvent('keydown', {
					key: name,
					shiftKey: !!shiftKey,
					bubbles: true,
				}));
			}

			it("should be reachable from the keyboard", function () {
				assert.notEqual(splitter.getAttribute('tabindex'), '-1');
				splitter.focus();
				assert.equal(doc.activeElement, splitter);
			});

			it("should resize with the arrow keys", function () {
				var before = rail.railWidth;
				key('ArrowRight');
				assert.equal(rail.railWidth, before + 8);
				key('ArrowLeft');
				assert.equal(rail.railWidth, before);
				key('ArrowRight', true);
				assert.equal(rail.railWidth, before + 24, "shift takes a longer step");
			});

			it("should reach both limits from the keyboard", function () {
				key('Home');
				assert.equal(rail.railWidth, PharosRail.MIN_WIDTH);
				key('End');
				assert.equal(rail.railWidth, PharosRail.MAX_WIDTH);
			});

			it("should reset the width on a double click", function () {
				rail.railWidth = 250;
				splitter.dispatchEvent(new win.MouseEvent('dblclick', { bubbles: true }));
				assert.isNotOk(rail.getAttribute('width'));
				assert.equal(rail.railWidth, 178);
			});

			it("should report the width it is resizing", function () {
				rail.railWidth = 200;
				assert.equal(splitter.getAttribute('role'), 'separator');
				assert.isNotEmpty(splitter.getAttribute('aria-label'));
				assert.equal(splitter.getAttribute('aria-valuenow'), '200');
				assert.equal(splitter.getAttribute('aria-valuemin'),
					String(PharosRail.MIN_WIDTH));
				assert.equal(splitter.getAttribute('aria-valuemax'),
					String(PharosRail.MAX_WIDTH));
			});

			it("should go away when the rail is collapsed", function () {
				// A collapsed rail is a fixed 44px, so a drag beside it can only
				// write a width nothing will honour -- and that width is then
				// what gets persisted. The web client renders its handle only
				// while expanded for the same reason.
				assert.isAbove(splitter.getBoundingClientRect().width, 0);
				rail.railCollapsed = true;
				assert.equal(splitter.getBoundingClientRect().width, 0);
			});
		});
	});

	describe("Zotero's own views", function () {
		it("should still be selectable after using a Pharos module", async function () {
			// Regression guard for the failure mode this design avoids: adding a
			// collection-tree row type would have made getSearchObject() throw into
			// both the item tree and the tag selector.
			rail.module = 'discovery';
			rail.module = 'library';
			await selectLibrary(win);
			var item = await createDataObject('item');
			await win.ZoteroPane.itemsView.selectItems([item.id]);
			assert.include(win.ZoteroPane.getSelectedItems().map(i => i.id), item.id);
		});
	});
});
