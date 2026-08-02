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

	describe("#translateItems()", function () {
		// Only the network is stood in for. Everything downstream of it -- the
		// temp file, the import, the relation, the queue row -- is the real
		// thing, because that is where this module's failures are: none of them
		// throws, and all of them look like a translation that worked.

		var pdfBytes, origRequest;

		before(async function () {
			let file = getTestDataDirectory();
			file.append('test.pdf');
			pdfBytes = (await IOUtils.read(file.path)).buffer;
		});

		afterEach(function () {
			if (origRequest) {
				Zotero.Pharos.API.request = origRequest;
				origRequest = null;
			}
		});

		/**
		 * @param {Object} job - what GET /api/jobs/{id} keeps answering
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
					return job;
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
});
