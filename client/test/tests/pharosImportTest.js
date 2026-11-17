describe("Pharos link import", function () {
	// Own sandbox: a global sinon.restore() would also undo stubs other suites
	// set up for themselves.
	var win, doc, rail, translate;
	var sandbox = sinon.createSandbox();

	beforeEach(function () {
		// The one seam to the network and the item saver. Nothing in this file
		// reaches arXiv.
		translate = sandbox.stub(Zotero.Pharos.Import, 'translateIdentifier');
	});

	afterEach(function () {
		sandbox.restore();
	});

	// Only the groups that need the main window open one. Creating items
	// while a window is open that is closed at the end of the file left a
	// later suite's note save hanging for 10s (pharosStrings), so everything
	// that can run without a window does.
	function windowHooks() {
		before(async function () {
			win = await loadZoteroPane();
			doc = win.document;
			rail = doc.getElementById('pharos-rail');
		});

		beforeEach(async function () {
			await selectLibrary(win);
			rail.module = 'library';
			rail._importInput.value = '';
		});

		after(async function () {
			rail.module = 'library';
			win.ZoteroPane.itemsView.selection.clearSelection();
			await Zotero.Promise.delay(500);
			win.close();
		});
	}

	// A stand-in for what the arXiv translator saves.
	async function fakeSaved(id, props = {}) {
		return createDataObject('item', Object.assign({
			itemType: 'preprint',
			setTitle: 'Attention Is All You Need',
		}, props)).then(async (item) => {
			item.setField('archiveID', `arXiv:${id}`);
			await item.saveTx();
			return item;
		});
	}

	describe("#parse()", function () {
		var parse = text => Zotero.Pharos.Import.parse(text);

		it("should read an arXiv abs link", function () {
			assert.deepEqual(parse('https://arxiv.org/abs/2606.01164'), [{ arXiv: '2606.01164' }]);
		});

		it("should read an arXiv pdf link", function () {
			assert.deepEqual(parse('https://arxiv.org/pdf/2606.01164'), [{ arXiv: '2606.01164' }]);
		});

		it("should drop the version number", function () {
			assert.deepEqual(parse('https://arxiv.org/abs/1706.03762v7'), [{ arXiv: '1706.03762' }]);
		});

		it("should read a .pdf suffix and the /html/ form", function () {
			assert.deepEqual(parse('https://arxiv.org/pdf/1706.03762v7.pdf'), [{ arXiv: '1706.03762' }]);
			assert.deepEqual(parse('https://arxiv.org/html/1706.03762v1'), [{ arXiv: '1706.03762' }]);
		});

		it("should read alphaxiv and Hugging Face links", function () {
			assert.deepEqual(parse('https://www.alphaxiv.org/abs/1706.03762'), [{ arXiv: '1706.03762' }]);
			assert.deepEqual(parse('https://huggingface.co/papers/1706.03762'), [{ arXiv: '1706.03762' }]);
		});

		it("should read an old-style arXiv ID", function () {
			assert.deepEqual(parse('hep-th/9901001'), [{ arXiv: 'hep-th/9901001' }]);
		});

		it("should read a DOI and a DOI link", function () {
			assert.deepEqual(parse('10.1038/nature14539'), [{ DOI: '10.1038/nature14539' }]);
			assert.deepEqual(parse('https://doi.org/10.1038/nature14539'), [{ DOI: '10.1038/nature14539' }]);
		});

		it("should read several links, one DOI beside an arXiv ID included", function () {
			var found = parse('https://arxiv.org/abs/1706.03762\n  https://arxiv.org/pdf/2606.01164v2 10.1038/nature14539');
			assert.sameDeepMembers(found, [
				{ arXiv: '1706.03762' },
				{ arXiv: '2606.01164' },
				{ DOI: '10.1038/nature14539' },
			]);
		});

		it("should not repeat an identifier", function () {
			assert.lengthOf(
				parse('https://arxiv.org/abs/1706.03762 https://arxiv.org/pdf/1706.03762v3'),
				1
			);
		});

		it("should return nothing for text that is not a link", function () {
			assert.deepEqual(parse('hello world'), []);
			assert.deepEqual(parse(''), []);
			assert.deepEqual(parse(null), []);
		});

		it("should not take a bare ISBN-shaped number for a paper", function () {
			assert.deepEqual(parse('9780306406157'), []);
		});
	});

	describe("#findExisting()", function () {
		it("should find an item by its arXiv archiveID", async function () {
			var item = await fakeSaved('1706.03762');
			var found = await Zotero.Pharos.Import.findExisting({ arXiv: '1706.03762' });
			assert.equal(found.id, item.id);
		});

		it("should find an item by DOI", async function () {
			var item = await createDataObject('item', { itemType: 'journalArticle' });
			item.setField('DOI', '10.1000/found');
			await item.saveTx();
			var found = await Zotero.Pharos.Import.findExisting({ DOI: '10.1000/found' });
			assert.equal(found.id, item.id);
		});

		it("should match the archiveID exactly, not by prefix", async function () {
			await fakeSaved('1706.037901');
			assert.isNull(await Zotero.Pharos.Import.findExisting({ arXiv: '1706.03790' }));
		});

		it("should not count an item in the trash", async function () {
			var item = await fakeSaved('1706.03763');
			item.deleted = true;
			await item.saveTx();
			assert.isNull(await Zotero.Pharos.Import.findExisting({ arXiv: '1706.03763' }));
		});

		it("should be what Daily Papers uses to tell what is saved", async function () {
			var item = await fakeSaved('1706.03764');
			var found = await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '1706.03764' });
			assert.equal(found.id, item.id);
		});
	});

	describe("#importIdentifiers()", function () {
		it("should not call the translator for what is already there", async function () {
			var item = await fakeSaved('1706.03765');
			var result = await Zotero.Pharos.Import.importIdentifiers([{ arXiv: '1706.03765' }]);
			assert.sameMembers(result.existing.map(i => i.id), [item.id]);
			assert.lengthOf(result.imported, 0);
			assert.isFalse(translate.called);
		});

		it("should import into the library root when no collection is selected", async function () {
			var saved = await createDataObject('item');
			translate.resolves([saved]);
			var result = await Zotero.Pharos.Import.importIdentifiers([{ arXiv: '2606.00001' }]);
			assert.lengthOf(result.imported, 1);
			var args = translate.firstCall.args[1];
			assert.equal(args.libraryID, Zotero.Libraries.userLibraryID);
			assert.isFalse(args.collections);
		});

		// The library's own flags are what is read, so a stand-in library is
		// enough; a real group would also have to be created and cleaned up.
		function stubLibrary(flags) {
			var libraries = sandbox.stub(Zotero.Libraries, 'get');
			libraries.withArgs(424242).returns(flags);
			libraries.callThrough();
		}

		it("should refuse a read-only library", async function () {
			stubLibrary({ editable: false, filesEditable: true });
			var result = await Zotero.Pharos.Import.importIdentifiers(
				[{ arXiv: '2606.00003' }],
				{ libraryID: 424242, collections: false }
			);
			assert.deepEqual(result.failed.map(f => f.reason), ['read-only']);
			assert.isFalse(translate.called);
		});

		it("should refuse a library whose files cannot be edited", async function () {
			stubLibrary({ editable: true, filesEditable: false });
			var result = await Zotero.Pharos.Import.importIdentifiers(
				[{ arXiv: '2606.00004' }],
				{ libraryID: 424242, collections: false }
			);
			assert.deepEqual(result.failed.map(f => f.reason), ['read-only']);
		});

		it("should carry on after one identifier fails", async function () {
			var saved = await createDataObject('item');
			translate.onFirstCall().rejects(new Error('boom'));
			translate.onSecondCall().resolves([saved]);
			var result = await Zotero.Pharos.Import.importIdentifiers(
				[{ arXiv: '2606.00005' }, { arXiv: '2606.00006' }]
			);
			assert.lengthOf(result.failed, 1);
			assert.deepEqual(result.imported.map(i => i.id), [saved.id]);
		});

		it("should call nothing recognisable unrecognized", async function () {
			var result = await Zotero.Pharos.Import.importFromText('just words');
			assert.deepEqual(result.failed.map(f => f.reason), ['unrecognized']);
			assert.isFalse(translate.called);
		});

		it("should call a translator that finds nothing not-found", async function () {
			translate.resolves([]);
			var result = await Zotero.Pharos.Import.importFromText('https://arxiv.org/abs/2606.00007');
			assert.deepEqual(result.failed.map(f => f.reason), ['not-found']);
		});
	});

	describe("#classifyError()", function () {
		var classify = e => Zotero.Pharos.Import.classifyError(e);

		it("should recognise a rate limit", function () {
			assert.equal(classify({ status: 429 }), 'rate-limited');
			assert.equal(classify(new Error('HTTP request returned 429')), 'rate-limited');
			assert.equal(classify(new Error('Rate limit exceeded')), 'rate-limited');
		});

		it("should recognise a missing paper", function () {
			assert.equal(classify({ status: 404 }), 'not-found');
			assert.equal(classify(new Error('No translators available')), 'not-found');
		});

		it("should recognise a network failure", function () {
			assert.equal(classify(new Error('Request timed out')), 'network');
			assert.equal(classify({ status: 503 }), 'network');
			assert.equal(classify(new Error('NS_ERROR_NET_RESET')), 'network');
		});

		it("should fall back to other", function () {
			assert.equal(classify(new Error('something odd')), 'other');
			assert.equal(classify(undefined), 'other');
		});

		it("should map a thrown translator error onto the result", async function () {
			translate.rejects(Object.assign(new Error('Too Many Requests'), { status: 429 }));
			var result = await Zotero.Pharos.Import.importFromText('https://arxiv.org/abs/2606.00008');
			assert.equal(result.failed[0].reason, 'rate-limited');
		});
	});

	describe("with the main window", function () {
		windowHooks();

		describe("selected collection", function () {
			it("should import into the selected collection", async function () {
				var collection = await createDataObject('collection');
				await win.ZoteroPane.collectionsView.selectCollection(collection.id);
				translate.resolves([await createDataObject('item')]);
				await Zotero.Pharos.Import.importIdentifiers([{ arXiv: '2606.00002' }]);
				assert.deepEqual(translate.firstCall.args[1].collections, [collection.id]);
			});

			// The library's own flags are what is read, so a stand-in library is
			// enough; a real group would also have to be created and cleaned up.
			function stubLibrary(flags) {
				var libraries = sandbox.stub(Zotero.Libraries, 'get');
				libraries.withArgs(424242).returns(flags);
				libraries.callThrough();
			}

		});

		describe("rail paste box", function () {
			it("should import on Enter", async function () {
				var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(
					{ imported: [], existing: [], failed: [{ identifier: null, reason: 'unrecognized' }] }
				);
				rail._importInput.value = 'https://arxiv.org/abs/1706.03762';
				rail._importInput.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
				await new Promise(r => setTimeout(r, 0));
				await Zotero.Promise.delay(10);
				assert.equal(stub.firstCall.args[0], 'https://arxiv.org/abs/1706.03762');
			});

			it("should keep the text and say why when it fails", async function () {
				sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(
					{ imported: [], existing: [], failed: [{ identifier: { arXiv: 'x' }, reason: 'network' }] }
				);
				rail._importInput.value = 'https://arxiv.org/abs/1706.03762';
				await rail.importText(rail._importInput.value);
				assert.equal(rail._importInput.value, 'https://arxiv.org/abs/1706.03762');
				assert.equal(rail._importStatus.dataset.kind, 'error');
				assert.isFalse(rail._importStatus.hidden);
				assert.isFalse(rail._importInput.disabled);
			});

			it("should disable the field and show the beam while working", async function () {
				var release;
				sandbox.stub(Zotero.Pharos.Import, 'importFromText').returns(
					new Promise(resolve => release = resolve)
				);
				var pending = rail.importText('https://arxiv.org/abs/1706.03762');
				assert.isTrue(rail._importInput.disabled);
				assert.isTrue(rail._importBox.classList.contains('is-busy'));
				release({ imported: [], existing: [], failed: [{ identifier: null, reason: 'other' }] });
				await pending;
				assert.isFalse(rail._importInput.disabled);
				assert.isFalse(rail._importBox.classList.contains('is-busy'));
			});

			it("should select an already-imported item and clear the field", async function () {
				var item = await fakeSaved('1706.03766');
				rail._importInput.value = 'https://arxiv.org/abs/1706.03766';
				await rail.importText('https://arxiv.org/abs/1706.03766');
				assert.isFalse(translate.called);
				assert.deepEqual(win.ZoteroPane.getSelectedItems(true), [item.id]);
				assert.equal(rail._importInput.value, '');
				assert.equal(rail._importStatus.dataset.kind, 'ok');
			});

			it("should switch back to the library from another module", async function () {
				var item = await fakeSaved('1706.03767');
				// Showing Daily Papers would load its document and fire its backend
				// requests, which outlive this window; only the switch matters here.
				sandbox.stub(rail, '_ensureLoaded');
				rail.module = 'daily';
				await rail.importText('arXiv:1706.03767');
				assert.equal(rail.module, 'library');
				assert.deepEqual(win.ZoteroPane.getSelectedItems(true), [item.id]);
			});
		});

		describe("Cmd/Ctrl+V on the item list", function () {
			var importText;

			function press(target) {
				var event = new win.KeyboardEvent('keydown', {
					key: 'v',
					metaKey: Zotero.isMac,
					ctrlKey: !Zotero.isMac,
					bubbles: true,
					cancelable: true,
				});
				target.dispatchEvent(event);
				return event;
			}

			beforeEach(function () {
				importText = sandbox.stub(rail, 'importText').resolves(null);
				win.ZoteroPane.itemsView.focus();
			});

			it("should import a recognisable clipboard", function () {
				sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('https://arxiv.org/abs/1706.03762');
				var event = press(doc.activeElement);
				assert.isTrue(event.defaultPrevented);
				assert.isTrue(importText.calledOnce);
			});

			it("should leave any other clipboard alone", function () {
				sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('some copied sentence');
				var event = press(doc.activeElement);
				assert.isFalse(event.defaultPrevented);
				assert.isFalse(importText.called);
			});

			it("should leave an empty clipboard alone", function () {
				sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns(null);
				var event = press(doc.activeElement);
				assert.isFalse(event.defaultPrevented);
			});

			it("should do nothing while a text field has focus", function () {
				sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('https://arxiv.org/abs/1706.03762');
				// A field inside the item list's own container, e.g. an inline editor.
				var field = doc.createElement('input');
				doc.getElementById('zotero-items-tree').appendChild(field);
				var event = press(field);
				field.remove();
				assert.isFalse(event.defaultPrevented);
				assert.isFalse(importText.called);
			});
		});

	});

	describe("save from Daily Papers and Find Literature", function () {
		var paper = {
			id: 'd1',
			arxiv_id: '1706.03770',
			title: 'Pharos Title',
			authors: ['Ada Lovelace'],
			arxiv_url: 'https://arxiv.org/abs/1706.03770',
			read_status: 'done',
			summary_zh: 'A Chinese summary.',
		};

		it("should use the translator's item and attach the note", async function () {
			var saved = await fakeSaved('1706.03770');
			translate.resolves([saved]);
			var item = await Zotero.Pharos.Daily.saveToLibrary(paper);
			assert.equal(item.id, saved.id);
			assert.deepEqual(translate.firstCall.args[0], { arXiv: '1706.03770' });
			var notes = Zotero.Items.get(item.getNotes());
			assert.lengthOf(notes, 1);
			assert.include(notes[0].getNote(), 'A Chinese summary.');
		});

		it("should still save the paper when the translator fails", async function () {
			translate.rejects(new Error('Request timed out'));
			var item = await Zotero.Pharos.Daily.saveToLibrary(paper);
			assert.equal(item.getField('title'), 'Pharos Title');
			assert.equal(item.getField('archiveID'), 'arXiv:1706.03770');
			assert.lengthOf(item.getNotes(), 1);
		});

		it("should fall back when the translator finds nothing", async function () {
			translate.resolves([]);
			var item = await Zotero.Pharos.Daily.saveToLibrary(paper);
			assert.equal(item.getField('title'), 'Pharos Title');
		});

		it("should send a Find Literature arXiv result through the arXiv translator", async function () {
			var saved = await fakeSaved('1706.03771');
			translate.resolves([saved]);
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r1',
				title: 'A Preprint',
				authors: [],
				sources: ['arxiv'],
				url: 'https://arxiv.org/abs/1706.03771v2',
				summary_zh: '',
			});
			assert.equal(item.id, saved.id);
			assert.deepEqual(translate.firstCall.args[0], { arXiv: '1706.03771' });
		});

		it("should send a Find Literature article through its DOI", async function () {
			var saved = await createDataObject('item', { itemType: 'journalArticle' });
			translate.resolves([saved]);
			await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r2',
				title: 'A Journal Paper',
				authors: [],
				venue: 'Nature',
				doi: '10.1000/xyz',
				sources: ['openalex'],
				summary_zh: '',
			});
			assert.deepEqual(translate.firstCall.args[0], { DOI: '10.1000/xyz' });
		});

		it("should save a Find Literature result with no identifier as before", async function () {
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r3',
				title: 'No Identifier',
				authors: [],
				sources: ['openalex'],
				summary_zh: '',
			});
			assert.isFalse(translate.called);
			assert.equal(item.getField('title'), 'No Identifier');
		});
	});
});
