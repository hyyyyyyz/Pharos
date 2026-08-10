describe("Zotero.Pharos.Translate", function () {
	var win, zp;
	var pdfBytes, origRequest;

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
		let file = getTestDataDirectory();
		file.append('test.pdf');
		pdfBytes = (await IOUtils.read(file.path)).buffer;
	});

	after(function () {
		win.close();
	});

	beforeEach(function () {
		// Job records outlive a run by design -- that is what makes a failure
		// still visible in the item pane afterwards -- so they have to be
		// cleared between tests or one test's failure is the next one's state.
		Zotero.Pharos.Translate._clearJobs();
	});

	afterEach(function () {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	});

	/**
	 * Only the network is stood in for. Everything downstream of it -- the temp
	 * file, the import, the relation, the queue row -- is the real thing.
	 *
	 * @param {Object|Function} job - what GET /api/jobs/{id} keeps answering, or
	 *     a function called for each poll
	 */
	function stubBackend(job) {
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = async function (method, path) {
			if (method == 'POST' && path == '/api/papers') {
				return { id: 'paper-1' };
			}
			if (method == 'POST' && /^\/api\/papers\/[^/]+\/translate$/.test(path)) {
				return { id: 'job-1' };
			}
			if (method == 'GET' && path.startsWith('/api/jobs/')) {
				return typeof job == 'function' ? job() : job;
			}
			if (method == 'GET' && /^\/api\/papers\/[^/]+\/pdf\/(mono|dual)$/.test(path)) {
				return pdfBytes;
			}
			throw new Error(`Unexpected request: ${method} ${path}`);
		};
	}

	function queueRow(itemID) {
		return Zotero.ProgressQueues.get('pharos-translate')
			.getRows().find(row => row.id == itemID);
	}

	function suffix(kind) {
		return Zotero.getString(`pharos-translate-suffix-${kind}`);
	}

	/** A paper with a PDF, translated once through the stubbed backend. */
	async function translatedPaper(mode = Zotero.Pharos.Translate.MODE_MONO, both = false) {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		stubBackend({ status: 'done', has_mono: true, has_dual: both });
		await Zotero.Pharos.Translate.translateItems([attachment], mode);
		Zotero.Pharos.API.request = origRequest;
		origRequest = null;
		Zotero.Pharos.Translate._clearJobs();
		return { item, attachment };
	}

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

	describe("reader toolbar", function () {
		async function openPDF(attachment) {
			await selectLibrary(win);
			await zp.viewItems([attachment]);
			let reader = Zotero.Reader.getByTabID(win.Zotero_Tabs.selectedID);
			await reader._initPromise;
			await waitForCallback(() => (
				reader._iframe.contentDocument
					?.querySelector('[data-pharos-reader-translate="true"]')
			), 50, 10);
			return reader;
		}

		afterEach(function () {
			win.Zotero_Tabs.select('zotero-pane');
			win.Zotero_Tabs.closeAll();
			Zotero.Prefs.clear('pharos.pdfTranslation');
		});

		it("should put the PDF translation button before Find", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			var reader = await openPDF(attachment);
			var doc = reader._iframe.contentDocument;
			var button = doc.querySelector('[data-pharos-reader-translate="true"]');
			var customSections = button.closest('.custom-sections');

			assert.ok(customSections, "the button is injected through CustomSections");
			assert.strictEqual(customSections.lastElementChild, button.parentElement,
				"translation is the rightmost custom action, directly beside Find");
			assert.strictEqual(customSections.nextElementSibling, doc.querySelector('.find'),
				"CustomSections remains immediately to the left of Find");
			assert.equal(button.getAttribute('aria-haspopup'), 'menu');
			assert.equal(button.title, Zotero.getString('pharos-translate-menu'));
		});

		it("should not add a button for something that cannot be translated",
			async function () {
				var attachment = await importFileAttachment('test.png');
				var append = sinon.spy();

				Zotero.Pharos.Translate._renderToolbar({
					reader: { _item: attachment, _openContextMenu() {} },
					doc: win.document,
					append,
				});

				assert.isFalse(append.called);
			}
		);

		it("should honour the account's whole-PDF translation switch", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			var append = sinon.spy();
			Zotero.Prefs.set('pharos.pdfTranslation', false);

			Zotero.Pharos.Translate._renderToolbar({
				reader: { _item: attachment, _openContextMenu() {} },
				doc: win.document,
				append,
			});

			assert.isFalse(append.called);
		});

		it("should offer and dispatch both translation modes", async function () {
			var item = await createDataObject('item');
			var firstAttachment = await importPDFAttachment(item);
			var attachment = await importPDFAttachment(item);
			assert.notEqual(firstAttachment.id, attachment.id);
			var openContextMenu = sinon.stub().resolves();
			var reader = {
				_item: attachment,
				_window: win,
				_openContextMenu: openContextMenu,
			};
			var button;
			Zotero.Pharos.Translate._renderToolbar({
				reader,
				doc: win.document,
				append: node => button = node,
			});

			button.click();
			assert.isTrue(openContextMenu.calledOnce);
			var menu = openContextMenu.firstCall.args[0];
			assert.deepEqual(menu.itemGroups[0].map(option => option.label), [
				Zotero.getString('pharos-translate-menu-mono'),
				Zotero.getString('pharos-translate-menu-dual'),
			]);

			var credentials = sinon.stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
			var translate = sinon.stub(Zotero.Pharos.Translate, 'translateItems').resolves();
			var dialog = { open: sinon.spy() };
			var getDialog = sinon.stub(
				Zotero.ProgressQueues.get('pharos-translate'), 'getDialog'
			).returns(dialog);
			try {
				menu.itemGroups[0][0].onCommand();
				menu.itemGroups[0][1].onCommand();
				await waitForCallback(() => translate.callCount == 2, 10, 5);

				assert.strictEqual(translate.firstCall.args[0][0], attachment);
				assert.equal(translate.firstCall.args[1], Zotero.Pharos.Translate.MODE_MONO);
				assert.strictEqual(translate.secondCall.args[0][0], attachment);
				assert.equal(translate.secondCall.args[1], Zotero.Pharos.Translate.MODE_DUAL);
				assert.isTrue(dialog.open.calledTwice);
			}
			finally {
				getDialog.restore();
				translate.restore();
				credentials.restore();
			}
		});
	});

	describe("#translateItems()", function () {
		// Only the network is stood in for. Everything downstream of it -- the
		// temp file, the import, the relation, the queue row -- is the real
		// thing, because that is where this module's failures are: none of them
		// throws, and all of them look like a translation that worked.

		it("should relate the translation and the original both ways", async function () {
			this.timeout(20000);
			// Zotero relations are written per item, so relating one side leaves
			// a link that exists in one direction only -- and neither direction
			// throws when it is missing. Without both, the translation is a file
			// with no route back to the paper it came from.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: false });

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			var translation = Zotero.Items.get(item.getAttachments())
				.find(a => a.id != attachment.id);
			assert.ok(translation, "the translation was imported");
			assert.include(attachment.relatedItems, translation.key);
			assert.include(translation.relatedItems, attachment.key);
		});

		it("should keep a top-level translation findable", async function () {
			this.timeout(20000);
			// A top-level PDF has no parent to hang the translation off, so it
			// lands as another top-level item. Left there it is unfiled in the
			// library root, with nothing tying it to the paper it translates.
			var collection = await createDataObject('collection');
			var attachment = await importPDFAttachment(null);
			attachment.setCollections([collection.id]);
			await attachment.saveTx();
			stubBackend({ status: 'done', has_mono: true, has_dual: false });

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			assert.lengthOf(attachment.relatedItems, 1);
			var translation = await Zotero.Items.getByLibraryAndKeyAsync(
				attachment.libraryID, attachment.relatedItems[0]
			);
			assert.include(translation.relatedItems, attachment.key);
			assert.deepEqual(
				translation.getCollections(),
				[collection.id],
				"filed where the original is filed"
			);
		});

		it("should keep both renderings when the job produced both", async function () {
			this.timeout(20000);
			// One job produces both, and the mode was picked from a context menu
			// before the user had read a word. Discarding the other means paying
			// for the whole run again to change your mind.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: true });

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			var titles = Zotero.Items.get(item.getAttachments())
				.filter(a => a.id != attachment.id)
				.map(a => a.getField('title'));
			assert.lengthOf(titles, 2);
			assert.isTrue(titles.some(t => t.includes(suffix('mono'))), 'mono kept');
			assert.isTrue(titles.some(t => t.includes(suffix('dual'))), 'dual kept');
			// The row still names the one that was asked for: the spare is a
			// bonus, not the result.
			assert.include(queueRow(attachment.id).message, suffix('mono'));
		});

		it("should fail when the rendering asked for was not produced", async function () {
			this.timeout(20000);
			// Importing the other one instead would silently hand the user a
			// document in a layout they did not choose.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: false });

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_DUAL
			);

			var row = queueRow(attachment.id);
			assert.equal(row.status, Zotero.ProgressQueue.ROW_FAILED);
			assert.equal(row.message, Zotero.getString('pharos-translate-error-no-output'));
			assert.lengthOf(item.getAttachments(), 1, "nothing was imported");
		});

		it("should truncate a backend stack trace in the queue row", async function () {
			this.timeout(20000);
			// job.error is whatever the engine printed, and the row is one line
			// in a small dialog. Untruncated, a traceback blows the column out.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({
				status: 'error',
				error: 'Traceback (most recent call last):\n' + 'x'.repeat(4000),
			});

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			var row = queueRow(attachment.id);
			assert.equal(row.status, Zotero.ProgressQueue.ROW_FAILED);
			assert.isAtMost(row.message.length, 201, "200 characters plus the ellipsis");
			assert.isTrue(row.message.startsWith('Traceback'), "the useful end is kept");
			assert.isTrue(row.message.endsWith('…'), "and it says it was cut");
		});

		it("should say something when the failure said nothing", async function () {
			this.timeout(20000);
			// An error made of whitespace is still an empty cell, and an empty
			// cell in a queue reads as a row that is still running.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'error', error: '   ' });

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			var row = queueRow(attachment.id);
			assert.equal(row.status, Zotero.ProgressQueue.ROW_FAILED);
			assert.equal(row.message, Zotero.getString('pharos-translate-error-failed'));
		});
	});

	describe("#stageIndex()", function () {
		// The engine's stage label is free-form prose that can sit unchanged for
		// minutes. Mapping it onto three fixed steps is what stops that reading
		// as a hang, and the mapping has to be the web client's
		// (frontend/src/lib/model.ts) or the same job is described as being at
		// two different steps in two windows.

		it("should recognise the engine's labels in both languages", function () {
			var T = Zotero.Pharos.Translate;
			assert.equal(T.stageIndex('Parsing document layout', 5), 0);
			assert.equal(T.stageIndex('解析版面', 5), 0);
			assert.equal(T.stageIndex('Translating paragraphs', 40), 1);
			assert.equal(T.stageIndex('翻译正文', 40), 1);
			assert.equal(T.stageIndex('Typesetting output', 90), 2);
			assert.equal(T.stageIndex('重排版面', 90), 2);
		});

		it("should treat a queued job as the first step", function () {
			assert.equal(Zotero.Pharos.Translate.stageIndex('queued', 0), 0);
		});

		it("should fall back to progress for a label it does not know", function () {
			// A stepper frozen at step one for a job that is nearly done would
			// be a worse lie than a rough guess.
			var T = Zotero.Pharos.Translate;
			assert.equal(T.stageIndex('', 0), 0);
			assert.equal(T.stageIndex('doing something new', 50), 1);
			assert.equal(T.stageIndex('doing something new', 99), 2);
		});

		it("should never index past the last step", function () {
			// Math.floor(100 / 100 * 3) is 3, which would be off the end.
			assert.equal(Zotero.Pharos.Translate.stageIndex('mystery', 100), 2);
		});
	});

	describe("#getTranslatableAttachment()", function () {
		it("should return a PDF attachment directly", async function () {
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			assert.equal(
				Zotero.Pharos.Translate.getTranslatableAttachment(attachment).id,
				attachment.id
			);
		});

		it("should tolerate no item at all", function () {
			// The item pane sets .item = null between selections, and a throw
			// there would break rendering rather than just hiding the section.
			assert.isNull(Zotero.Pharos.Translate.getTranslatableAttachment(null));
		});

		it("should prefer the source PDF over its own translation", async function () {
			this.timeout(20000);
			// A translation is attached to the SAME parent as the file it was
			// made from, so a translated paper has two PDF children and the
			// order they come back in is not a promise. Picking the translation
			// would report the state of the output and offer to translate a
			// translation.
			var { item, attachment } = await translatedPaper();
			assert.lengthOf(item.getAttachments(), 2);
			assert.equal(
				Zotero.Pharos.Translate.getTranslatableAttachment(item).id,
				attachment.id
			);
		});
	});

	describe("#isTranslation()", function () {
		it("should recognise what _importResult named", async function () {
			this.timeout(20000);
			var { item, attachment } = await translatedPaper();
			var translation = Zotero.Items.get(item.getAttachments())
				.find(a => a.id != attachment.id);
			assert.isTrue(Zotero.Pharos.Translate.isTranslation(translation));
			assert.isFalse(Zotero.Pharos.Translate.isTranslation(attachment));
		});

		it("should not claim an unrelated parenthesised filename", async function () {
			// The suffix is the role marker, so anything that happens to end in
			// brackets must not be read as one.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			attachment.setField('title', 'Paper (draft).pdf');
			await attachment.saveTx();
			assert.isFalse(Zotero.Pharos.Translate.isTranslation(attachment));
		});

		it("should say which rendering it is", async function () {
			this.timeout(20000);
			var { item, attachment } = await translatedPaper(
				Zotero.Pharos.Translate.MODE_DUAL, true
			);
			var modes = Zotero.Items.get(item.getAttachments())
				.filter(a => a.id != attachment.id)
				.map(a => Zotero.Pharos.Translate.getTranslationMode(a));
			assert.sameMembers(modes, [
				Zotero.Pharos.Translate.MODE_MONO,
				Zotero.Pharos.Translate.MODE_DUAL
			]);
			assert.isNull(Zotero.Pharos.Translate.getTranslationMode(attachment));
		});
	});

	describe("#getState()", function () {
		it("should return null for an item translation does not apply to", async function () {
			// The item pane section hides on null. Anything else would put an
			// inert panel on every note and web page in the library.
			var note = await createDataObject('item', { itemType: 'note' });
			assert.isNull(Zotero.Pharos.Translate.getState(note));
			var item = await createDataObject('item');
			assert.isNull(Zotero.Pharos.Translate.getState(item));
		});

		it("should not touch the network", async function () {
			// This runs on every selection change, including an arrow key held
			// down over a list. A request here would be one per row scrolled
			// past -- and because the backend addresses papers by content hash,
			// it would be an upload rather than a GET.
			this.timeout(20000);
			var { item, attachment } = await translatedPaper();
			origRequest = Zotero.Pharos.API.request;
			Zotero.Pharos.API.request = function () {
				throw new Error("getState() went to the network");
			};
			assert.ok(Zotero.Pharos.Translate.getState(item));
			assert.ok(Zotero.Pharos.Translate.getState(attachment));
		});

		it("should say unknown, not untranslated, with no local evidence", async function () {
			// The account is shared with the web client. "Not translated" is a
			// claim about the account made from a library, and it is wrong for
			// exactly the user who has been translating on the web.
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			var state = Zotero.Pharos.Translate.getState(item);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_UNKNOWN);
			assert.isEmpty(state.translations);
		});

		it("should report translated once the translation is attached", async function () {
			this.timeout(20000);
			// The resting state comes from the library: the translated
			// attachment plus the relation tying it to its source. Nothing in
			// _jobs survives, which is what makes this the load-bearing path.
			var { item } = await translatedPaper();
			var state = Zotero.Pharos.Translate.getState(item);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_TRANSLATED);
			assert.lengthOf(state.translations, 1);
			assert.isTrue(state.translations[0].getField('title').includes(suffix('mono')));
		});

		it("should find the translation through the relation, not the filename", async function () {
			this.timeout(20000);
			// Renaming either file is a thing users do. The pairing has to
			// survive it, which is why it is a dc:relation and not a prefix
			// match.
			var { item, attachment } = await translatedPaper();
			var translation = Zotero.Items.get(item.getAttachments())
				.find(a => a.id != attachment.id);
			attachment.setField('title', 'Something else entirely.pdf');
			await attachment.saveTx();

			var state = Zotero.Pharos.Translate.getState(attachment);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_TRANSLATED);
			assert.deepEqual(state.translations.map(t => t.id), [translation.id]);
		});

		it("should link a translation back to the paper it came from", async function () {
			this.timeout(20000);
			var { item, attachment } = await translatedPaper();
			var translation = Zotero.Items.get(item.getAttachments())
				.find(a => a.id != attachment.id);

			var state = Zotero.Pharos.Translate.getState(translation);
			assert.isTrue(state.isTranslation);
			assert.ok(state.original, "the original is reachable from the translation");
			assert.equal(state.original.id, attachment.id);
			assert.isEmpty(state.translations, "a translation is not its own translation");
		});

		it("should report a failure, truncated, without a running job", async function () {
			this.timeout(20000);
			// The whole point: the progress dialog is closed by now. Untruncated
			// this is a Python traceback, which would push the retry button off
			// the bottom of the item pane.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({
				status: 'error',
				error: 'Traceback (most recent call last):\n' + 'x'.repeat(4000),
			});

			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			var state = Zotero.Pharos.Translate.getState(item);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_FAILED);
			assert.isAtMost(state.error.length, 201, "200 characters plus the ellipsis");
			assert.isTrue(state.error.endsWith('…'));
			assert.equal(state.mode, Zotero.Pharos.Translate.MODE_MONO,
				"retry knows which mode to run");
		});

		it("should keep an older translation reachable after a failed re-run", async function () {
			this.timeout(30000);
			// A failure is a report on the most recent attempt, not a verdict on
			// the file that is already there. Reporting only the failure would
			// hide a translation the user can still open.
			var { item, attachment } = await translatedPaper();
			stubBackend({ status: 'error', error: 'engine exploded' });
			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_DUAL
			);

			var state = Zotero.Pharos.Translate.getState(item);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_FAILED);
			assert.lengthOf(state.translations, 1, "the mono translation is still offered");
		});

		it("should report a queued paper as running before its job starts", async function () {
			this.timeout(20000);
			// The queue runs one at a time, so the second of two selected papers
			// can sit untouched for minutes. Reporting it as anything else
			// invites a second click that queues it twice.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: false });

			var promise = Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);
			var state = Zotero.Pharos.Translate.getState(item);
			assert.equal(state.state, Zotero.Pharos.Translate.STATE_RUNNING);
			assert.equal(state.phase, 'queued');
			await promise;
		});

		it("should normalise the stage while keeping the engine's own words", async function () {
			this.timeout(30000);
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			var seen = [];
			var listener = () => {
				let state = Zotero.Pharos.Translate.getState(attachment);
				if (state && state.phase == 'running') {
					seen.push(state);
				}
			};
			Zotero.Pharos.Translate.addStateListener(listener);
			try {
				var replies = [
					{ status: 'running', stage: 'Typesetting the rebuilt pages', progress: 80 },
					{ status: 'done', has_mono: true, has_dual: false },
				];
				stubBackend(() => (replies.length > 1 ? replies.shift() : replies[0]));
				await Zotero.Pharos.Translate.translateItems(
					[attachment], Zotero.Pharos.Translate.MODE_MONO
				);
			}
			finally {
				Zotero.Pharos.Translate.removeStateListener(listener);
			}

			assert.isNotEmpty(seen, "the poll loop reported progress");
			assert.equal(seen[0].stageIndex, 2);
			assert.equal(seen[0].progress, 80);
			assert.equal(seen[0].stage, 'Typesetting the rebuilt pages',
				"the raw stage survives for the tooltip");
		});

		it("should not leave a cancelled queue reporting itself as running", async function () {
			this.timeout(30000);
			// Cancelling empties the queue, and nothing downstream ever touches
			// what was in it again. Left alone, a paper that was waiting its
			// turn would sit at "translating" in the item pane for the rest of
			// the session -- the exact stale-forever state this section exists
			// to remove. The FIRST paper is already in flight and fails through
			// the poll loop's own cancellation check; the second is the one that
			// only the cancel handler can reach.
			var first = await importPDFAttachment(await createDataObject('item'));
			var second = await importPDFAttachment(await createDataObject('item'));
			stubBackend({ status: 'done', has_mono: true, has_dual: false });

			var promise = Zotero.Pharos.Translate.translateItems(
				[first, second], Zotero.Pharos.Translate.MODE_MONO
			);
			Zotero.ProgressQueues.get('pharos-translate').cancel();

			var waiting = Zotero.Pharos.Translate.getState(second);
			assert.equal(waiting.state, Zotero.Pharos.Translate.STATE_FAILED);
			assert.equal(waiting.error, Zotero.getString('pharos-translate-error-cancelled'));

			await promise;
			var inFlight = Zotero.Pharos.Translate.getState(first);
			assert.equal(inFlight.state, Zotero.Pharos.Translate.STATE_FAILED);
		});
	});

	describe("#addStateListener()", function () {
		it("should survive a listener that throws", async function () {
			this.timeout(20000);
			// The listeners are called from inside the poll loop. One badly
			// behaved section must not take a translation down with it.
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			var bad = () => {
				throw new Error("section blew up");
			};
			Zotero.Pharos.Translate.addStateListener(bad);
			try {
				stubBackend({ status: 'done', has_mono: true, has_dual: false });
				await Zotero.Pharos.Translate.translateItems(
					[attachment], Zotero.Pharos.Translate.MODE_MONO
				);
			}
			finally {
				Zotero.Pharos.Translate.removeStateListener(bad);
			}
			assert.lengthOf(item.getAttachments(), 2, "the translation still landed");
		});
	});

	describe("#isEnabled()", function () {
		var PREF = 'pharos.pdfTranslation';

		afterEach(function () {
			Zotero.Prefs.clear(PREF);
		});

		it("should treat an unset value as enabled", function () {
			// The direction matters. The pref is written only once a login or a
			// verify has seen the account, so "unset" means "not asked yet" --
			// defaulting the other way would hide the feature from every account
			// for the window between startup and the first verify.
			Zotero.Prefs.clear(PREF);
			assert.isTrue(Zotero.Pharos.Translate.isEnabled());
		});

		it("should honour the account switch being off", function () {
			Zotero.Prefs.set(PREF, false);
			assert.isFalse(Zotero.Pharos.Translate.isEnabled());
		});

		it("should report no state at all when the account has it off",
			async function () {
				// The whole apparatus has to go, not just the buttons: a user who
				// switched this off in the web client and still finds 译文/对照
				// waiting here is being told their own setting did not take.
				var item = await createDataObject('item');
				var attachment = await importFileAttachment('test.pdf', {
					parentItemID: item.id,
				});

				Zotero.Prefs.clear(PREF);
				assert.ok(Zotero.Pharos.Translate.getState(attachment),
					"the fixture cannot show the gate if it has no state to begin with");

				Zotero.Prefs.set(PREF, false);
				assert.isNull(Zotero.Pharos.Translate.getState(attachment));
			});
	});

	describe("#stateLabel()", function () {
		it("should name the three states that report something happened", function () {
			var T = Zotero.Pharos.Translate;
			for (let state of [T.STATE_RUNNING, T.STATE_FAILED, T.STATE_TRANSLATED]) {
				let label = T.stateLabel(state);
				assert.isNotEmpty(label, `${state} has no label`);
				assert.notEqual(label, state, `${state} rendered as its own key`);
			}
		});

		it("should say nothing for the unknown state", function () {
			// Deliberate: "no translation here" is the resting state of nearly
			// every row, and printing it on all of them makes a scannable column
			// unreadable -- while making a paper translated on another machine
			// look exactly like a book.
			assert.equal(Zotero.Pharos.Translate.stateLabel(
				Zotero.Pharos.Translate.STATE_UNKNOWN), '');
		});
	});
});
