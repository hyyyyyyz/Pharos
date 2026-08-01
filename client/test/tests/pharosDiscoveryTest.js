describe("Zotero.Pharos.Discovery", function () {
	after(async function () {
		await Zotero.Pharos.API.setToken(null);
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Discovery.search);
	});

	describe("#isPreprint()", function () {
		it("should treat an arXiv-only result as a preprint", function () {
			assert.isTrue(Zotero.Pharos.Discovery.isPreprint({
				sources: ['arxiv'], venue: null,
			}));
		});

		it("should treat a journal result as an article", function () {
			assert.isFalse(Zotero.Pharos.Discovery.isPreprint({
				sources: ['openalex'], venue: 'Nature',
			}));
		});

		it("should not decide on the DOI", function () {
			// arXiv mints DOIs too, so a DOI says nothing about whether a paper
			// is a preprint.
			assert.isTrue(Zotero.Pharos.Discovery.isPreprint({
				sources: ['openalex'], venue: 'arXiv', doi: '10.48550/arXiv.2601.00001',
			}));
		});

		it("should recognise other preprint servers", function () {
			assert.isTrue(Zotero.Pharos.Discovery.isPreprint({
				sources: ['openalex'], venue: 'bioRxiv',
			}));
		});
	});

	describe("#buildNote()", function () {
		it("should return nothing when the model wrote nothing", function () {
			assert.equal(Zotero.Pharos.Discovery.buildNote({
				title: 'A paper', summary_zh: '', contribution: '', method: '',
			}), '');
		});

		it("should include the sections the model filled in", function () {
			var note = Zotero.Pharos.Discovery.buildNote({
				title: 'A paper',
				summary_zh: 'The summary.',
				contribution: 'The contribution.',
				core_trick: 'The trick.',
				limitations: 'The limits.',
				analysis_mode: 'llm',
			});
			assert.include(note, 'The summary.');
			assert.include(note, 'The contribution.');
			assert.include(note, 'The trick.');
			assert.include(note, 'The limits.');
		});

		it("should say when the reading came from rules rather than a model", function () {
			// A rules-based summary is a placeholder; a note that did not admit
			// it would be mistaken for the model's own reading.
			var note = Zotero.Pharos.Discovery.buildNote({
				title: 'A paper',
				summary_zh: 'Mechanical summary.',
				analysis_mode: 'rules',
			});
			assert.include(note, Zotero.getString('pharos-discovery-rules-note'));
		});

		it("should escape model output", function () {
			var note = Zotero.Pharos.Discovery.buildNote({
				title: '<img src=x onerror=alert(1)>',
				summary_zh: '<script>bad()</script>',
				analysis_mode: 'llm',
			});
			assert.notInclude(note, '<img');
			assert.notInclude(note, '<script>');
		});
	});

	describe("#saveToLibrary()", function () {
		it("should file a journal result as an article", async function () {
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r1',
				title: 'A Journal Paper',
				authors: ['Ada Lovelace'],
				abstract: 'An abstract.',
				year: 2025,
				venue: 'Nature',
				doi: '10.1000/xyz',
				url: 'https://example.org/paper',
				sources: ['openalex'],
				summary_zh: '',
			});
			assert.equal(item.itemType, 'journalArticle');
			assert.equal(item.getField('publicationTitle'), 'Nature');
			assert.equal(item.getField('DOI'), '10.1000/xyz');
			assert.equal(item.getField('date'), '2025');
		});

		it("should file an arXiv result as a preprint", async function () {
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r2',
				title: 'A Preprint',
				authors: ['Alan Turing'],
				abstract: 'An abstract.',
				year: 2026,
				venue: null,
				sources: ['arxiv'],
				summary_zh: '',
			});
			assert.equal(item.itemType, 'preprint');
			assert.equal(item.getField('repository'), 'arXiv');
		});

		it("should not lose the item to a field the type does not have", async function () {
			// publicationTitle is not valid on a preprint. Setting it would throw
			// and take the whole item with it, so unknown fields are skipped.
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r3',
				title: 'Mixed Metadata',
				authors: [],
				venue: 'arXiv',
				doi: '10.48550/arXiv.2601.00002',
				sources: ['arxiv', 'openalex'],
				summary_zh: '',
			});
			assert.equal(item.itemType, 'preprint');
			assert.equal(item.getField('title'), 'Mixed Metadata');
		});

		it("should attach the reading as a child note", async function () {
			var item = await Zotero.Pharos.Discovery.saveToLibrary({
				id: 'r4',
				title: 'Read Me',
				authors: [],
				sources: ['arxiv'],
				summary_zh: 'A Chinese summary.',
				analysis_mode: 'llm',
			});
			var notes = Zotero.Items.get(item.getNotes());
			assert.lengthOf(notes, 1);
			assert.include(notes[0].getNote(), 'A Chinese summary.');
		});
	});

	describe("the discovery window", function () {
		it("should open and report being signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosDiscovery.xhtml");
			try {
				await win.Zotero_Pharos_Discovery.initialized;
				assert.isNotEmpty(win.document.getElementById('pharos-daily-status').textContent);
				assert.isTrue(win.document.getElementById('pharos-discovery-search').disabled);
			}
			finally {
				win.close();
			}
		});
	});
});
