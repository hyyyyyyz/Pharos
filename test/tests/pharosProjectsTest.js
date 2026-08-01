describe("Zotero.Pharos.Projects", function () {
	// Every note this file makes is standalone, so it lands as a top-level item
	// in the library. Left behind, they change what later suites see when they
	// select or enumerate items -- three itemPane tests failed only when run
	// after this file. Child notes and attachments, which the other Pharos
	// suites create, hang off their parent and do not have that effect.
	var created = [];

	async function track(promise) {
		let item = await promise;
		created.push(item.id);
		return item;
	}

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
		if (created.length) {
			await Zotero.Items.erase(created);
			created = [];
		}
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Projects.advance);
	});

	describe("#canAdvance()", function () {
		it("should allow advancing from an early stage", function () {
			assert.isTrue(Zotero.Pharos.Projects.canAdvance({ stage: 'discovery' }));
		});

		it("should refuse to advance past the last stage", function () {
			assert.isFalse(Zotero.Pharos.Projects.canAdvance({ stage: 'complete' }));
		});

		it("should know every stage the backend defines", function () {
			// The stage list is duplicated from the backend's ProjectStage
			// literal. If they drift, the UI shows a blank stage label and the
			// advance button enables when it should not.
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-stage-' + stage),
					`missing label for stage ${stage}`
				);
			}
		});

		it("should have a label for every artifact type", function () {
			for (let type of Zotero.Pharos.Projects.ARTIFACT_TYPES) {
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-type-' + type.replace('_', '-')),
					`missing label for type ${type}`
				);
			}
		});
	});

	describe("#saveArtifactAsNote()", function () {
		var project = {
			id: 'p1',
			name: 'A Project',
			automation_notice: 'No compute runner is connected.',
		};

		it("should save a standalone note with the record's context", async function () {
			var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote({
				id: 'a1',
				title: 'A Claim',
				type: 'claim',
				stage: 'claims',
				status: 'ready',
				body: 'The claim body.',
			}, project));
			assert.equal(note.itemType, 'note');
			var html = note.getNote();
			assert.include(html, 'A Claim');
			assert.include(html, 'A Project');
			assert.include(html, 'The claim body.');
		});

		it("should carry the automation notice into the note", async function () {
			// A record that reads like an experiment result, detached from the
			// caveat that nothing executed it, is exactly the misreading the
			// backend's notice exists to prevent.
			var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote({
				id: 'a2',
				title: 'A Result',
				type: 'result',
				stage: 'analysis',
				status: 'draft',
				body: '95% accuracy.',
			}, project));
			assert.include(note.getNote(), 'No compute runner is connected.');
		});

		it("should escape the record's body", async function () {
			var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote({
				id: 'a3',
				title: '<img src=x onerror=alert(1)>',
				type: 'draft',
				stage: 'drafting',
				status: 'draft',
				body: '<script>bad()</script>',
			}, project));
			var html = note.getNote();
			assert.notInclude(html, '<img src=x');
			assert.notInclude(html, '<script>bad');
		});

		it("should survive a record with no body", async function () {
			var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote({
				id: 'a4',
				title: 'Empty',
				type: 'hypothesis',
				stage: 'ideation',
				status: 'draft',
				body: '',
			}, project));
			assert.include(note.getNote(), 'Empty');
		});

		it("should file the note into a collection when asked", async function () {
			var collection = await createDataObject('collection');
			var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote(
				{ id: 'a5', title: 'Filed', type: 'claim', stage: 'claims', status: 'draft', body: '' },
				project,
				{ collections: [collection.id] }
			));
			assert.isTrue(note.inCollection(collection.id));
		});
	});

	describe("the projects window", function () {
		it("should open and report being signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosProjects.xhtml");
			try {
				await win.Zotero_Pharos_Projects.initialized;
				assert.isNotEmpty(win.document.getElementById('pharos-daily-status').textContent);
				assert.isTrue(win.document.getElementById('pharos-projects-select').disabled);
			}
			finally {
				win.close();
			}
		});
	});
});
