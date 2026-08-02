describe("Zotero.Pharos.Daily", function () {
	var origBaseURL;
	var origRequest = null;
	var origHasCredentials = null;
	var requests;

	/**
	 * Swap out the API layer and record what the module would have sent.
	 *
	 * Most of what matters here is invisible from the return value: a key the
	 * backend declares extra="forbid" is a 422, a read that inherits the API
	 * layer's 30s default is aborted while the server is still working, and a
	 * paper id with a slash in it silently addresses a different route.
	 *
	 * `responder` is either the value to resolve with, or a function called with
	 * (method, path, options) whose return value resolves and whose throw
	 * rejects -- which is how the failure paths are driven, since every one of
	 * them is an error the API layer raised and this module deliberately does not
	 * catch.
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
		if (origHasCredentials) {
			Zotero.Pharos.API.hasCredentials = origHasCredentials;
			origHasCredentials = null;
		}
	}

	/** What Zotero.Pharos.API.request throws once it has unwrapped a {"detail"}. */
	function httpError(status, message) {
		var e = new Error(message);
		e.status = status;
		return e;
	}

	/** Passes in any locale: en-US throws on a missing id, others return it. */
	function assertLocalized(id) {
		var value = Zotero.getString(id);
		assert.isNotEmpty(value, `missing string ${id}`);
		assert.notEqual(value, id, `missing string ${id}`);
	}

	/** The date every fixture below is filed under. */
	const DAY = '2026-08-02';

	/** A DailyPaperOut carrying every field the panel reads. */
	function makePaper(overrides) {
		return Object.assign({
			id: 'p1',
			arxiv_id: '2601.00001',
			title: 'A Paper',
			authors: ['Ada Lovelace', 'Alan Turing'],
			abstract: 'An abstract.',
			arxiv_url: 'https://arxiv.org/abs/2601.00001',
			pdf_url: null,
			published_at: `${DAY}T00:00:00Z`,
			venue: null,
			categories: ['cs.AI'],
			matched_domain: 'VLA',
			matched_keywords: ['vla'],
			read_status: 'pending',
			read_error: null,
			read_model: null,
			summary_zh: '',
			highlights: null,
			scores: null,
			score_recommendation: null,
		}, overrides);
	}

	/**
	 * Answer the four requests the panel makes on open.
	 *
	 * `extra` is consulted first and its `undefined` means "not mine", so a test
	 * states only the path it is about -- and may return a pending promise for
	 * it, which is how the mid-request cases below are driven.
	 */
	function digest({ papers = [], dates, status, config, extra } = {}) {
		var run = {
			id: 'r1', date: DAY, status: 'done', fetched: 80,
			read_done: papers.length, read_failed: 0, error: null,
			started_at: `${DAY}T00:00:00Z`, finished_at: `${DAY}T00:05:00Z`,
		};
		var day = { date: DAY, total: papers.length, run, papers };
		return function (method, path, options) {
			if (extra) {
				let answer = extra(method, path, options);
				if (answer !== undefined) {
					return answer;
				}
			}
			if (path == '/api/daily/config') {
				return config || { enabled: true, categories: ['cs.AI'], max_per_day: 20 };
			}
			if (path == '/api/daily/status') {
				return status || {
					llm_configured: true,
					provider: { name: 'deepseek', model: 'deepseek-chat', configured: true },
					directions: ['VLA'],
					last_run: run,
					today: null,
					sweeping: null,
				};
			}
			if (path == '/api/daily/dates') {
				return dates || [{
					date: DAY, total: papers.length, read: 0,
					pending: papers.length, failed: 0,
				}];
			}
			if (/^\/api\/daily\/\d{4}-\d{2}-\d{2}$/.test(path)) {
				return day;
			}
			return null;
		};
	}

	/**
	 * Open the digest with the API layer stubbed and a token pretended.
	 *
	 * hasCredentials() is faked rather than a token actually stored: storing one
	 * goes through OSKeyStore, which is a real keychain on some machines, and
	 * nothing here needs the token itself.
	 */
	async function withPanel(responder, fn) {
		captureRequests(responder);
		origHasCredentials = Zotero.Pharos.API.hasCredentials;
		Zotero.Pharos.API.hasCredentials = () => true;
		var win = await loadWindow("chrome://zotero/content/pharosDaily.xhtml");
		try {
			// loadWindow resolves on the load event, but init() is async and keeps
			// going after it.
			await win.Zotero_Pharos_Daily.initialized;
			await fn(win, win.document, win.Zotero_Pharos_Daily);
		}
		finally {
			win.close();
			restoreRequests();
		}
	}

	before(function () {
		origBaseURL = Zotero.Prefs.get('pharos.baseURL');
	});

	afterEach(function () {
		restoreRequests();
	});

	after(async function () {
		restoreRequests();
		Zotero.Prefs.set('pharos.baseURL', origBaseURL);
		await Zotero.Pharos.API.setToken(null);
	});

	it("should be loaded onto the Zotero namespace", function () {
		assert.isFunction(Zotero.Pharos.Daily.saveToLibrary);
		assert.isFunction(Zotero.Pharos.Daily.readPaper);
		assert.isFunction(Zotero.Pharos.Daily.findInLibrary);
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

	describe("#getDates()", function () {
		it("should ask for the dates this account matches", async function () {
			captureRequests([]);
			await Zotero.Pharos.Daily.getDates();
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'GET');
			assert.equal(requests[0].path, '/api/daily/dates');
		});

		it("should pass the summaries through unrenamed", async function () {
			// The daily schemas are plain BaseModels, so the wire is snake_case
			// all the way to the view. Camel-casing here would mean un-camelling
			// it again wherever a date row is compared with a status row.
			var row = { date: '2026-08-02', total: 5, read: 3, pending: 1, failed: 1 };
			captureRequests([row]);
			assert.deepEqual(await Zotero.Pharos.Daily.getDates(), [row]);
		});

		it("should return an empty list rather than null", async function () {
			// A 200 with no body is not a day with no papers, but no caller can
			// do anything with the difference, and every one of them would
			// otherwise need the same guard before iterating.
			captureRequests(null);
			assert.deepEqual(await Zotero.Pharos.Daily.getDates(), []);
		});

		it("should not swallow a sign-out", async function () {
			// The view is the only layer that can turn this into a prompt, so it
			// has to arrive there as itself rather than as an empty digest.
			captureRequests(() => {
				throw new Zotero.Pharos.API.SignedOutError();
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.getDates());
			assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
		});
	});

	describe("#getDay()", function () {
		it("should default to the local today", async function () {
			captureRequests({ date: Zotero.Pharos.Daily.today(), total: 0, run: null, papers: [] });
			await Zotero.Pharos.Daily.getDay();
			assert.equal(requests[0].method, 'GET');
			assert.equal(requests[0].path, `/api/daily/${Zotero.Pharos.Daily.today()}`);
		});

		it("should ask for the date it was given", async function () {
			captureRequests({ date: '2026-07-31', total: 0, run: null, papers: [] });
			await Zotero.Pharos.Daily.getDay('2026-07-31');
			assert.equal(requests[0].path, '/api/daily/2026-07-31');
		});

		it("should keep run and papers distinct on an empty day", async function () {
			// run === null means never swept; run set with no papers means swept
			// and nothing matched. They are different sentences on screen, so
			// nothing in this layer may flatten one into the other.
			var run = { id: 'r1', date: '2026-07-31', status: 'done', fetched: 120,
				read_done: 0, read_failed: 0, error: null,
				started_at: '2026-07-31T00:00:00Z', finished_at: '2026-07-31T00:04:00Z' };
			captureRequests({ date: '2026-07-31', total: 0, run, papers: [] });
			var day = await Zotero.Pharos.Daily.getDay('2026-07-31');
			assert.deepEqual(day.run, run);
			assert.lengthOf(day.papers, 0);
		});

		it("should let a bad date arrive as a 400", async function () {
			// The path regex refuses it, and that is a different thing from a day
			// with nothing in it -- which is a 200 with an empty list.
			captureRequests(() => {
				throw httpError(400, 'invalid date');
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.getDay('yesterday'));
			assert.equal(e.status, 400);
		});
	});

	describe("#getStatus()", function () {
		it("should treat an absent llm_configured as configured", async function () {
			// Never accuse the operator of a misconfiguration on the strength of
			// a field that did not arrive.
			captureRequests({});
			assert.isTrue((await Zotero.Pharos.Daily.getStatus()).llm_configured);
		});

		it("should believe an explicit false", async function () {
			captureRequests({ llm_configured: false });
			assert.isFalse((await Zotero.Pharos.Daily.getStatus()).llm_configured);
		});

		it("should return a whole shape for an empty response", async function () {
			// The view reads every one of these unconditionally; the point of
			// normalising here is that it never has to write a ?. chain.
			captureRequests(null);
			var status = await Zotero.Pharos.Daily.getStatus();
			assert.isTrue(status.llm_configured);
			assert.isNull(status.provider);
			assert.deepEqual(status.directions, []);
			assert.isNull(status.last_run);
			assert.isNull(status.today);
			assert.isNull(status.sweeping);
		});

		it("should fill in the keys the provider left out", async function () {
			captureRequests({ provider: { name: 'deepseek' } });
			var provider = (await Zotero.Pharos.Daily.getStatus()).provider;
			assert.equal(provider.name, 'deepseek');
			assert.equal(provider.model, '');
			assert.equal(provider.base_url, '');
			assert.isFalse(provider.configured);
		});

		it("should not invent a provider", async function () {
			captureRequests({ provider: null });
			assert.isNull((await Zotero.Pharos.Daily.getStatus()).provider);
		});

		it("should never hand the view a non-list of directions", async function () {
			captureRequests({ directions: 'VLA' });
			assert.deepEqual((await Zotero.Pharos.Daily.getStatus()).directions, []);
			restoreRequests();
			captureRequests({ directions: ['VLA', 'World Model'] });
			assert.deepEqual(
				(await Zotero.Pharos.Daily.getStatus()).directions, ['VLA', 'World Model']
			);
		});

		it("should report sweeping only from the sweeper's own state", async function () {
			// last_run.status reads "running" forever for a row orphaned by a
			// backend restart. A poller driven by it never stops, and the Update
			// button never comes back.
			captureRequests({
				sweeping: null,
				last_run: { id: 'r1', date: '2026-08-01', status: 'running', fetched: 0,
					read_done: 0, read_failed: 0, error: null,
					started_at: '2026-08-01T00:00:00Z', finished_at: null },
			});
			var status = await Zotero.Pharos.Daily.getStatus();
			assert.isNull(status.sweeping);
			assert.equal(status.last_run.status, 'running');
		});

		it("should carry today's own counts through", async function () {
			// These, not last_run's counters, are what a live sweep moves.
			var today = { date: '2026-08-02', total: 9, read: 4, pending: 5, failed: 0 };
			captureRequests({ today, sweeping: '2026-08-02' });
			var status = await Zotero.Pharos.Daily.getStatus();
			assert.deepEqual(status.today, today);
			assert.equal(status.sweeping, '2026-08-02');
		});

		it("should throw rather than report a misconfiguration it did not see", async function () {
			// The view keeps the last known status on a failure. Returning a
			// default here would let one dropped request put up the "no API key"
			// banner for a server that has one.
			captureRequests(() => {
				throw httpError(0, 'connection refused');
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.getStatus());
			assert.equal(e.status, 0);
		});
	});

	describe("#refresh()", function () {
		it("should send no key it was not given", async function () {
			// RefreshRequest is extra="forbid" and every field has a default, so
			// an explicit null would overrule the default it was meant to accept.
			captureRequests({ id: 'r1', date: '2026-08-02', status: 'running' });
			await Zotero.Pharos.Daily.refresh();
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/daily/refresh');
			assert.deepEqual(requests[0].options.body, {});
		});

		it("should send the date, the window and the re-read flag", async function () {
			captureRequests({ id: 'r1', date: '2026-08-01', status: 'running' });
			await Zotero.Pharos.Daily.refresh({ date: '2026-08-01', days: 3, reread: true });
			assert.deepEqual(requests[0].options.body,
				{ date: '2026-08-01', days: 3, reread: true });
		});

		it("should send reread:false when it was asked for", async function () {
			// Guards a truthiness test creeping in: false and undefined mean
			// different things, and only one of them is "leave the default".
			captureRequests({ id: 'r1', date: '2026-08-02', status: 'running' });
			await Zotero.Pharos.Daily.refresh({ reread: false });
			assert.deepEqual(requests[0].options.body, { reread: false });
		});

		it("should return a run the view can seed its counters from", async function () {
			// submit() writes the row before returning, so this is real and not
			// an optimistic placeholder.
			var run = { id: 'r7', date: '2026-08-02', status: 'running', fetched: 0,
				read_done: 0, read_failed: 0, error: null,
				started_at: '2026-08-02T01:00:00Z', finished_at: null };
			captureRequests(run);
			assert.deepEqual(await Zotero.Pharos.Daily.refresh(), run);
		});

		it("should let a 409 through with its status", async function () {
			// The server's prose is English and names an internal date. The view
			// shows pharos-daily-refresh-busy instead, and needs the status to
			// know that it should.
			captureRequests(() => {
				throw httpError(409, 'a sweep for 2026-08-02 is already running');
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.refresh());
			assert.equal(e.status, 409);
			assert.include(e.message, 'already running');
		});
	});

	describe("#readPaper()", function () {
		it("should post to the paper's read endpoint", async function () {
			captureRequests({ id: 'p1', read_status: 'done' });
			await Zotero.Pharos.Daily.readPaper('p1');
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/daily/papers/p1/read');
		});

		it("should allow the read longer than the server does", async function () {
			// The backend gives one read 90s and the API layer's default is 30s,
			// so the default would abort a read the server then finishes anyway
			// -- and the row is written either way, so the user is shown a
			// failure that did not happen.
			captureRequests({ id: 'p1', read_status: 'done' });
			await Zotero.Pharos.Daily.readPaper('p1');
			assert.equal(requests[0].options.timeout, Zotero.Pharos.Daily.READ_TIMEOUT);
			assert.isAbove(Zotero.Pharos.Daily.READ_TIMEOUT, 90000);
		});

		it("should escape a paper id", async function () {
			captureRequests({ id: 'a/b' });
			await Zotero.Pharos.Daily.readPaper('a/b');
			assert.equal(requests[0].path, '/api/daily/papers/a%2Fb/read');
		});

		it("should resolve a failed reading rather than reject", async function () {
			// The backend catches ReaderError, writes the row and returns 200. A
			// client that reads a resolved promise as success shows a spinner
			// turning into an unchanged card with nothing said.
			captureRequests({ id: 'p1', read_status: 'error', read_error: 'provider timed out' });
			var paper = await Zotero.Pharos.Daily.readPaper('p1');
			assert.equal(paper.read_status, 'error');
			assert.equal(paper.read_error, 'provider timed out');
		});

		it("should return the paper scored for this caller", async function () {
			// Replaced wholesale rather than merged: relevance and the overall
			// score are computed against the caller's own directions, so keeping
			// any field from the old row mixes two readers' rubrics.
			captureRequests({
				id: 'p1',
				read_status: 'done',
				summary_zh: '一段中文速览。',
				scores: { relevance: 0.9, recommendation: 0.82 },
				score_recommendation: 0.82,
			});
			var paper = await Zotero.Pharos.Daily.readPaper('p1');
			assert.equal(paper.scores.relevance, 0.9);
			assert.equal(paper.score_recommendation, 0.82);
		});

		it("should let a 503 through with its status", async function () {
			// Nothing was attempted and nothing was written. The fix is
			// configuration, so the view must not offer a retry.
			captureRequests(() => {
				throw httpError(503, 'no chat provider is configured; set PHAROS_CHAT_PROVIDER');
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.readPaper('p1'));
			assert.equal(e.status, 503);
		});

		it("should let a 404 through with its status", async function () {
			captureRequests(() => {
				throw httpError(404, 'paper not found');
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.readPaper('gone'));
			assert.equal(e.status, 404);
		});

		it("should not swallow a sign-out", async function () {
			captureRequests(() => {
				throw new Zotero.Pharos.API.SignedOutError();
			});
			var e = await getPromiseError(Zotero.Pharos.Daily.readPaper('p1'));
			assert.instanceOf(e, Zotero.Pharos.API.SignedOutError);
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

		// The note is filed as a child of a real preprint carrying the PDF, in a
		// container Zotero convention treats as user-authored. Without this line
		// a model's inference is indistinguishable from the reader's own notes
		// six months later, and the model only ever saw the abstract.
		it("should say a model wrote it, and name the model", function () {
			var note = Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'done',
				summary_zh: 'The summary.',
				read_model: 'some-model-v2',
			});
			assert.include(note, 'some-model-v2');
			assert.include(note, Zotero.ftl.formatValueSync(
				'pharos-daily-note-provenance', { model: 'some-model-v2' }));
		});

		it("should still disclose the model when the model is unknown", function () {
			var note = Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'done',
				summary_zh: 'The summary.',
			});
			assert.include(note,
				Zotero.getString('pharos-daily-note-provenance-unknown'));
		});

		// A "\n"-joined footer renders welded into one sentence, because this is
		// HTML and a newline is whitespace -- which would run the disclosure into
		// the unrelated matched-direction line.
		it("should keep the disclosure in its own paragraph", function () {
			var note = Zotero.Pharos.Daily.buildNote({
				title: 'A paper',
				read_status: 'done',
				summary_zh: 'The summary.',
				read_model: 'm',
				matched_domain: 'robotics',
			});
			assert.notInclude(note, '\n' + Zotero.getString('pharos-daily-matched'));
			assert.include(note, '</p>');
			assert.isAbove(note.split('<p>').length, 2);
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

		it("should not call the backend's import endpoint", async function () {
			// POST /api/daily/papers/{id}/import files a paper in the WEB
			// library. 导入文库 on the desktop means Zotero's library, and
			// pointing this at the endpoint would put it somewhere the reader,
			// the annotations and the citation machinery cannot see.
			captureRequests({});
			await Zotero.Pharos.Daily.saveToLibrary({
				id: 'd5', arxiv_id: '2601.00002', title: 'Local', authors: [], read_status: 'pending',
			});
			assert.lengthOf(requests, 0);
		});
	});

	describe("#findInLibrary()", function () {
		it("should find what saveToLibrary already saved", async function () {
			var saved = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'f1', arxiv_id: '2602.11111', title: 'Already Here', authors: [],
				read_status: 'pending',
			});
			var found = await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '2602.11111' });
			assert.ok(found);
			assert.equal(found.id, saved.id);
		});

		it("should not find a paper that was never saved", async function () {
			assert.isNull(await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '2602.99999' }));
		});

		it("should not find a trashed item", async function () {
			// Someone who binned it should be offered the import again rather
			// than told it is already there.
			var item = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'f2', arxiv_id: '2602.22222', title: 'Binned', authors: [],
				read_status: 'pending',
			});
			item.deleted = true;
			await item.saveTx();
			assert.isNull(await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '2602.22222' }));
		});

		it("should answer null for a paper with no arXiv id", async function () {
			assert.isNull(await Zotero.Pharos.Daily.findInLibrary({ title: 'No id' }));
			assert.isNull(await Zotero.Pharos.Daily.findInLibrary(null));
		});

		it("should never call the backend", async function () {
			// imported_paper_id names a row in the web library, on a shared
			// record, blanked for anyone who does not own it. The desktop's
			// answer is local or it is wrong.
			captureRequests({});
			await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '2602.11111' });
			assert.lengthOf(requests, 0);
		});

		it("should fail off rather than take the list down", async function () {
			// This drives a badge next to every card. A search that throws must
			// cost the badge, not the render.
			var stub = sinon.stub(Zotero.Search.prototype, 'search').rejects(new Error('boom'));
			try {
				assert.isNull(await Zotero.Pharos.Daily.findInLibrary({ arxiv_id: '2602.11111' }));
			}
			finally {
				stub.restore();
			}
		});
	});

	describe("the string table", function () {
		// Every id below is a VALUE message. Zotero.getString() reads pharos.ftl
		// through formatValueSync, falls through to the .properties bundle when
		// there is no value, and throws there in en-US -- so a missing id, or an
		// id that only carries attributes, fails here rather than in front of a
		// user.
		const VALUE_IDS = [
			'pharos-daily-menu',
			'pharos-daily-heading',
			'pharos-daily-error',
			'pharos-daily-loading',
			'pharos-daily-matched',
			'pharos-daily-highlight-contribution',
			'pharos-daily-highlight-innovation',
			'pharos-daily-highlight-method',
			'pharos-daily-highlight-results',
			'pharos-daily-rail-head',
			'pharos-daily-rail-unreachable',
			'pharos-daily-rail-no-directions',
			'pharos-daily-rail-no-match',
			'pharos-daily-rail-empty',
			'pharos-daily-refresh',
			'pharos-daily-refreshing',
			'pharos-daily-refresh-tooltip',
			'pharos-daily-filter-all',
			'pharos-daily-sort-score',
			'pharos-daily-sort-time',
			'pharos-daily-last-run-failed',
			'pharos-daily-refresh-busy',
			'pharos-daily-no-llm',
			'pharos-daily-no-llm-tooltip',
			'pharos-daily-read-unavailable',
			'pharos-daily-provider-none',
			'pharos-daily-no-directions-title',
			'pharos-daily-no-directions-desc',
			'pharos-daily-disabled-title',
			'pharos-daily-disabled-desc',
			'pharos-daily-firstuse-title',
			'pharos-daily-firstuse-desc',
			'pharos-daily-nomatch-title',
			'pharos-daily-nomatch-desc',
			'pharos-daily-directions-label',
			'pharos-daily-open-settings',
			'pharos-daily-edit-directions',
			'pharos-daily-refetch',
			'pharos-daily-day-unswept',
			'pharos-daily-day-unswept-hint',
			'pharos-daily-day-nomatch',
			'pharos-daily-unreachable-hint',
			'pharos-daily-detail-empty',
			'pharos-daily-pending',
			'pharos-daily-read',
			'pharos-daily-reading',
			'pharos-daily-retry',
			'pharos-daily-retrying',
			'pharos-daily-read-failed',
			'pharos-daily-score-tooltip',
			'pharos-daily-open',
			'pharos-daily-open-pdf',
			'pharos-daily-none',
			'pharos-daily-import',
			'pharos-daily-importing',
			'pharos-daily-imported',
			'pharos-daily-section-summary',
			'pharos-daily-section-highlights',
			'pharos-daily-section-scores',
			'pharos-daily-section-info',
			'pharos-daily-section-abstract',
			'pharos-daily-score-relevance',
			'pharos-daily-score-recency',
			'pharos-daily-score-popularity',
			'pharos-daily-score-quality',
			'pharos-daily-score-recommendation',
			'pharos-daily-score-relevance-hint',
			'pharos-daily-score-recency-hint',
			'pharos-daily-score-popularity-hint',
			'pharos-daily-score-quality-hint',
			'pharos-daily-score-recommendation-hint',
			'pharos-daily-score-note',
			'pharos-daily-info-authors',
			'pharos-daily-info-direction',
			'pharos-daily-info-direction-hint',
			'pharos-daily-info-categories',
			'pharos-daily-info-keywords',
			'pharos-daily-info-keywords-hint',
			// Still read by Find Literature and Research Projects.
			'pharos-daily-save',
			'pharos-daily-saving',
			'pharos-daily-saved',
			'pharos-daily-save-failed',
			// Shown next to pharos-daily-unreachable-hint.
			'pharos-error-unreachable',
			'pharos-error-signed-out-detail',
		];

		it("should have every value message the module names", function () {
			for (let id of VALUE_IDS) {
				assertLocalized(id);
			}
		});

		it("should keep the window title attribute-only", function () {
			// Zotero.getString() cannot read an attribute, and falls through to a
			// bundle where no pharos-* id exists. pharos-daily-heading is the
			// value message that exists for exactly this reason.
			assert.notOk(Zotero.ftl.formatValueSync('pharos-daily-window'));
			assertLocalized('pharos-daily-heading');
		});

		it("should have dropped the ids the rebuild replaced", function () {
			// Left in place they would look serviceable to the next person and
			// name the emptiness the new pair now distinguishes.
			assert.notOk(Zotero.ftl.formatValueSync('pharos-daily-empty'));
			assert.notOk(Zotero.ftl.formatValueSync('pharos-daily-unread'));
		});

		it("should substitute every argument the view passes", function () {
			// Fluent leaves an unknown argument as a literal "{ $name }", so a
			// renamed placeholder shows the reader the source of the string.
			// Zotero.getString() cannot catch this: handed params it reads the
			// .properties bundle, where none of these ids exist -- which is
			// itself a throw in en-US.
			function formatted(id, args) {
				let value = Zotero.ftl.formatValueSync(id, args);
				assert.isNotEmpty(value, `missing string ${id}`);
				assert.notInclude(value, '$', `unsubstituted argument in ${id}`);
				return value;
			}

			assert.include(formatted('pharos-daily-count', { count: 12 }), '12');
			assert.include(formatted('pharos-daily-date-pending', { count: 3 }), '3');
			assert.include(
				formatted('pharos-daily-sweep-progress', { total: 9, read: 4 }), '9'
			);
			assert.include(
				formatted('pharos-daily-last-run-failed-detail', { error: 'arXiv said no' }),
				'arXiv said no'
			);
			assert.include(
				formatted('pharos-daily-refresh-failed', { error: 'arXiv said no' }),
				'arXiv said no'
			);
			assert.include(
				formatted('pharos-daily-provider', { name: 'deepseek', model: 'deepseek-chat' }),
				'deepseek-chat'
			);
			assert.include(
				formatted('pharos-daily-day-nomatch-hint', { fetched: 120 }), '120'
			);
			assert.include(
				formatted('pharos-daily-read-failed-detail', { error: 'timed out' }), 'timed out'
			);
			assert.include(
				formatted('pharos-daily-retry-failed', { error: 'timed out' }), 'timed out'
			);
			assert.include(
				formatted('pharos-daily-import-failed', { error: 'disk full' }), 'disk full'
			);
		});

		it("should keep the separator on the sweep-failed fragment", function () {
			// It is appended to pharos-daily-sweep-progress, so the leading " · "
			// is part of the value. Fluent discards whitespace after "=", which
			// is why it is written as a string literal; losing it welds the two
			// numbers together.
			var value = Zotero.ftl.formatValueSync('pharos-daily-sweep-failed', { failed: 2 })
				// Fluent may wrap placeables in bidi isolation marks.
				.replace(/[⁨⁩]/g, '');
			assert.isTrue(value.startsWith(' '), `no leading space: ${JSON.stringify(value)}`);
			assert.include(value, '2');
		});
	});

	describe("the digest window", function () {
		it("should open and report being signed out", async function () {
			// Opening it at all covers the localization ids in pharosDaily.xhtml:
			// one missing from the window's own resource list stops it appearing,
			// and the failure is a bare `undefined` rejection with no stack.
			//
			// Deliberately shallow beyond that. The view's markup is rebuilt
			// separately from this module, so anything asserted about a
			// particular element here would pin someone else's DOM.
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosDaily.xhtml");
			try {
				// loadWindow resolves on the load event, but init() is async and
				// keeps going after it. Asserting without waiting passed alone and
				// failed whenever the machine was busier.
				if (win.Zotero_Pharos_Daily && win.Zotero_Pharos_Daily.initialized) {
					await win.Zotero_Pharos_Daily.initialized;
				}
				assert.ok(win.document.documentElement);
				// Signed out, so it must say so rather than sit blank.
				assert.isNotEmpty(win.document.documentElement.textContent.trim());
			}
			finally {
				win.close();
			}
		});
	});

	// Everything below fails SILENTLY when it regresses: no throw, no logged
	// error, and a screen that looks entirely plausible to anyone who is not the
	// person whose scroll position, selection or error message it just moved.
	describe("the digest panel", function () {
		function detailOf(doc) {
			return doc.getElementById('pharos-dv-detail');
		}

		it("should not rebuild the detail panel a poll did not change", async function () {
			// _tick() renders twice per list refresh for the length of a sweep.
			// Restoring scrollTop is not enough on its own: a text selection
			// cannot be restored once its nodes are gone, so the subtree has to
			// survive, which is what node identity here pins.
			await withPanel(digest({
				papers: [makePaper({ id: 'p1' }),
					makePaper({ id: 'p2', arxiv_id: '2601.00002', title: 'Another' })],
			}), async function (win, doc, view) {
				view.selectPaper('p1');
				var detail = detailOf(doc);
				var body = detail.firstChild;
				assert.ok(body, 'nothing was drawn for the selected paper');

				view._render();
				view._render();
				assert.strictEqual(detail.firstChild, body,
					'the panel was torn down and rebuilt with nothing changed');
			});
		});

		it("should not rebuild the date rail a poll did not change", async function () {
			// Same defect and same cause as the detail panel above, with a
			// different casualty: replaceChildren() discards the focused node, so
			// a user arrowing through dates while a sweep runs loses focus twice a
			// second. Node identity is what pins it -- a rebuild that reproduces
			// the same markup passes every content assertion and still moves
			// focus, which is why this asserts identity rather than text.
			await withPanel(digest({}), async function (win, doc, view) {
				var rail = doc.getElementById('pharos-dv-rail');
				var row = rail.firstChild;
				assert.ok(row, 'nothing was drawn in the rail');

				view._render();
				view._render();
				assert.strictEqual(rail.firstChild, row,
					'the rail was torn down and rebuilt with nothing changed');
			});
		});

		it("should rebuild the date rail when a date's pending count moves",
			async function () {
				// The skip above is only safe if the signature covers everything
				// the rail draws. This is the half that cannot be shown by a test
				// that changes nothing: a pending count that moves without the
				// rail noticing is silent -- the rail simply goes on showing
				// yesterday's number, and looks entirely healthy doing it.
				var pending = 2;
				await withPanel(digest({
					papers: [makePaper({ id: 'p1' })],
					extra: function (method, path) {
						if (path == '/api/daily/dates') {
							return [{ date: DAY, total: 5, read: 0, pending, failed: 0 }];
						}
						return undefined;
					},
				}), async function (win, doc, view) {
					var rail = doc.getElementById('pharos-dv-rail');
					assert.include(rail.textContent, '2', 'the fixture count was not drawn');

					pending = 3;
					await view.loadDates();
					view._render();
					assert.include(rail.textContent, '3',
						'the rail kept a stale count -- the signature does not '
						+ 'cover pending, so the skip hides real changes');
				});
			});

		it("should keep the reader's place, and start a new paper at the top",
			async function () {
				// Two separate contracts, and only the second one needs code:
				// replaceChildren() and the append that follows it run inside one
				// synchronous call, so no layout ever sees the box empty and the
				// offset survives a rebuild by itself -- which is right for a poll
				// redrawing the same paper and wrong for the paper the reader just
				// clicked, who would otherwise land halfway down it.
				var abstract = 'A very long abstract sentence. '.repeat(1200);
				await withPanel(digest({
					papers: [makePaper({ id: 'p1', abstract }),
						makePaper({ id: 'p2', arxiv_id: '2601.00002', title: 'Another', abstract })],
					extra: (method, path) => {
						if (path == '/api/daily/papers/p1/read') {
							return makePaper({
								id: 'p1',
								abstract,
								read_status: 'done',
								summary_zh: '一段中文速览。',
							});
						}
						return undefined;
					},
				}), async function (win, doc, view) {
					view.selectPaper('p1');
					var detail = detailOf(doc);
					detail.scrollTop = 120;
					assert.isAbove(detail.scrollTop, 0, 'the panel does not scroll to test');
					var scroll = detail.scrollTop;

					// A poll during a sweep, then a read: both redraw this paper.
					view._render();
					assert.equal(detail.scrollTop, scroll);
					await view.read({ id: 'p1' });
					assert.include(detail.textContent, '一段中文速览。');
					assert.equal(detail.scrollTop, scroll);

					// A different paper is a different document.
					view.selectPaper('p2');
					assert.equal(detail.scrollTop, 0);
				});
			});

		it("should not show one paper's read failure on another paper's card",
			async function () {
				// A read is allowed 180s, which is a long time to ask someone to
				// sit on one card. Held module-wide, the failure of A's read was
				// printed under B as if B had failed -- a specific, plausible,
				// false statement.
				var fail;
				var pending = new Promise((resolve, reject) => {
					fail = reject;
				});
				await withPanel(digest({
					papers: [makePaper({ id: 'p1' }),
						makePaper({ id: 'p2', arxiv_id: '2601.00002', title: 'Another' })],
					extra: (method, path) => (path == '/api/daily/papers/p1/read'
						? pending
						: undefined),
				}), async function (win, doc, view) {
					var detail = detailOf(doc);
					view.selectPaper('p1');
					var reading = view.read({ id: 'p1' });
					assert.include(detail.textContent,
						Zotero.getString('pharos-daily-reading'));

					// The reader moves on while A is still in flight.
					view.selectPaper('p2');
					assert.notInclude(detail.textContent,
						Zotero.getString('pharos-daily-reading'),
						"B's button claims a read that is running on A");
					assert.isFalse(detail.querySelector('.pharos-dv-d-ghost').disabled,
						"B's button is disabled by a read running on A");

					fail(httpError(500, 'ZZQQ the provider exploded'));
					await reading;
					assert.notInclude(detail.textContent, 'ZZQQ',
						"A's failure was printed under B");

					// And it is still there when they go back to the paper it
					// actually belongs to.
					view.selectPaper('p1');
					assert.include(detail.textContent, 'ZZQQ');
				});
			});

		it("should not show one paper's import failure on another paper's card",
			async function () {
				var fail;
				var pending = new Promise((resolve, reject) => {
					fail = reject;
				});
				var stub = sinon.stub(Zotero.Pharos.Daily, 'saveToLibrary').returns(pending);
				try {
					await withPanel(digest({
						papers: [makePaper({ id: 'p1' }),
							makePaper({ id: 'p2', arxiv_id: '2601.00002', title: 'Another' })],
					}), async function (win, doc, view) {
						var detail = detailOf(doc);
						view.selectPaper('p1');
						var importing = view.importToLibrary({ id: 'p1' });
						assert.include(detail.textContent,
							Zotero.getString('pharos-daily-importing'));

						view.selectPaper('p2');
						assert.notInclude(detail.textContent,
							Zotero.getString('pharos-daily-importing'));
						assert.isFalse(doc.getElementById('pharos-dv-import').disabled);

						fail(new Error('ZZQQ the disk is full'));
						await importing;
						assert.notInclude(detail.textContent, 'ZZQQ');

						view.selectPaper('p1');
						assert.include(detail.textContent, 'ZZQQ');
					});
				}
				finally {
					stub.restore();
				}
			});

		it("should stop saying In Library once the item is trashed", async function () {
			// findInLibrary() deliberately does not match a trashed item --
			// someone who binned it should be offered the import again -- but the
			// memo in front of it had no invalidation, and this document is
			// loaded once and never reloaded, so the answer stood for the rest of
			// the application session with the button disabled.
			var item = await Zotero.Pharos.Daily.saveToLibrary({
				id: 'seed', arxiv_id: '2603.12345', title: 'Trashable', authors: [],
				read_status: 'pending',
			});
			await withPanel(digest({
				papers: [makePaper({ id: 'p1', arxiv_id: '2603.12345' })],
			}), async function (win, doc, view) {
				view.selectPaper('p1');
				var button = () => doc.getElementById('pharos-dv-import');
				// Bounded: waitForCallback's `timeout` is in SECONDS, and its
				// default of 10000 would sit here for the best part of three hours
				// if this ever regressed.
				await waitForCallback(() => button().disabled, 50, 5);
				assert.include(button().textContent,
					Zotero.getString('pharos-daily-imported'));

				item.deleted = true;
				await item.saveTx();

				await waitForCallback(() => !button().disabled, 50, 5);
				assert.include(button().textContent,
					Zotero.getString('pharos-daily-import'));
			});
		});

		it("should let the keyboard select a paper", async function () {
			// The dates beside it are real buttons and the toolbar controls are
			// too, so without this the one thing a keyboard could not reach was
			// the paper -- and with it, the whole detail panel.
			await withPanel(digest({
				papers: [makePaper({ id: 'p1' }),
					makePaper({ id: 'p2', arxiv_id: '2601.00002', title: 'Another' })],
			}), async function (win, doc) {
				var cards = doc.querySelectorAll('.pharos-dv-card');
				assert.lengthOf(cards, 2);
				// The second card in DOM order, whichever paper the sort put
				// there -- asserting on a title would pin the tie-break instead.
				var card = cards[1];
				assert.equal(card.getAttribute('tabindex'), '0');
				assert.isFalse(card.classList.contains('is-selected'));

				card.dispatchEvent(new win.KeyboardEvent('keydown',
					{ key: 'Enter', bubbles: true }));
				assert.isTrue(card.classList.contains('is-selected'));
				assert.include(detailOf(doc).textContent,
					card.querySelector('.pharos-dv-card-title').textContent);
			});
		});

		it("should refetch the list at a third of the poll cadence", async function () {
			// _tick() increments its counter BEFORE testing it, so LIST_EVERY is
			// a divisor and not a count of skipped ticks. At 2 the list rebuilt
			// on every second tick -- half the cadence the constant's own comment
			// promises the next person who tunes it.
			await withPanel(digest({ papers: [makePaper()] }),
				async function (win, doc, view) {
					requests.length = 0;
					await view._tick();
					await view._tick();
					assert.lengthOf(requests.filter(r => r.path == '/api/daily/dates'), 0);
					await view._tick();
					assert.lengthOf(requests.filter(r => r.path == '/api/daily/dates'), 1);
				});
		});

		it("should offer the service hint only for a request nothing answered",
			async function () {
				// Decided from the exception, not by comparing _errorText()'s
				// output with a localized string: that made control flow depend on
				// the wording of a message a translator is free to change.
				await withPanel(digest({
					extra: (method, path) => {
						if (/^\/api\/daily\/\d{4}-\d{2}-\d{2}$/.test(path)) {
							throw httpError(0, 'connection refused');
						}
						return undefined;
					},
				}), function (win, doc) {
					assert.include(doc.getElementById('pharos-dv-list').textContent,
						Zotero.getString('pharos-daily-unreachable-hint'));
				});

				await withPanel(digest({
					extra: (method, path) => {
						if (/^\/api\/daily\/\d{4}-\d{2}-\d{2}$/.test(path)) {
							throw httpError(500, 'ZZQQ the database is on fire');
						}
						return undefined;
					},
				}), function (win, doc) {
					var list = doc.getElementById('pharos-dv-list');
					assert.include(list.textContent, 'ZZQQ');
					assert.notInclude(list.textContent,
						Zotero.getString('pharos-daily-unreachable-hint'));
				});
			});
	});
});
