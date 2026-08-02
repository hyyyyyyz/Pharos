describe("Zotero.Pharos.Projects (data layer)", function () {
	var origRequest = null;
	var requests;

	/**
	 * Swap out the API layer and record what the module would have sent.
	 *
	 * Most of what these assertions are about is invisible from the return value:
	 * a camelCase key reaching a backend declared `extra="forbid"` is a 422, a
	 * `note` key that JSON.stringify dropped is a 422 on a field with no default,
	 * and a PATCH aimed at the wrong path is a 404 that reads like a missing row.
	 */
	function captureRequests(response) {
		requests = [];
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			return Promise.resolve(response === undefined ? null : response);
		};
	}

	/** Make the next call fail the way api.request() fails on a 4xx. */
	function captureFailure(error) {
		requests = [];
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			return Promise.reject(error);
		};
	}

	function restoreRequests() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	}

	function lastBody() {
		return requests[requests.length - 1].options.body;
	}

	/** Passes in any locale: en-US throws on a missing id, others return it. */
	function assertLocalized(value, id) {
		assert.isNotEmpty(value, `missing string ${id}`);
		assert.notEqual(value, id, `missing string ${id}`);
	}

	afterEach(function () {
		restoreRequests();
	});

	after(async function () {
		restoreRequests();
		await Zotero.Pharos.API.setToken(null);
	});

	it("should expose every mutation the projects window needs", function () {
		// Guards the wiring as much as the file: a function that exists but is
		// called by nothing is the failure DECISIONS §4 names, and one the window
		// expects but that is missing is a bare TypeError mid-render.
		for (let name of [
			'list', 'get', 'canAdvance',
			'create', 'update', 'archive', 'restore', 'setStage', 'remove', 'advance',
			'addSource', 'updateSource', 'removeSource',
			'createArtifact', 'updateArtifact', 'removeArtifact',
			'saveArtifactAsNote',
			'stageLabel', 'stageShort', 'stageNote', 'typeLabel', 'statusLabel',
		]) {
			assert.isFunction(Zotero.Pharos.Projects[name], `missing ${name}()`);
		}
	});

	it("should not carry a redundant artifact-listing call", function () {
		// GET /{id}/artifacts returns exactly the artifacts the project GET
		// already carries, in the same order. It was never called from any UI.
		assert.isUndefined(Zotero.Pharos.Projects.getArtifacts);
	});

	describe("constants", function () {
		it("should have a default record type for every stage", function () {
			// The record form opens from any stage, `complete` included, and a
			// missing entry seeds the form with an undefined type -- a 422 only
			// once the user presses save.
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				let type = Zotero.Pharos.Projects.DEFAULT_TYPE[stage];
				assert.include(
					Zotero.Pharos.Projects.ARTIFACT_TYPES, type,
					`stage ${stage} defaults to a type the backend does not accept`
				);
			}
		});

		it("should know every record status the backend defines", function () {
			assert.deepEqual(
				Zotero.Pharos.Projects.ARTIFACT_STATUSES,
				['draft', 'ready', 'verified', 'rejected']
			);
		});
	});

	describe("label helpers", function () {
		it("should resolve a label, a short label and a note for every stage", function () {
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				assertLocalized(Zotero.Pharos.Projects.stageLabel(stage), stage);
				assertLocalized(Zotero.Pharos.Projects.stageShort(stage), stage + '-short');
				assertLocalized(Zotero.Pharos.Projects.stageNote(stage), stage + '-note');
			}
		});

		it("should resolve a label for every record type and status", function () {
			for (let type of Zotero.Pharos.Projects.ARTIFACT_TYPES) {
				assertLocalized(Zotero.Pharos.Projects.typeLabel(type), type);
			}
			for (let status of Zotero.Pharos.Projects.ARTIFACT_STATUSES) {
				assertLocalized(Zotero.Pharos.Projects.statusLabel(status), status);
			}
		});

		it("should replace every underscore in a type id, not just the first", function () {
			// `experiment_plan` has one underscore, so replace('_', '-') is
			// accidentally right today and would break on the first two-word type
			// added -- with a blank label rather than an error. Pinned against the
			// id itself, since a hypothetical type has no string to resolve.
			let orig = Zotero.getString;
			Zotero.getString = id => id;
			try {
				assert.equal(
					Zotero.Pharos.Projects.typeLabel('a_b_c'),
					'pharos-projects-type-a-b-c'
				);
			}
			finally {
				Zotero.getString = orig;
			}
		});
	});

	describe("#list()", function () {
		var projects = [
			{ id: 'p1', status: 'active', stage: 'ideation' },
			{ id: 'p2', status: 'archived', stage: 'complete' },
		];

		it("should hide archived projects by default", async function () {
			// pharosDiscovery's "file into a project" calls list() bare, and
			// filing a paper into an archived project would be wrong.
			captureRequests(projects);
			var result = await Zotero.Pharos.Projects.list();
			assert.deepEqual(result.map(p => p.id), ['p1']);
		});

		it("should return archived projects on request, in one call", async function () {
			// The backend has no status filter, so the toggle in the window costs
			// no second request.
			captureRequests(projects);
			var result = await Zotero.Pharos.Projects.list(true);
			assert.deepEqual(result.map(p => p.id), ['p1', 'p2']);
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].path, '/api/projects');
		});
	});

	describe("#canAdvance()", function () {
		it("should allow advancing an active project from an early stage", function () {
			assert.isTrue(Zotero.Pharos.Projects.canAdvance({
				status: 'active', stage: 'discovery',
			}));
		});

		it("should refuse to advance past the last stage", function () {
			assert.isFalse(Zotero.Pharos.Projects.canAdvance({
				status: 'active', stage: 'complete',
			}));
		});

		it("should refuse to advance an archived project", function () {
			// advance_project answers 409 "Archived projects cannot advance". The
			// stage-only test could not misfire while archived projects never
			// reached the window; now that they do, it would offer a button whose
			// only outcome is an error.
			assert.isFalse(Zotero.Pharos.Projects.canAdvance({
				status: 'archived', stage: 'discovery',
			}));
		});
	});

	describe("project mutations", function () {
		it("should send create() fields under their wire names", async function () {
			captureRequests({ id: 'p1' });
			await Zotero.Pharos.Projects.create({
				name: 'A Project', researchQuestion: 'Does it?',
			});
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/projects');
			assert.deepEqual(lastBody(), {
				name: 'A Project',
				description: '',
				research_question: 'Does it?',
			});
			// ProjectCreate is extra="forbid": the camelCase key would be a 422.
			assert.notProperty(lastBody(), 'researchQuestion');
		});

		it("should send update() patches unchanged", async function () {
			captureRequests({ id: 'p1' });
			await Zotero.Pharos.Projects.update('p1', { name: 'Renamed', description: '' });
			assert.equal(requests[0].method, 'PATCH');
			assert.equal(requests[0].path, '/api/projects/p1');
			assert.deepEqual(lastBody(), { name: 'Renamed', description: '' });
		});

		it("should archive and restore through the status field", async function () {
			captureRequests({ id: 'p1' });
			await Zotero.Pharos.Projects.archive('p1');
			assert.deepEqual(lastBody(), { status: 'archived' });
			await Zotero.Pharos.Projects.restore('p1');
			assert.deepEqual(lastBody(), { status: 'active' });
			assert.equal(requests[1].path, '/api/projects/p1');
		});

		it("should rewind through setStage()", async function () {
			// Backwards movement is the point: a failed experiment sends the
			// project back, and update_project allows it by design.
			captureRequests({ id: 'p1' });
			await Zotero.Pharos.Projects.setStage('p1', 'ideation');
			assert.equal(requests[0].method, 'PATCH');
			assert.deepEqual(lastBody(), { stage: 'ideation' });
		});

		it("should delete a project with DELETE", async function () {
			captureRequests();
			var result = await Zotero.Pharos.Projects.remove('p1');
			assert.equal(requests[0].method, 'DELETE');
			assert.equal(requests[0].path, '/api/projects/p1');
			assert.isNull(result);
		});
	});

	describe("source mutations", function () {
		it("should clear a note with an explicit null", async function () {
			// The one place null means clear: SourcePatch.note is
			// required-but-nullable and is passed straight through, where the
			// project and record patches drop nulls before applying.
			captureRequests({ id: 's1' });
			await Zotero.Pharos.Projects.updateSource('p1', 's1', null);
			assert.equal(requests[0].method, 'PATCH');
			assert.equal(requests[0].path, '/api/projects/p1/sources/s1');
			assert.deepEqual(lastBody(), { note: null });
		});

		it("should still send the note key when given nothing", async function () {
			// JSON.stringify drops an undefined value, and `note` has no default,
			// so the key going missing is a 422 rather than "leave it alone".
			captureRequests({ id: 's1' });
			await Zotero.Pharos.Projects.updateSource('p1', 's1');
			assert.property(lastBody(), 'note');
			assert.isNull(lastBody().note);
		});

		it("should send a written note as it stands", async function () {
			captureRequests({ id: 's1' });
			await Zotero.Pharos.Projects.updateSource('p1', 's1', 'Why it matters');
			assert.deepEqual(lastBody(), { note: 'Why it matters' });
		});

		it("should remove a source without touching the search it came from", async function () {
			captureRequests();
			var result = await Zotero.Pharos.Projects.removeSource('p1', 's1');
			assert.equal(requests[0].method, 'DELETE');
			assert.equal(requests[0].path, '/api/projects/p1/sources/s1');
			assert.isNull(result);
		});

		it("should add a source under its wire name", async function () {
			captureRequests({ id: 's1' });
			await Zotero.Pharos.Projects.addSource('p1', 'r1');
			assert.equal(requests[0].path, '/api/projects/p1/sources');
			assert.deepEqual(lastBody(), { result_id: 'r1', note: null });
		});
	});

	describe("record mutations", function () {
		it("should default a new record's body and status", async function () {
			captureRequests({ id: 'a1' });
			await Zotero.Pharos.Projects.createArtifact('p1', {
				stage: 'ideation', type: 'hypothesis', title: 'A hypothesis',
			});
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/projects/p1/artifacts');
			assert.deepEqual(lastBody(), {
				stage: 'ideation',
				type: 'hypothesis',
				title: 'A hypothesis',
				body: '',
				status: 'draft',
			});
		});

		it("should keep a status the user chose", async function () {
			captureRequests({ id: 'a1' });
			await Zotero.Pharos.Projects.createArtifact('p1', {
				stage: 'claims', type: 'claim', title: 'A claim', status: 'verified',
			});
			assert.equal(lastBody().status, 'verified');
		});

		it("should send record patches unchanged", async function () {
			// '' rather than null is how a body is emptied: the backend drops
			// nulls, so a null body would succeed and change nothing.
			captureRequests({ id: 'a1' });
			await Zotero.Pharos.Projects.updateArtifact('p1', 'a1', { body: '' });
			assert.equal(requests[0].method, 'PATCH');
			assert.equal(requests[0].path, '/api/projects/p1/artifacts/a1');
			assert.deepEqual(lastBody(), { body: '' });
		});

		it("should delete a record with DELETE", async function () {
			captureRequests();
			var result = await Zotero.Pharos.Projects.removeArtifact('p1', 'a1');
			assert.equal(requests[0].method, 'DELETE');
			assert.equal(requests[0].path, '/api/projects/p1/artifacts/a1');
			assert.isNull(result);
		});
	});

	describe("errors", function () {
		it("should hand the backend's own message to the caller", async function () {
			// The detail strings are written for a person, and one of them is a
			// 404 standing in for another account's row -- deliberately
			// indistinguishable from an id that never existed. Rewording either
			// would cost the user the only specific thing they are told.
			var backend = new Error('Project not found');
			backend.status = 404;
			captureFailure(backend);
			var caught;
			try {
				await Zotero.Pharos.Projects.update('someone-elses-id', { name: 'x' });
			}
			catch (e) {
				caught = e;
			}
			assert.strictEqual(caught, backend);
			assert.equal(caught.message, 'Project not found');
			assert.equal(caught.status, 404);
		});

		it("should let a SignedOutError through untouched", async function () {
			// The window turns this into a sign-in prompt and disables the write
			// controls; a module that wrapped it would break that test.
			var signedOut = new Zotero.Pharos.API.SignedOutError();
			captureFailure(signedOut);
			var caught;
			try {
				await Zotero.Pharos.Projects.removeArtifact('p1', 'a1');
			}
			catch (e) {
				caught = e;
			}
			assert.instanceOf(caught, Zotero.Pharos.API.SignedOutError);
		});
	});
});
