describe("Zotero.Pharos.Translate", function () {
	var win, zp;

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
	});

	after(function () {
		win.close();
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Translate.translateItems);
	});

	it("should register its progress queue", function () {
		// The context menu handler calls ProgressQueues.get('pharos-translate')
		// by name; a typo there is invisible until someone runs a translation.
		assert.ok(Zotero.ProgressQueues.get('pharos-translate'));
	});

	describe("#canTranslate()", function () {
		it("should accept a stored PDF attachment", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			assert.isTrue(Zotero.Pharos.Translate.canTranslate(attachment));
		});

		it("should reject a non-PDF attachment", async function () {
			var attachment = await importFileAttachment('test.png');
			assert.isFalse(Zotero.Pharos.Translate.canTranslate(attachment));
		});

		it("should reject a regular item", async function () {
			var item = await createDataObject('item');
			assert.isFalse(Zotero.Pharos.Translate.canTranslate(item));
		});

		it("should reject a note", async function () {
			var note = await createDataObject('item', { itemType: 'note' });
			assert.isFalse(Zotero.Pharos.Translate.canTranslate(note));
		});

		it("should reject a linked-URL attachment", async function () {
			// There is no file to upload, so this has to be excluded before the
			// upload step rather than failing there.
			var item = await createDataObject('item');
			var attachment = await Zotero.Attachments.linkFromURL({
				parentItemID: item.id,
				url: 'https://example.com/paper.pdf',
				contentType: 'application/pdf',
				title: 'Linked PDF',
			});
			assert.isFalse(Zotero.Pharos.Translate.canTranslate(attachment));
		});
	});

	describe("#hasTranslatableAttachment()", function () {
		it("should accept a regular item with a PDF child", async function () {
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			assert.isTrue(Zotero.Pharos.Translate.hasTranslatableAttachment(item));
		});

		it("should reject a regular item whose only child is not a PDF", async function () {
			var item = await createDataObject('item');
			await importFileAttachment('test.png', { parentItemID: item.id });
			assert.isFalse(Zotero.Pharos.Translate.hasTranslatableAttachment(item));
		});

		it("should reject a regular item with no attachments", async function () {
			var item = await createDataObject('item');
			assert.isFalse(Zotero.Pharos.Translate.hasTranslatableAttachment(item));
		});
	});

	describe("context menu", function () {
		// These select the PARENT item rather than the PDF itself: selecting a
		// PDF attachment in the items pane opens the reader, which cannot load a
		// file in the test harness, and the failure surfaces here rather than
		// where it comes from. The parent exercises the same code path, since
		// hasTranslatableAttachment() looks through to the children.

		beforeEach(async function () {
			// Without a selected collection-tree row there is no itemsView, and
			// buildItemContextMenu takes an early branch that never reaches the
			// entries under test.
			await selectLibrary(win);
		});

		it("should show the translation menu for an item with a PDF", async function () {
			// The test that matters most: buildItemContextMenu addresses menu
			// children by INDEX into its `options` array, so that array and
			// zoteroPane.xhtml have to stay in the same order. Getting that wrong
			// does not throw -- it silently shows or hides the wrong entry.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			await zp.itemsView.selectItems([item.id]);
			await zp.buildItemContextMenu();

			var menu = win.document.getElementById('zotero-itemmenu');
			var entry = menu.querySelector('.zotero-menuitem-pharos-translate');
			assert.ok(entry, "the Pharos entry exists in the menu");
			assert.isFalse(entry.hidden, "the Pharos entry is visible for a PDF");
		});

		it("should hide the translation menu for a non-PDF attachment", async function () {
			var attachment = await importFileAttachment('test.png');

			await zp.itemsView.selectItems([attachment.id]);
			await zp.buildItemContextMenu();

			var menu = win.document.getElementById('zotero-itemmenu');
			assert.isTrue(menu.querySelector('.zotero-menuitem-pharos-translate').hidden);
		});

		it("should be a submenu rather than a single action", async function () {
			// The two modes cannot be counted here: a XUL menupopup's children
			// are not instantiated until the popup is first opened, so querying
			// them from a test reports zero rather than failing. What is
			// checkable is that the entry is a <menu> -- if it were ever
			// flattened to a <menuitem>, one of the two modes would be gone.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			await zp.itemsView.selectItems([item.id]);
			await zp.buildItemContextMenu();

			var menu = win.document.getElementById('zotero-itemmenu');
			var entry = menu.querySelector('.zotero-menuitem-pharos-translate');
			assert.equal(entry.tagName, 'menu');
		});

		it("should have two distinct translation modes", function () {
			assert.notEqual(
				Zotero.Pharos.Translate.MODE_MONO,
				Zotero.Pharos.Translate.MODE_DUAL
			);
		});
	});
});
