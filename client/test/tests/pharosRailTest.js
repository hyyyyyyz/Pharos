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
		assert.lengthOf(buttons, 4);
		assert.deepEqual(
			Array.from(buttons).map(b => b.dataset.module),
			['library', 'daily', 'discovery', 'projects']
		);
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
			rail.collapsed = false;
		});

		it("should persist the collapsed state", function () {
			rail.collapsed = true;
			assert.isTrue(Zotero.Prefs.get('pharos.rail.collapsed'));
			assert.equal(rail.getAttribute('collapsed'), 'true');
			rail.collapsed = false;
			assert.isFalse(Zotero.Prefs.get('pharos.rail.collapsed'));
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
