describe("Zotero.Pharos.Chat", function () {
	var win, zp, origBaseURL;

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	beforeEach(function () {
		Zotero.Pharos.Chat._clearCache();
	});

	after(async function () {
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		await Zotero.Pharos.API.setToken(null);
		win.close();
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Chat.sendMessage);
	});

	describe("#getAttachment()", function () {
		it("should return a PDF attachment directly", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			assert.equal(Zotero.Pharos.Chat.getAttachment(attachment).id, attachment.id);
		});

		it("should find the PDF under a regular item", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			assert.equal(Zotero.Pharos.Chat.getAttachment(item).id, attachment.id);
		});

		it("should return null for an item with no PDF", async function () {
			var item = await createDataObject('item');
			await importFileAttachment('test.png', { parentItemID: item.id });
			assert.isNull(Zotero.Pharos.Chat.getAttachment(item));
		});

		it("should return null for a note", async function () {
			var note = await createDataObject('item', { itemType: 'note' });
			assert.isNull(Zotero.Pharos.Chat.getAttachment(note));
		});

		it("should tolerate no item at all", function () {
			// The item pane sets .item = null between selections, and a throw
			// there would break rendering rather than just hiding the section.
			assert.isNull(Zotero.Pharos.Chat.getAttachment(null));
		});
	});

	describe("#canChat()", function () {
		it("should accept an item with a PDF", async function () {
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			assert.isTrue(Zotero.Pharos.Chat.canChat(item));
		});

		it("should reject an item without one", async function () {
			var item = await createDataObject('item');
			assert.isFalse(Zotero.Pharos.Chat.canChat(item));
		});
	});

	describe("#sendMessage()", function () {
		it("should refuse to send when signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			var e = await getPromiseError(
				Zotero.Pharos.Chat.sendMessage('conv-1', 'hello', {})
			);
			assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
		});
	});

	describe("item pane section", function () {
		beforeEach(async function () {
			await selectLibrary(win);
		});

		it("should show for an item with a PDF", async function () {
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			await zp.itemsView.selectItems([item.id]);
			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			assert.ok(box, "the section element exists");
			// Set explicitly rather than waiting on the pane's own render pass,
			// which is what the section's `item` setter reacts to.
			box.item = item;
			assert.isFalse(box.hidden);
		});

		it("should hide for an item without a PDF", async function () {
			var item = await createDataObject('item');

			await zp.itemsView.selectItems([item.id]);
			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			box.item = item;
			assert.isTrue(box.hidden);
		});

		it("should have a sidenav button", function () {
			// The button only appears if "pharos-chat" is in _builtInPanes; a
			// pane missing from that list is treated as a plugin's and looks for
			// an icon that does not exist.
			var sidenav = win.document.querySelector('item-pane-sidenav');
			assert.ok(sidenav.querySelector('.btn[data-pane="pharos-chat"]'));
		});

		it("should not carry a conversation across items", async function () {
			// Switching items must reset the thread: continuing the previous
			// conversation would answer questions about the wrong paper.
			var first = await createDataObject('item');
			await importPDFAttachment(first);
			var second = await createDataObject('item');
			await importPDFAttachment(second);

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			box.item = first;
			box._conversationID = 'conversation-for-first';
			box.item = second;
			assert.isNull(box._conversationID);
		});
	});
});
