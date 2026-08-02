describe("Zotero.Pharos.Chat", function () {
	var win, zp, origBaseURL;
	var stubs = [];
	var requests, origRequest;

	/**
	 * Stand in for the backend. `responder` is either a canned body or a
	 * function of (method, path, options) whose return value resolves.
	 */
	function captureRequests(responder) {
		requests = [];
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			if (typeof responder == 'function') {
				return Promise.resolve().then(() => responder(method, path, options));
			}
			return Promise.resolve(responder === undefined ? null : responder);
		};
	}

	function restoreRequests() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	}

	function stub(object, method) {
		var s = sinon.stub(object, method);
		stubs.push(s);
		return s;
	}

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	beforeEach(function () {
		Zotero.Pharos.Chat._clearCache();
	});

	afterEach(function () {
		restoreRequests();
		while (stubs.length) {
			stubs.pop().restore();
		}
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

	describe("#getLatestConversation()", function () {
		it("should return the newest without creating one", async function () {
			// The list comes back newest-first and the backend appends to
			// whichever conversation is handed back, so picking the wrong end
			// resumes a thread the user abandoned.
			captureRequests([{ id: 'newest' }, { id: 'older' }]);
			var conversation = await Zotero.Pharos.Chat.getLatestConversation('paper-1');
			assert.equal(conversation.id, 'newest');
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'GET');
		});

		it("should return null rather than create when there is none", async function () {
			// This one runs merely because an item was selected. A POST here
			// would write a conversation row per paper the user clicked past.
			captureRequests([]);
			assert.isNull(await Zotero.Pharos.Chat.getLatestConversation('paper-1'));
			assert.lengthOf(requests, 1);
		});
	});

	describe("#getOrCreateConversation()", function () {
		it("should create only when the paper has none", async function () {
			captureRequests((method) => (method == 'GET' ? [] : { id: 'created' }));
			var conversation = await Zotero.Pharos.Chat.getOrCreateConversation('paper-1');
			assert.equal(conversation.id, 'created');
			assert.deepEqual(requests.map(r => r.method), ['GET', 'POST']);
		});

		it("should reuse an existing one", async function () {
			captureRequests([{ id: 'existing' }]);
			var conversation = await Zotero.Pharos.Chat.getOrCreateConversation('paper-1');
			assert.equal(conversation.id, 'existing');
			assert.deepEqual(requests.map(r => r.method), ['GET']);
		});
	});

	describe("#getMessages()", function () {
		it("should keep the backend's order and roles", async function () {
			// Nothing else pins this: a reversed or re-sorted thread still
			// renders, still scrolls, and simply reads as though the model
			// answered before it was asked.
			captureRequests({
				id: 'conversation-1',
				messages: [
					{ role: 'user', content: 'first question' },
					{ role: 'assistant', content: 'first answer' },
					{ role: 'user', content: 'second question' },
				],
			});
			var messages = await Zotero.Pharos.Chat.getMessages('conversation-1');
			assert.deepEqual(
				messages,
				[
					{ role: 'user', content: 'first question' },
					{ role: 'assistant', content: 'first answer' },
					{ role: 'user', content: 'second question' },
				]
			);
		});

		it("should tolerate a conversation with no messages", async function () {
			captureRequests({ id: 'conversation-1' });
			assert.deepEqual(await Zotero.Pharos.Chat.getMessages('conversation-1'), []);
		});

		it("should tolerate an empty body", async function () {
			// request() returns null for a 200 with nothing in it.
			captureRequests(null);
			assert.deepEqual(await Zotero.Pharos.Chat.getMessages('conversation-1'), []);
		});

		it("should drop rows it cannot render", async function () {
			captureRequests({
				messages: [
					{ role: 'user', content: 'kept' },
					{ role: 'assistant', content: '' },
					{ role: 'system', content: 'not a bubble' },
					null,
				],
			});
			assert.deepEqual(
				await Zotero.Pharos.Chat.getMessages('conversation-1'),
				[{ role: 'user', content: 'kept' }]
			);
		});

		it("should trim a long thread from the old end", async function () {
			// The backend hands the model the newest turns that fit in 48,000
			// characters and drops the rest, so those are the ones worth
			// showing. Trimming the other end would leave the box holding
			// exactly what the model has forgotten.
			var messages = [];
			for (let i = 0; i < 20; i++) {
				messages.push({ role: 'user', content: `${i}:${'x'.repeat(5000)}` });
			}
			captureRequests({ messages });
			var kept = await Zotero.Pharos.Chat.getMessages('conversation-1');
			assert.isBelow(kept.length, messages.length);
			assert.isAtMost(
				kept.reduce((sum, m) => sum + m.content.length, 0),
				48000
			);
			// The last stored turn is the one the next answer follows from.
			assert.equal(kept[kept.length - 1].content, messages[messages.length - 1].content);
		});

		it("should keep the newest turn however long it is", async function () {
			// A single answer can exceed the whole window. Dropping it would
			// render an empty box for a conversation that plainly has content.
			captureRequests({
				messages: [{ role: 'assistant', content: 'y'.repeat(60000) }],
			});
			assert.lengthOf(await Zotero.Pharos.Chat.getMessages('conversation-1'), 1);
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
		/**
		 * Hand the section an item the way switching to it does.
		 *
		 * Creating an item selects it, so the pane has usually already set the
		 * same item on the section before the test body runs -- and the setter
		 * deliberately treats a repeat of the same item as a re-render rather
		 * than a switch, since that is what an edit to the item looks like.
		 * Going through null is what makes this one a switch.
		 */
		function switchTo(box, item) {
			box.item = null;
			box.item = item;
		}

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

		it("should keep the thread when the same item is set again", async function () {
			// The item pane re-runs the setter on every render pass, including
			// the one that follows any edit to the item. Treating that as a
			// switch wipes a live thread because a tag was added -- and now
			// refetches it from the server each time as well.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			box._conversationID = 'conversation-1';
			box._addMessage('user', 'still here?');

			box.item = item;

			assert.equal(box._conversationID, 'conversation-1');
			assert.equal(box._messages.childElementCount, 1);
		});

		it("should restore the thread the backend has been appending to", async function () {
			// getOrCreateConversation() resumes whatever conversation the paper
			// already had, and the backend keeps adding to it. Without this the
			// box is blank while the model answers with the whole thread in
			// context -- which throws nothing and looks like a good answer.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);

			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'getLatestConversation').resolves({ id: 'conversation-1' });
			stub(Zotero.Pharos.Chat, 'getMessages').resolves([
				{ role: 'user', content: 'what is the contribution?' },
				{ role: 'assistant', content: 'a new sampler' },
			]);

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			await waitForCallback(() => box._messages.childElementCount == 2, 10, 5);

			assert.equal(box._conversationID, 'conversation-1');
			assert.equal(
				Zotero.Pharos.Chat.getKnownPaperID.firstCall.args[0].id,
				attachment.id,
				"resolves the thread for this item's own PDF"
			);
			var bubbles = box._messages.children;
			assert.isTrue(bubbles[0].classList.contains('is-user'));
			assert.equal(bubbles[0].textContent, 'what is the contribution?');
			assert.isTrue(bubbles[1].classList.contains('is-assistant'));
			assert.equal(bubbles[1].textContent, 'a new sampler');
		});

		it("should not paint one paper's thread into another paper's box", async function () {
			// The load is a round trip and item changes are a keypress apart,
			// so the answer can arrive after the box has moved on. Nothing
			// throws when it does; the previous paper's thread simply appears
			// under the new one, and it reads as though it belongs there.
			var first = await createDataObject('item');
			var firstPDF = await importPDFAttachment(first);
			var second = await createDataObject('item');
			await importPDFAttachment(second);

			var release;
			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			// Only the first paper has been resolved this session, so only it
			// can restore -- which is also what stops the second item's own
			// load from standing in for the leak this is looking for.
			stub(Zotero.Pharos.Chat, 'getKnownPaperID')
				.callsFake(a => (a && a.id == firstPDF.id ? 'paper-1' : null));
			stub(Zotero.Pharos.Chat, 'getLatestConversation').resolves({ id: 'conversation-1' });
			stub(Zotero.Pharos.Chat, 'getMessages')
				.returns(new Promise(resolve => (release = resolve)));

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, first);
			await waitForCallback(() => Zotero.Pharos.Chat.getMessages.called, 10, 5);

			box.item = second;
			release([{ role: 'user', content: 'about the first paper' }]);
			await Zotero.Promise.delay(50);

			assert.equal(box._messages.childElementCount, 0);
		});

		it("should not resolve the paper merely because an item was selected", async function () {
			// Resolving a paper uploads its file. Doing that on selection would
			// upload the library one arrow key at a time, which is why the
			// section stays inert until a question is asked.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			captureRequests({});

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			await Zotero.Promise.delay(50);

			assert.lengthOf(requests, 0);
		});

		it("should stay answerable when the thread cannot be loaded", async function () {
			// Scrollback is a convenience; asking is the feature. A load that
			// fails must cost the scrollback only.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'getLatestConversation').resolves({ id: 'conversation-1' });
			stub(Zotero.Pharos.Chat, 'getMessages').rejects(new Error('boom'));

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			await waitForCallback(() => Zotero.Pharos.Chat.getMessages.called, 10, 5);
			await Zotero.Promise.delay(50);

			assert.equal(box._conversationID, 'conversation-1', "the thread still resumes");
			assert.isFalse(box._historyLoaded, "a failed load leaves a retry open");
			assert.isFalse(box._send.disabled, "a new question can still be sent");
			assert.isFalse(box._busy);
		});

		it("should paint the stored thread only once", async function () {
			// _restore() and send() both reach the same conversation, and a
			// second pass would duplicate every turn already on screen.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'getLatestConversation').resolves({ id: 'conversation-1' });
			stub(Zotero.Pharos.Chat, 'getMessages').resolves([
				{ role: 'user', content: 'only once' },
			]);

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			await waitForCallback(() => box._messages.childElementCount == 1, 10, 5);

			await box._renderHistory('conversation-1', box._generation);

			assert.equal(box._messages.childElementCount, 1);
			assert.equal(Zotero.Pharos.Chat.getMessages.callCount, 1);
		});
	});
});
