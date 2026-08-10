describe("Zotero.Pharos.ReaderChat", function () {
	var win, zp, tabs, contextPane;

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
		tabs = win.Zotero_Tabs;
		contextPane = win.ZoteroContextPane;
	});

	beforeEach(async function () {
		await selectLibrary(win);
		tabs.closeAll();
		contextPane.collapsed = true;
		Zotero.Pharos.Chat._clearCache();
	});

	after(async function () {
		tabs.select('zotero-pane');
		tabs.closeAll();
		win.close();
	});

	async function openPDF(attachment) {
		await zp.viewItems([attachment]);
		let reader = Zotero.Reader.getByTabID(tabs.selectedID);
		await reader._initPromise;
		await waitForCallback(() => (
			reader._iframe.contentDocument
				?.querySelector('[data-pharos-reader-chat="true"]')
		), 50, 10);
		return reader;
	}

	function getDetails(reader) {
		return contextPane.context._getItemContext(reader.tabID);
	}

	async function waitForAutoOpen(reader) {
		await waitForCallback(() => {
			let details = getDetails(reader);
			return details?.dataset.pharosChatAutoOpened == 'true';
		}, 50, 10);
		return getDetails(reader);
	}

	it("should reveal AI chat when a paper opens", async function () {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		let reader = await openPDF(attachment);

		await waitForCallback(() => !contextPane.collapsed, 50, 10);
		let details = contextPane.context._getItemContext(reader.tabID);
		let chat = details.getEnabledPane('pharos-chat');

		assert.ok(chat, "the paper has an AI chat section");
		assert.equal(contextPane.context.mode, 'item');
		assert.isTrue(chat.open, "the section body is visible, not only its heading");
		assert.isFalse(chat.collapsible,
			"the reader chat is an application surface, not a collapsible item section");
		assert.equal(details.primaryPane, 'pharos-chat');
		assert.equal(details.dataset.pharosChatAutoOpened, 'true');
	});

	it("should make AI chat fill the reader's right-hand pane", async function () {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		let reader = await openPDF(attachment);
		let details = await waitForAutoOpen(reader);
		await waitForCallback(() => !contextPane.collapsed, 50, 10);

		let header = details.querySelector('#zotero-item-pane-header');
		let info = details.getEnabledPane('info');
		let chat = details.getEnabledPane('pharos-chat');
		let messages = chat.querySelector('.pharos-chat-messages');
		let composer = chat.querySelector('.pharos-chat-composer');
		let surface = details.querySelector('.zotero-view-item');

		assert.equal(win.getComputedStyle(header).display, 'none');
		assert.equal(win.getComputedStyle(info).display, 'none');
		assert.notEqual(win.getComputedStyle(chat).display, 'none');
		assert.equal(win.getComputedStyle(messages).maxHeight, 'none');
		assert.isAtMost(
			composer.getBoundingClientRect().bottom,
			surface.getBoundingClientRect().bottom + 1,
			"the composer stays inside the visible pane rather than below metadata"
		);
	});

	it("should let the reader sidenav leave and return to full-height chat", async function () {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		let reader = await openPDF(attachment);
		let details = await waitForAutoOpen(reader);
		let chat = details.getEnabledPane('pharos-chat');
		let messages = chat.querySelector('.pharos-chat-messages');
		let marker = win.document.createElement('div');
		messages.append(marker);

		await details.scrollToPane('info', 'instant');
		assert.equal(details.primaryPane, '');
		assert.isTrue(chat.collapsible);
		assert.notEqual(
			win.getComputedStyle(details.querySelector('#zotero-item-pane-header')).display,
			'none'
		);

		await details.scrollToPane('pharos-chat', 'instant');
		assert.equal(details.primaryPane, 'pharos-chat');
		assert.isFalse(chat.collapsible);
		assert.isTrue(messages.contains(marker),
			"switching panes must not replace the conversation element");
	});

	it("should not turn the library item pane into a chat-only surface", async function () {
		let item = await createDataObject('item');
		await importPDFAttachment(item);
		tabs.select('zotero-pane');
		await zp.selectItem(item.id);

		let details = zp.itemPane._itemDetails;
		assert.equal(details.tabType, 'library');
		assert.equal(details.primaryPane, '');
		assert.notEqual(
			win.getComputedStyle(details.querySelector('#zotero-item-pane-header')).display,
			'none'
		);
		assert.notEqual(win.getComputedStyle(details.getEnabledPane('info')).display, 'none');
	});

	it("should keep an explicit AI chat button in the reader toolbar", async function () {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		let reader = await openPDF(attachment);
		let button = reader._iframe.contentDocument
			.querySelector('[data-pharos-reader-chat="true"]');

		assert.equal(button.textContent.trim(), Zotero.getString('pane-pharos-chat'));
		contextPane.collapsed = true;
		button.click();
		await waitForCallback(() => !contextPane.collapsed, 50, 10);

		let details = contextPane.context._getItemContext(reader.tabID);
		assert.isTrue(details.getEnabledPane('pharos-chat').open);
	});

	it("should prepare the PDF attachment that is actually open", async function () {
		let item = await createDataObject('item');
		let firstAttachment = await importPDFAttachment(item);
		let openAttachment = await importPDFAttachment(item);
		assert.notEqual(firstAttachment.id, openAttachment.id);

		let ChatBox = win.customElements.get('pharos-chat-box');
		let prepare = sinon.stub(ChatBox.prototype, 'prepare').resolves(true);
		try {
			let reader = await openPDF(openAttachment);
			await waitForCallback(() => prepare.called, 10, 5);

			assert.isTrue(prepare.calledOnce);
			assert.strictEqual(prepare.firstCall.args[0], openAttachment,
				"the parent item's first PDF must not replace the open Reader attachment");
			assert.strictEqual(reader._item, openAttachment);
		}
		finally {
			prepare.restore();
		}
	});

	it("should not reveal or focus a stale reader after switching tabs", async function () {
		let firstItem = await createDataObject('item');
		let firstAttachment = await importPDFAttachment(firstItem);
		let firstReader = await openPDF(firstAttachment);
		let firstDetails = await waitForAutoOpen(firstReader);

		let secondItem = await createDataObject('item');
		let secondAttachment = await importPDFAttachment(secondItem);
		let secondReader = await openPDF(secondAttachment);
		let secondDetails = await waitForAutoOpen(secondReader);

		tabs.select(firstReader.tabID);
		await waitForCallback(
			() => contextPane.context._itemPaneDeck.selectedPanel === firstDetails,
			50, 5
		);
		contextPane.collapsed = true;

		let pane = firstDetails.getEnabledPane('pharos-chat');
		let input = pane.querySelector('.pharos-chat-input');
		let blockedScroll = Zotero.Promise.defer();
		let scroll = sinon.stub(firstDetails, 'scrollToPane').resolves(true);
		scroll.onFirstCall().returns(blockedScroll.promise);
		let prepare = sinon.stub(pane, 'prepare').resolves(true);
		let focus = sinon.spy(input, 'focus');
		let opening;
		try {
			opening = Zotero.Pharos.ReaderChat.open(firstReader, { focus: true });
			await waitForCallback(() => scroll.calledOnce, 10, 5);

			tabs.select(secondReader.tabID);
			await waitForCallback(
				() => contextPane.context._itemPaneDeck.selectedPanel === secondDetails,
				50, 5
			);
			blockedScroll.resolve(null);

			assert.isFalse(await opening);
			assert.equal(tabs.selectedID, secondReader.tabID);
			assert.isTrue(contextPane.collapsed,
				"stale work must not open the shared pane for the newly selected tab");
			assert.isFalse(prepare.called);
			assert.isFalse(focus.called);
		}
		finally {
			blockedScroll.resolve(null);
			if (opening) {
				await opening.catch(() => {});
			}
			focus.restore();
			prepare.restore();
			scroll.restore();
		}
	});

	it("should finish rendering and scrolling before focusing after a toolbar click",
		async function () {
			let item = await createDataObject('item');
			let attachment = await importPDFAttachment(item);
			let reader = await openPDF(attachment);
			let details = await waitForAutoOpen(reader);
			let pane = details.getEnabledPane('pharos-chat');
			let input = pane.querySelector('.pharos-chat-input');
			let button = reader._iframe.contentDocument
				.querySelector('[data-pharos-reader-chat="true"]');

			contextPane.collapsed = true;
			pane.open = false;
			input.disabled = false;
			let postExpandScroll = Zotero.Promise.defer();
			let originalScroll = details.scrollToPane.bind(details);
			let scroll = sinon.stub(details, 'scrollToPane').callsFake((...args) => {
				// Expanding ItemDetails can start its own scroll at the same time as
				// ReaderChat's final scroll. Hold every post-expand caller on the same
				// barrier instead of relying on a fragile call number.
				return contextPane.collapsed
					? originalScroll(...args)
					: postExpandScroll.promise;
			});
			let prepare = sinon.stub(pane, 'prepare').resolves(true);
			let focus = sinon.spy(input, 'focus');
			let originalOpen = Zotero.Pharos.ReaderChat.open;
			let opening;
			let openCalled = Zotero.Promise.defer();
			let open = sinon.stub(Zotero.Pharos.ReaderChat, 'open').callsFake((...args) => {
				opening = originalOpen.apply(Zotero.Pharos.ReaderChat, args);
				openCalled.resolve();
				return opening;
			});
			try {
				button.click();
				await openCalled.promise;
				await waitForCallback(
					() => scroll.callCount >= 2 && !contextPane.collapsed,
					10, 5
				);

				assert.isFalse(focus.called,
					"the composer must not steal focus while its final scroll is pending");
				assert.isFalse(prepare.called,
					"paper preparation also starts only after the reader surface settles");
				postExpandScroll.resolve(true);
				assert.isTrue(await opening);

				assert.isTrue(focus.called);
				assert.isTrue(prepare.calledOnce);
				assert.strictEqual(details.ownerDocument.activeElement, input);
				assert.isTrue(focus.calledBefore(prepare));
				assert.strictEqual(prepare.firstCall.args[0], attachment);
			}
			finally {
				postExpandScroll.resolve(true);
				if (opening) {
					await opening.catch(() => {});
				}
				open.restore();
				focus.restore();
				prepare.restore();
				scroll.restore();
			}
		}
	);

	it("should respect a manual collapse when returning to the same paper", async function () {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		let reader = await openPDF(attachment);
		await waitForCallback(() => !contextPane.collapsed, 50, 10);

		contextPane.collapsed = true;
		tabs.select('zotero-pane');
		tabs.select(reader.tabID);
		await Zotero.Promise.delay(50);

		assert.isTrue(contextPane.collapsed);
	});

	it("should support PDFs linked from outside Zotero storage", async function () {
		let item = await createDataObject('item');
		let file = getTestDataDirectory();
		file.append('test.pdf');
		let attachment = await Zotero.Attachments.linkFromFile({
			file,
			parentItemID: item.id,
		});
		let reader = await openPDF(attachment);
		await waitForCallback(() => !contextPane.collapsed, 50, 10);

		let details = contextPane.context._getItemContext(reader.tabID);
		assert.ok(details.getEnabledPane('pharos-chat'));
	});
});
