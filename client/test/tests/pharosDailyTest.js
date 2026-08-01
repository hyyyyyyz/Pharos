describe("Zotero.Pharos.Daily", function () {
	var origBaseURL;

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	after(async function () {
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		await Zotero.Pharos.API.setToken(null);
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Daily.saveToLibrary);
	});

	describe("#today()", function () {
		it("should use the local date, not UTC", function () {
			// The digest is keyed by the date the reader is having. Deriving it
			// from toISOString() would hand someone in UTC+8 asking at 09:00 the
			// previous day's papers.
			var now = new Date();
			var expected = now.getFullYear()
				+ '-' + String(now.getMonth() + 1).padStart(2, '0')
				+ '-' + String(now.getDate()).padStart(2, '0');
			assert.equal(Zotero.Pharos.Daily.today(), expected);
		});

		it("should be a valid date string", function () {
			assert.match(Zotero.Pharos.Daily.today(), /^\d{4}-\d{2}-\d{2}$/);
		});
	});

	describe("#buildNote()", function () {
		it("should return nothing for an unread paper", function () {
			assert.equal(Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'pending',
				summary_zh: 'should be ignored',
			}), '');
		});

		it("should include the summary and highlights", function () {
			var note = Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'done',
				summary_zh: 'The summary.',
				highlights: { contribution: 'The contribution.', method: 'The method.' },
			});
			assert.include(note, 'The summary.');
			assert.include(note, 'The contribution.');
			assert.include(note, 'The method.');
		});

		it("should escape HTML in the model's output", function () {
			// The summary is model output stored server-side and rendered into a
			// note. Interpolating it raw would let a crafted paper title or
			// summary inject markup into the user's library.
			var note = Zotero.Pharos.Daily.buildNote({
				title: '<img src=x onerror=alert(1)>',
				read_status: 'done',
				summary_zh: '<script>bad()</script>',
			});
			assert.notInclude(note, '<img');
			assert.notInclude(note, '<script>');
			assert.include(note, '&lt;img');
		});

		it("should skip highlight keys the model left out", function () {
			var note = Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'done',
				highlights: { contribution: 'Only this one.' },
			});
			assert.include(note, 'Only this one.');
			assert.notInclude(note, '<li><strong></strong>');
		});
	});

	describe("#saveToLibrary()", function () {
		it("should create a preprint with arXiv metadata", async function () {
			var item = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'd1',
				arxiv_id: '2601.01234',
				title: 'Learning to Test',
				authors: ['Ada Lovelace', 'Alan Turing'],
				abstract: 'An abstract.',
				arxiv_url: 'https://arxiv.org/abs/2601.01234',
				published_at: '2026-01-15T00:00:00Z',
				read_status: 'pending',
			});
			assert.equal(item.itemType, 'preprint');
			assert.equal(item.getField('title'), 'Learning to Test');
			assert.equal(item.getField('archiveID'), 'arXiv:2601.01234');
			assert.equal(item.getField('repository'), 'arXiv');
			assert.equal(item.getField('date').slice(0, 10), '2026-01-15');
			assert.lengthOf(item.getCreators(), 2);
		});

		it("should attach the model's reading as a child note", async function () {
			var item = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'd2',
				arxiv_id: '2601.05678',
				title: 'Read Me',
				authors: [],
				read_status: 'done',
				summary_zh: 'A Chinese summary.',
			});
			var notes = Zotero.Items.get(item.getNotes());
			assert.lengthOf(notes, 1);
			assert.include(notes[0].getNote(), 'A Chinese summary.');
		});

		it("should not attach a note for an unread paper", async function () {
			var item = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'd3',
				arxiv_id: '2601.09999',
				title: 'Unread',
				authors: [],
				read_status: 'pending',
			});
			assert.lengthOf(item.getNotes(), 0);
		});

		it("should file the item into a collection when asked", async function () {
			var collection = await createDataObject('collection');
			var item = await Zotero.Pharos.Daily.saveToLibrary(
				{ id: 'd4', arxiv_id: '2601.00001', title: 'Filed', authors: [], read_status: 'pending' },
				{ collections: [collection.id] }
			);
			assert.isTrue(item.inCollection(collection.id));
		});
	});

	describe("the digest window", function () {
		it("should open and report being signed out", async function () {
			// Opening it at all covers the localization ids in pharosDaily.xhtml:
			// one missing from the window's own resource list stops it appearing.
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosDaily.xhtml");
			try {
				// loadWindow resolves on the load event, but init() is async and
				// keeps going after it. Asserting without waiting passed alone and
				// failed whenever the machine was busier.
				await win.Zotero_Pharos_Daily.initialized;
				assert.ok(win.document.getElementById('pharos-daily-root'));
				// Signed out, so it must say so rather than sit blank.
				assert.isNotEmpty(win.document.getElementById('pharos-daily-status').textContent);
				assert.isTrue(win.document.getElementById('pharos-daily-refresh').disabled);
			}
			finally {
				win.close();
			}
		});
	});
});
