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

	/**
	 * Sign in and say what model the account has.
	 *
	 * Every section test that gets as far as _restore() needs both: the section
	 * asks for the provider on selection so that a missing model is reported
	 * before the composer is used, and an unstubbed lookup would go to the
	 * network.
	 */
	function signedInWith(provider) {
		stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
		return stub(Zotero.Pharos.Chat, 'getProvider').resolves(provider);
	}

	/** A stream that emits one fragment and then waits to be aborted. */
	function stubHangingStream(firstDelta) {
		return stub(Zotero.Pharos.Chat, 'sendMessage')
			.callsFake((conversationID, message, { onDelta, signal }) => {
				if (onDelta) {
					onDelta(firstDelta);
				}
				return new Promise((resolve, reject) => {
					signal.addEventListener('abort', () => {
						// What Zotero.Pharos.API.stream() propagates from fetch.
						let error = new Error('aborted');
						error.name = 'AbortError';
						reject(error);
					});
				});
			});
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

	describe("#listConversations()", function () {
		it("should keep the backend's newest-first order", async function () {
			captureRequests([{ id: 'newest' }, { id: 'older' }]);
			var conversations = await Zotero.Pharos.Chat.listConversations('paper-1');
			assert.deepEqual(conversations.map(c => c.id), ['newest', 'older']);
		});

		it("should return an array for an empty body", async function () {
			// request() answers null for a 200 with nothing in it, and every
			// caller here iterates the result.
			captureRequests(null);
			assert.deepEqual(await Zotero.Pharos.Chat.listConversations('paper-1'), []);
		});
	});

	describe("#createConversation()", function () {
		it("should POST without inventing a title", async function () {
			// The backend names a conversation after its first question. A title
			// sent from here would be a worse one that then stuck.
			captureRequests({ id: 'created', title: '论文对话' });
			var conversation = await Zotero.Pharos.Chat.createConversation('paper-1');
			assert.equal(conversation.id, 'created');
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/ai/papers/paper-1/conversations');
			assert.deepEqual(requests[0].options.body, {});
		});
	});

	describe("#deleteConversation()", function () {
		it("should delete the conversation and nothing else", async function () {
			// The confirmation promises that the paper's index survives. That is
			// true of the backend -- PaperAiContext is a separate table keyed by
			// (user, paper) and delete_conversation() never touches it -- and it
			// stays true only while this client sends exactly one request.
			captureRequests(null);
			await Zotero.Pharos.Chat.deleteConversation('conversation-1');
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'DELETE');
			assert.equal(requests[0].path, '/api/ai/conversations/conversation-1');
		});
	});

	describe("#getProvider()", function () {
		it("should ask once per session", async function () {
			// Asked on every item switch. Without the cache, arrowing down a
			// list of papers is one request per row.
			captureRequests({ configured: true, model: 'gpt-4o-mini' });
			assert.isTrue((await Zotero.Pharos.Chat.getProvider()).configured);
			assert.isTrue((await Zotero.Pharos.Chat.getProvider()).configured);
			assert.lengthOf(requests, 1);
		});

		it("should ask again when asked to refresh", async function () {
			// A model configured in the web app mid-session has to be reachable
			// without restarting the client.
			var configured = false;
			captureRequests(() => ({ configured: (configured = !configured) }));
			assert.isTrue((await Zotero.Pharos.Chat.getProvider()).configured);
			assert.isFalse((await Zotero.Pharos.Chat.getProvider({ refresh: true })).configured);
			assert.lengthOf(requests, 2);
		});

		it("should not remember a failure", async function () {
			// A cached rejection would be handed to every later caller, forever,
			// including after the server came back.
			var fail = true;
			captureRequests(() => {
				if (fail) {
					throw new Error('boom');
				}
				return { configured: true };
			});
			assert.instanceOf(await getPromiseError(Zotero.Pharos.Chat.getProvider()), Error);
			fail = false;
			assert.isTrue((await Zotero.Pharos.Chat.getProvider()).configured);
		});
	});

	describe("#getContext()", function () {
		it("should read without preparing", async function () {
			// The whole of the section's laziness rests on this being a GET:
			// ensureContext() would start an extraction, and reporting state
			// must never do that.
			captureRequests({ status: 'ready', charCount: 1234, hasSummary: true });
			var context = await Zotero.Pharos.Chat.getContext('paper-1');
			assert.equal(context.charCount, 1234);
			assert.deepEqual(requests.map(r => r.method), ['GET']);
			assert.equal(requests[0].path, '/api/ai/papers/paper-1/context');
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

			signedInWith({ configured: true });
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);
			stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
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
			signedInWith({ configured: true });
			// Only the first paper has been resolved this session, so only it
			// can restore -- which is also what stops the second item's own
			// load from standing in for the leak this is looking for.
			stub(Zotero.Pharos.Chat, 'getKnownPaperID')
				.callsFake(a => (a && a.id == firstPDF.id ? 'paper-1' : null));
			stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);
			stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
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
			//
			// The provider lookup that does happen here is the deliberate
			// exception: it reads the account's configuration rather than the
			// paper, and it is the only way a missing model can be reported
			// before a question has been written and lost.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			captureRequests({ configured: true });

			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			await Zotero.Promise.delay(50);

			assert.deepEqual(
				requests.map(r => `${r.method} ${r.path}`),
				['GET /api/ai/provider']
			);
		});

		it("should stay answerable when the thread cannot be loaded", async function () {
			// Scrollback is a convenience; asking is the feature. A load that
			// fails must cost the scrollback only.
			var item = await createDataObject('item');
			await importPDFAttachment(item);

			signedInWith({ configured: true });
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);
			stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
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

			signedInWith({ configured: true });
			stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
			stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);
			stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
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

	describe("item pane section state", function () {
		/** @see the identical helper above; both describes drive the same box. */
		function switchTo(box, item) {
			box.item = null;
			box.item = item;
		}

		/** An item with a PDF, selected, with its section handed the item. */
		async function openBox() {
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			await zp.itemsView.selectItems([item.id]);
			var box = win.document.getElementById('zotero-editpane-pharos-chat');
			switchTo(box, item);
			return { box, item };
		}

		beforeEach(async function () {
			await selectLibrary(win);
			// The provider is account state, so the section deliberately keeps
			// it across item switches -- which in one long-lived window means
			// across tests too.
			win.document.getElementById('zotero-editpane-pharos-chat')._provider = null;
		});

		describe("no model configured", function () {
			it("should say so, and where to go, rather than wait for the stream to fail", async function () {
				// Before this, a missing model reached the user as whatever the
				// backend's 503 happened to say, in the same grey line the next
				// action overwrites -- and only after the question had been
				// written and thrown away.
				signedInWith({ configured: false, source: 'none' });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				var stream = stub(Zotero.Pharos.Chat, 'sendMessage').resolves('');

				var { box } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);

				assert.isFalse(box._notice.hidden, "the card is shown");
				assert.isTrue(box._input.disabled, "the composer is dead on purpose");
				assert.isTrue(box._send.disabled);

				box._input.value = 'what is the contribution?';
				await box.send();
				assert.isFalse(stream.called, "nothing is sent while the gate is up");
			});

			it("should not gate the composer while the lookup is still in flight", async function () {
				// null is "not asked yet", not "no model". Gating on it would
				// blank the composer for a round trip on every selection.
				stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				stub(Zotero.Pharos.Chat, 'getProvider').returns(new Promise(() => {}));

				var { box } = await openBox();
				await Zotero.Promise.delay(50);

				assert.isNull(box._provider);
				assert.isTrue(box._notice.hidden);
				assert.isFalse(box._input.disabled);
			});

			it("should lift the gate once a model is configured", async function () {
				signedInWith({ configured: true, source: 'server' });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);

				var { box } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);

				assert.isTrue(box._notice.hidden);
				assert.isFalse(box._input.disabled);
			});

			it("should show the sign-in card when there is no account", async function () {
				stub(Zotero.Pharos.API, 'hasCredentials').returns(false);
				var provider = stub(Zotero.Pharos.Chat, 'getProvider').resolves({ configured: true });

				var { box } = await openBox();
				await Zotero.Promise.delay(50);

				assert.equal(box._gate(), 'signed-out');
				assert.isFalse(box._notice.hidden);
				assert.isTrue(box._input.disabled);
				assert.isFalse(provider.called, "signed out, nothing is asked of the server");
			});
		});

		describe("resting state", function () {
			it("should report a paper it already understands without preparing one", async function () {
				// The laziness is deliberate: resolving a paper uploads it. What
				// this adds is the report, and the report must not become the
				// preparation.
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
				stub(Zotero.Pharos.Chat, 'listConversations').resolves([]);
				var ensure = stub(Zotero.Pharos.Chat, 'ensureContext').resolves({});
				stub(Zotero.Pharos.Chat, 'getContext').resolves({
					status: 'ready',
					hasSummary: true,
					charCount: 41234,
				});

				var { box } = await openBox();
				await waitForCallback(() => box._phase == 'ready', 10, 5);

				assert.isFalse(ensure.called, "reporting must not prepare");
				assert.include(box._phaseChars.textContent, '41');
				assert.isFalse(box._phaseChars.hidden);
			});

			it("should say the paper is read on the first question when nothing is known", async function () {
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);

				var { box } = await openBox();
				await Zotero.Promise.delay(50);

				assert.equal(box._phase, 'unknown');
				assert.equal(
					box._phaseLabel.textContent,
					Zotero.getString('pharos-chat-phase-lazy')
				);
				assert.isTrue(box._phaseChars.hidden);
			});

			it("should keep painting when the item's data is not loaded", async function () {
				// getDisplayTitle() throws UnloadedDataException, and letting it
				// out abandons the rest of the pass -- every region painted after
				// the empty state keeps whatever it last showed, including a
				// delete left live against a conversation that is no longer the
				// current one. Nothing throws; the section simply stops agreeing
				// with itself.
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);

				var { box, item } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);
				stub(item, 'getDisplayTitle').throws(
					new Zotero.Exception.UnloadedDataException('not loaded', 'itemData')
				);

				box._render();

				assert.isTrue(box._emptyPaper.hidden);
				assert.isFalse(box._input.disabled, "and the rest of the pass still ran");
				assert.isTrue(box._menuDelete.disabled);
			});

			it("should offer the starter questions while the thread is empty", async function () {
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);

				var { box, item } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);

				assert.isFalse(box._empty.hidden);
				assert.equal(box._starters.childElementCount, 4);
				assert.equal(box._emptyPaper.textContent, item.getDisplayTitle());

				// A chip's label is the question it asks. Pinning that they are
				// the same string is what stops one drifting from the other.
				var send = stub(box, 'send');
				box._starters.firstChild.click();
				assert.equal(send.firstCall.args[0], box._starters.firstChild.textContent);
			});
		});

		describe("stopping", function () {
			async function startStreaming() {
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				stub(Zotero.Pharos.Chat, 'resolvePaperID').resolves('paper-1');
				stub(Zotero.Pharos.Chat, 'ensureContext').resolves({
					status: 'ready', hasSummary: true, charCount: 10,
				});
				stub(Zotero.Pharos.Chat, 'getOrCreateConversation').resolves({ id: 'conversation-1' });
				stub(Zotero.Pharos.Chat, 'getMessages').resolves([]);
				stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);

				var { box } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);
				box._input.value = 'why does this work?';
				var sent = box.send();
				await waitForCallback(() => box._busy && Zotero.Pharos.Chat.sendMessage.called, 10, 5);
				return { box, sent };
			}

			it("should swap the send button for a stop button while streaming", async function () {
				stubHangingStream('half an ans');
				var { box, sent } = await startStreaming();

				assert.isTrue(box._send.hidden);
				assert.isFalse(box._stop.hidden);

				box.stop();
				await sent;
				assert.isFalse(box._stop.hidden === false, "the stop button goes with the stream");
				assert.isFalse(box._send.hidden);
			});

			it("should drop the partial answer the backend did not save", async function () {
				// stream_chat_events() returns on GeneratorExit without saving a
				// partial answer, so text left on screen is a turn the model does
				// not have and will not be given back on the next question.
				stubHangingStream('half an ans');
				var { box, sent } = await startStreaming();
				assert.equal(box._messages.childElementCount, 2, "question and a partial answer");

				box.stop();
				await sent;

				assert.equal(box._messages.childElementCount, 1);
				assert.isTrue(box._messages.firstChild.classList.contains('is-user'),
					"the question stays, because the backend kept it");
			});

			it("should not report a stop as a failure", async function () {
				// Whoever pressed the button knows why it stopped. An error
				// banner there reads as something having gone wrong.
				stubHangingStream('half an ans');
				var { box, sent } = await startStreaming();

				box.stop();
				await sent;

				assert.isTrue(box._banner.hidden, "not an error");
				assert.equal(box._status.textContent, Zotero.getString('pharos-chat-stopped'));
				assert.isFalse(box._busy);
				assert.isFalse(box._send.disabled, "the next question can be asked");
			});

			it("should stay silent when the abort came from switching items", async function () {
				// The same AbortError, but nobody asked for it and the box now
				// belongs to another paper.
				stubHangingStream('half an ans');
				var { box, sent } = await startStreaming();

				var other = await createDataObject('item');
				await importPDFAttachment(other);
				box.item = other;
				await sent;

				assert.equal(box._status.textContent, '');
				assert.isTrue(box._banner.hidden);
				assert.equal(box._messages.childElementCount, 0);
			});
		});

		describe("failures", function () {
			/**
			 * Ask one question and have the stream fail.
			 *
			 * `providerAfter`, when given, is what a *refreshed* lookup answers:
			 * the section only re-asks after a 503, and modelling that as a
			 * different answer to the refresh is what makes the test independent
			 * of when the refresh happens to run.
			 */
			async function failWith(error, providerAfter) {
				stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
				stub(Zotero.Pharos.Chat, 'getProvider').callsFake(({ refresh } = {}) =>
					Promise.resolve(refresh && providerAfter ? providerAfter : { configured: true }));
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				stub(Zotero.Pharos.Chat, 'resolvePaperID').resolves('paper-1');
				stub(Zotero.Pharos.Chat, 'ensureContext').resolves({ status: 'ready', hasSummary: true });
				stub(Zotero.Pharos.Chat, 'getOrCreateConversation').resolves({ id: 'conversation-1' });
				stub(Zotero.Pharos.Chat, 'getMessages').resolves([]);
				stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'conversation-1' }]);
				stub(Zotero.Pharos.Chat, 'sendMessage').rejects(error);

				var { box } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);
				box._input.value = 'why does this work?';
				await box.send();
				return box;
			}

			it("should keep a failure on screen until it is dismissed", async function () {
				// It used to go into the status line, which the next thing that
				// happens overwrites -- so the reason the answer failed was gone
				// by the time the question had been retyped.
				var box = await failWith(new Error('the model refused'));

				assert.isFalse(box._banner.hidden);
				assert.equal(box._bannerText.textContent, 'the model refused');

				// The next action must not erase it.
				box._setStatus('thinking about something else');
				assert.isFalse(box._banner.hidden);

				box.querySelector('.pharos-chat-banner-dismiss').click();
				assert.isTrue(box._banner.hidden);
			});

			it("should turn the backend's 503 into the card that says where to go", async function () {
				// A 503 from the stream is the model being unconfigured or
				// unusable. The sentence on its own leaves the user with
				// nowhere to click, in a line the next action erases.
				var error = new Error('请先在设置中配置 OpenAI 兼容模型。');
				error.status = 503;
				// The server that just refused says why when asked again.
				var box = await failWith(error, { configured: false, source: 'none' });
				await waitForCallback(
					() => box._provider && box._provider.configured === false, 10, 5
				);

				assert.isFalse(box._notice.hidden);
				assert.isTrue(box._input.disabled);
				assert.isFalse(box._banner.hidden, "and the server's own sentence is kept");
			});

			it("should clear the failure when the next question is asked", async function () {
				var box = await failWith(new Error('the model refused'));
				assert.isFalse(box._banner.hidden);

				Zotero.Pharos.Chat.sendMessage
					.callsFake((id, message, { onDelta }) => {
						onDelta('an answer');
						return Promise.resolve('an answer');
					});
				box._input.value = 'try again';
				await box.send();

				assert.isTrue(box._banner.hidden);
				assert.equal(box._messages.lastChild.textContent, 'an answer');
			});
		});

		describe("conversations", function () {
			async function openWithConversations(conversations) {
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns('paper-1');
				stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
				stub(Zotero.Pharos.Chat, 'listConversations').resolves(conversations);
				stub(Zotero.Pharos.Chat, 'getMessages').resolves([]);

				var { box } = await openBox();
				await waitForCallback(
					() => box._conversations.length == conversations.length, 10, 5
				);
				return box;
			}

			it("should name the thread even when it is the only one", async function () {
				// Matching the web client. The picker is a label as much as a
				// control: conversations are created implicitly and outlive the
				// window, so at one entry it still answers "which thread am I
				// in?". Hiding it below two also meant the row appeared out of
				// nowhere and pushed the composer down the moment a second
				// conversation existed.
				var box = await openWithConversations([{ id: 'c1', title: '只有一个' }]);
				assert.isFalse(box._sessions.hidden);
				assert.equal(box._sessionSelect.value, 'c1');
				assert.isFalse(box._newButton.hidden, "and the paper can take another thread");
			});

			it("should hide the picker when the paper has no thread at all", async function () {
				var box = await openWithConversations([]);
				assert.isTrue(box._sessions.hidden);
			});

			it("should list every thread newest first and select the resumed one", async function () {
				var box = await openWithConversations([
					{ id: 'c1', title: 'newest question' },
					{ id: 'c2', title: 'older question' },
				]);

				assert.isFalse(box._sessions.hidden);
				assert.deepEqual(
					Array.from(box._sessionSelect.options).map(o => o.value),
					['c1', 'c2']
				);
				assert.equal(box._sessionSelect.value, 'c1');
				assert.equal(box._conversationID, 'c1');
			});

			it("should repaint the thread when another is chosen", async function () {
				var box = await openWithConversations([
					{ id: 'c1', title: 'newest' },
					{ id: 'c2', title: 'older' },
				]);
				Zotero.Pharos.Chat.getMessages.resolves([
					{ role: 'user', content: 'from the older thread' },
				]);

				await box._selectConversation('c2');

				assert.equal(box._conversationID, 'c2');
				assert.equal(box._messages.childElementCount, 1);
				assert.equal(box._messages.firstChild.textContent, 'from the older thread');
			});

			it("should start an empty thread without fetching one", async function () {
				var box = await openWithConversations([{ id: 'c1', title: 'existing' }]);
				stub(Zotero.Pharos.Chat, 'createConversation').resolves({ id: 'c2', title: '论文对话' });
				box._addMessage('user', 'from the old thread');

				await box._newConversation();

				assert.equal(box._conversationID, 'c2');
				assert.equal(box._messages.childElementCount, 0);
				assert.isTrue(box._historyLoaded,
					"a conversation created here has nothing stored to go looking for");
			});

			it("should hide conversation management until the paper has an id", async function () {
				// Listing conversations is keyed by the paper, and getting that
				// id costs an upload. Until one has been paid for there is
				// nothing to list, create against, or delete.
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				var list = stub(Zotero.Pharos.Chat, 'listConversations').resolves([]);

				var { box } = await openBox();
				await Zotero.Promise.delay(50);

				assert.isFalse(list.called);
				assert.isTrue(box._newButton.hidden);
				assert.isTrue(box._moreButton.hidden);
			});

			it("should not paint one paper's conversation list into another's picker", async function () {
				// Same race as the thread restore, one round trip further out:
				// the list arrives after the box has moved on, and a picker
				// offering the previous paper's threads is not distinguishable
				// from one that belongs here.
				var first = await createDataObject('item');
				var firstPDF = await importPDFAttachment(first);
				var second = await createDataObject('item');
				await importPDFAttachment(second);

				var release;
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID')
					.callsFake(a => (a && a.id == firstPDF.id ? 'paper-1' : null));
				stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
				stub(Zotero.Pharos.Chat, 'getMessages').resolves([]);
				stub(Zotero.Pharos.Chat, 'listConversations')
					.returns(new Promise(resolve => (release = resolve)));

				var box = win.document.getElementById('zotero-editpane-pharos-chat');
				switchTo(box, first);
				await waitForCallback(() => Zotero.Pharos.Chat.listConversations.called, 10, 5);

				box.item = second;
				release([{ id: 'c1', title: 'about the first paper' }, { id: 'c2', title: 'also' }]);
				await Zotero.Promise.delay(50);

				assert.lengthOf(box._conversations, 0);
				assert.isTrue(box._sessions.hidden);
				assert.isNull(box._conversationID);
			});

			it("should not adopt a paper id resolved for the item just left", async function () {
				// Resolving a paper is an upload, and Zotero.HTTP takes no abort
				// signal, so the first question's round trip carries on after the
				// user has moved on. Writing that id back would leave the new
				// paper's box holding the old paper's identity -- and the id is
				// what the *next* question gets asked about, so nothing on screen
				// would say anything was wrong.
				var first = await createDataObject('item');
				await importPDFAttachment(first);
				var second = await createDataObject('item');
				await importPDFAttachment(second);

				var release;
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				stub(Zotero.Pharos.Chat, 'getContext').resolves(null);
				stub(Zotero.Pharos.Chat, 'listConversations').resolves([]);
				stub(Zotero.Pharos.Chat, 'ensureContext').resolves({ status: 'ready', hasSummary: true });
				stub(Zotero.Pharos.Chat, 'getOrCreateConversation').resolves({ id: 'c1' });
				stub(Zotero.Pharos.Chat, 'sendMessage').resolves('');
				stub(Zotero.Pharos.Chat, 'resolvePaperID')
					.returns(new Promise(resolve => (release = resolve)));

				var box = win.document.getElementById('zotero-editpane-pharos-chat');
				switchTo(box, first);
				await waitForCallback(() => box._provider !== null, 10, 5);
				box._input.value = 'about the first paper';
				var sent = box.send();
				await waitForCallback(() => Zotero.Pharos.Chat.resolvePaperID.called, 10, 5);

				switchTo(box, second);
				release('paper-for-the-first');
				await sent;

				assert.isNull(box._paperID);
				assert.isNull(box._conversationID);
				assert.isFalse(Zotero.Pharos.Chat.getOrCreateConversation.called);
			});

			it("should ask before deleting, and delete only the conversation", async function () {
				var box = await openWithConversations([
					{ id: 'c1', title: 'newest' },
					{ id: 'c2', title: 'older' },
				]);
				var remove = stub(Zotero.Pharos.Chat, 'deleteConversation').resolves(null);

				// Two steps to a destructive action: the header button opens the
				// actions, and the action asks.
				box._moreButton.doCommand();
				assert.isFalse(box._menu.hidden);
				box._menuDelete.click();
				assert.isFalse(box._confirm.hidden);
				assert.isTrue(box._menu.hidden, "and the actions close behind it");
				assert.isFalse(remove.called);

				box.querySelector('.pharos-chat-confirm-go').click();
				await waitForCallback(() => remove.called, 10, 5);
				await Zotero.Promise.delay(20);

				assert.deepEqual(remove.firstCall.args, ['c1']);
				assert.deepEqual(box._conversations.map(c => c.id), ['c2'],
					"and falls back to the newest thread left");
				assert.equal(box._conversationID, 'c2');
				assert.isTrue(box._confirm.hidden);
			});

			it("should let the confirmation be cancelled", async function () {
				var box = await openWithConversations([{ id: 'c1', title: 'newest' }]);
				var remove = stub(Zotero.Pharos.Chat, 'deleteConversation').resolves(null);

				box._moreButton.doCommand();
				box._menuDelete.click();
				assert.isFalse(box._confirm.hidden);
				box.querySelector('.pharos-chat-confirm-cancel').click();

				assert.isTrue(box._confirm.hidden);
				assert.isFalse(remove.called);
				assert.equal(box._conversationID, 'c1');
			});

			it("should refuse to delete a thread that is still generating", async function () {
				// The backend answers 409 for this. Shutting the control off is
				// what stops the user meeting that as an error.
				stubHangingStream('half an ans');
				signedInWith({ configured: true });
				stub(Zotero.Pharos.Chat, 'getKnownPaperID').returns(null);
				stub(Zotero.Pharos.Chat, 'resolvePaperID').resolves('paper-1');
				stub(Zotero.Pharos.Chat, 'ensureContext').resolves({ status: 'ready', hasSummary: true });
				stub(Zotero.Pharos.Chat, 'getOrCreateConversation').resolves({ id: 'c1' });
				stub(Zotero.Pharos.Chat, 'getMessages').resolves([]);
				stub(Zotero.Pharos.Chat, 'listConversations').resolves([{ id: 'c1', title: 'a' }]);
				var remove = stub(Zotero.Pharos.Chat, 'deleteConversation').resolves(null);

				var { box } = await openBox();
				await waitForCallback(() => box._provider !== null, 10, 5);
				box._input.value = 'why does this work?';
				var sent = box.send();
				await waitForCallback(() => box._busy, 10, 5);

				assert.isTrue(box._menuDelete.disabled);
				await box._deleteConversation();
				assert.isFalse(remove.called);

				box.stop();
				await sent;
				assert.isFalse(box._menuDelete.disabled);
			});
		});
	});
});
