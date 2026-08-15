describe("Zotero.Pharos.Harness", function () {
	var origRequest = null;
	var requests = [];

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

	afterEach(function () {
		restoreRequests();
	});

	it("should list workflows through the authenticated API", async function () {
		captureRequests([{ workflowKey: 'harness.canary', version: 1 }]);
		let workflows = await Zotero.Pharos.Harness.listWorkflows();
		assert.deepEqual(workflows, [{ workflowKey: 'harness.canary', version: 1 }]);
		assert.equal(requests[0].path, '/api/harness/workflows');
		assert.notOk(requests[0].options && requests[0].options.anon,
			"run data is owner-scoped and must carry the bearer token");
	});

	it("should read a run and its events by id", async function () {
		captureRequests({ id: 'run-1', state: 'succeeded', steps: [] });
		let run = await Zotero.Pharos.Harness.getRun('run-1');
		assert.equal(run.id, 'run-1');
		assert.equal(requests[0].path, '/api/harness/runs/run-1');

		captureRequests({ events: [], nextSeq: 0 });
		await Zotero.Pharos.Harness.events('run-1', { afterSeq: 7 });
		assert.equal(requests[0].path, '/api/harness/runs/run-1/events?after_seq=7');
	});

	it("should control runs without client-side state", async function () {
		captureRequests({ id: 'run-2', state: 'cancelled' });
		await Zotero.Pharos.Harness.cancel('run-2');
		assert.equal(requests[0].method, 'POST');
		assert.equal(requests[0].path, '/api/harness/runs/run-2/cancel');

		captureRequests({ id: 'run-3', state: 'paused' });
		await Zotero.Pharos.Harness.pause('run-3');
		assert.equal(requests[0].path, '/api/harness/runs/run-3/pause');

		captureRequests({ id: 'run-3', state: 'queued' });
		await Zotero.Pharos.Harness.resume('run-3');
		assert.equal(requests[0].path, '/api/harness/runs/run-3/resume');
	});

	it("should send approval decisions over the wire", async function () {
		captureRequests({ id: 'a-1', state: 'approved' });
		await Zotero.Pharos.Harness.decideApproval('a-1', 'approved', 'go');
		assert.equal(requests[0].path, '/api/harness/approvals/a-1/decision');
		assert.equal(requests[0].options.body.decision, 'approved');
		assert.equal(requests[0].options.body.reason, 'go');
	});

	it("should cap the run list limit at the backend ceiling", async function () {
		captureRequests({ runs: [], nextCursor: null });
		await Zotero.Pharos.Harness.listRuns({ limit: 100000 });
		assert.include(requests[0].path, 'limit=200',
			"a larger client-side limit must not pass a 422-sized value");
	});
});
