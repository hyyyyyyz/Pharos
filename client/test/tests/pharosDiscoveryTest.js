describe("Zotero.Pharos.Discovery", function () {
	var origRequest = null;
	var origHasCredentials = null;
	var requests;

	/**
	 * Swap out the API layer and record what the module would have sent.
	 *
	 * Most of what matters here is invisible from the return value: a key the
	 * backend declares extra="forbid" is a 422, and a search that inherits the
	 * API layer's 30s default is aborted while three providers are still working.
	 *
	 * `responder` is either the value to resolve with, or a function called with
	 * (method, path, options) whose return value resolves and whose throw
	 * rejects.
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

	/** A LiteratureResultOut, with every field the window reads. */
	function makeResult(overrides) {
		return Object.assign({
			id: 'r1',
			search_id: 's1',
			title: 'Attention Is Still All You Need',
			authors: ['Ada Lovelace', 'Alan Turing'],
			abstract: 'ZZQQ We propose a novel transformer variant that compresses the KV cache.',
			year: 2026,
			venue: null,
			doi: null,
			url: 'https://example.org/paper',
			pdf_url: null,
			sources: ['arxiv'],
			source_ids: { arxiv: '2601.00001' },
			citation_count: null,
			rank: 1,
			analysis_mode: 'rules',
			analysis_model: null,
			analysis_warning: 'Summarised from the abstract without a model.',
			summary_zh: '',
			contribution: '',
			core_trick: '',
			method: '',
			results: '',
			limitations: '',
			created_at: '2026-08-01T10:00:00+00:00',
		}, overrides);
	}

	/** A LiteratureSearchOut. */
	function makeSearch(overrides) {
		return Object.assign({
			id: 's1',
			project_id: null,
			query: 'kv cache compression',
			sources: ['arxiv', 'openalex'],
			status: 'complete',
			result_count: 1,
			errors: {},
			created_at: '2026-08-01T10:00:00+00:00',
			completed_at: '2026-08-01T10:00:20+00:00',
			results: [makeResult()],
		}, overrides);
	}

	/**
	 * Open the window with the API layer stubbed and a token pretended.
	 *
	 * hasCredentials() is faked rather than a token actually stored: storing one
	 * goes through OSKeyStore, which is a real keychain on some machines, and
	 * nothing in these tests needs the token itself.
	 */
	async function withWindow(responder, fn) {
		captureRequests(responder);
		origHasCredentials = Zotero.Pharos.API.hasCredentials;
		Zotero.Pharos.API.hasCredentials = () => true;
		var win = await loadWindow("chrome://zotero/content/pharosDiscovery.xhtml");
		try {
			await win.Zotero_Pharos_Discovery.initialized;
			await fn(win, win.document);
		}
		finally {
			win.close();
			restoreRequests();
		}
	}

	after(async function () {
		await Zotero.Pharos.API.setToken(null);
	});

	afterEach(function () {
		restoreRequests();
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

		// The card shows the model; the note outlives the card.
		it("should name the model that produced an AI reading", function () {
			var note = Zotero.Pharos.Discovery.buildNote({
				title: 'A paper',
				summary_zh: 'A reading.',
				analysis_mode: 'llm',
				analysis_model: 'some-model-v2',
			});
			assert.include(note, 'some-model-v2');
		});

		// analyze_result overwrites every field EXCEPT limitations, which keeps
		// the cue-matched sentence rules extraction copied out of the English
		// abstract. Crediting the whole list to the model is wrong about that
		// one line.
		it("should disown the limitations row it did not write", function () {
			var note = Zotero.Pharos.Discovery.buildNote({
				title: 'A paper',
				summary_zh: 'A reading.',
				analysis_mode: 'llm',
				analysis_model: 'm',
				limitations: 'A sentence lifted from the abstract.',
			});
			assert.include(note,
				Zotero.getString('pharos-discovery-note-limitations'));
		});

		it("should not claim a limitations caveat when there is no such row", function () {
			var note = Zotero.Pharos.Discovery.buildNote({
				title: 'A paper',
				summary_zh: 'A reading.',
				analysis_mode: 'llm',
				analysis_model: 'm',
			});
			assert.notInclude(note,
				Zotero.getString('pharos-discovery-note-limitations'));
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

	describe("#isRules()", function () {
		// The single most dangerous predicate in the module. Getting it backwards
		// puts an English abstract extract under an "AI reading (Chinese)" chip
		// and nothing throws.
		it("should call an llm reading an llm reading", function () {
			assert.isFalse(Zotero.Pharos.Discovery.isRules({ analysis_mode: 'llm' }));
		});

		it("should call anything that is not exactly llm rules", function () {
			assert.isTrue(Zotero.Pharos.Discovery.isRules({ analysis_mode: 'rules' }));
			// A newer backend, a partially built object, a missing field: all of
			// them have to fail closed. Testing for == 'rules' would let each one
			// through as a model reading.
			assert.isTrue(Zotero.Pharos.Discovery.isRules({ analysis_mode: 'heuristic' }));
			assert.isTrue(Zotero.Pharos.Discovery.isRules({}));
			assert.isTrue(Zotero.Pharos.Discovery.isRules(null));
		});

		it("should pick the chip that matches", function () {
			assert.equal(Zotero.Pharos.Discovery.modeStringID({ analysis_mode: 'llm' }),
				'pharos-discovery-mode-llm');
			assert.equal(Zotero.Pharos.Discovery.modeStringID({ analysis_mode: 'rules' }),
				'pharos-discovery-mode-rules');
			assert.equal(Zotero.Pharos.Discovery.modeStringID({}),
				'pharos-discovery-mode-rules');
		});
	});

	describe("#trick()", function () {
		it("should never fall back to the abstract", function () {
			// The bug this replaces: `summary_zh || abstract`. summary_zh is ""
			// for every rules row, which is every row of a fresh search, so the
			// raw English abstract was rendered in the box a Chinese AI summary
			// occupies, with the same class and styling.
			var trick = Zotero.Pharos.Discovery.trick(makeResult({
				abstract: 'ZZQQ this sentence must not appear.',
				core_trick: '',
			}));
			assert.equal(trick.state, 'empty');
			assert.notInclude(trick.text, 'ZZQQ');
			assert.isNotEmpty(trick.text);
		});

		it("should never fall back to summary_zh", function () {
			var trick = Zotero.Pharos.Discovery.trick(makeResult({
				analysis_mode: 'llm',
				summary_zh: 'ZZQQ the whole-paper summary.',
				core_trick: 'The key idea itself.',
			}));
			assert.equal(trick.state, 'ai');
			assert.equal(trick.text, 'The key idea itself.');
		});

		it("should keep the rules-extracted sentence rather than discarding it", function () {
			// rule_summary() always fills core_trick, falling back to the first
			// sentence and then to the cleaned title, so this is real content.
			// The web client throws it away and prints "not generated yet".
			var trick = Zotero.Pharos.Discovery.trick(makeResult({
				core_trick: '  We compress the KV cache.  ',
			}));
			assert.equal(trick.state, 'extracted');
			assert.equal(trick.text, 'We compress the KV cache.');
		});

		it("should distinguish a model that returned nothing from one never called", function () {
			var never = Zotero.Pharos.Discovery.trick(makeResult({ core_trick: '' }));
			var nothing = Zotero.Pharos.Discovery.trick(makeResult({
				analysis_mode: 'llm', core_trick: '   ',
			}));
			assert.equal(never.state, 'empty');
			assert.equal(nothing.state, 'empty');
			assert.notEqual(never.text, nothing.text);
		});
	});

	describe("#analysisWarning()", function () {
		it("should surface a rules warning", function () {
			assert.equal(
				Zotero.Pharos.Discovery.analysisWarning(makeResult({
					analysis_warning: 'No model was called.',
				})),
				'No model was called.'
			);
		});

		it("should not surface one on an llm reading", function () {
			// The two fields never co-occur on the wire, but a stale warning left
			// on an upgraded row must not claim the model was never called.
			assert.equal(Zotero.Pharos.Discovery.analysisWarning(makeResult({
				analysis_mode: 'llm', analysis_warning: 'stale',
			})), '');
		});
	});

	describe("#sourceLabel()", function () {
		it("should use the providers' own spelling", function () {
			assert.equal(Zotero.Pharos.Discovery.sourceName('arxiv'), 'arXiv');
			assert.equal(Zotero.Pharos.Discovery.sourceName('openalex'), 'OpenAlex');
		});

		it("should pass an unknown provider through rather than blanking it", function () {
			assert.equal(Zotero.Pharos.Discovery.sourceName('crossref'), 'crossref');
		});

		it("should join with the locale's own separator", function () {
			var label = Zotero.Pharos.Discovery.sourceLabel(['arxiv', 'openalex']);
			assert.include(label, 'arXiv');
			assert.include(label, 'OpenAlex');
			// en-US joins with ", ", which Fluent would strip without the string
			// literal in the .ftl. zh-CN uses an ideographic comma.
			assert.notEqual(label, 'arXivOpenAlex');
		});

		it("should say so rather than render an empty string", function () {
			assert.isNotEmpty(Zotero.Pharos.Discovery.sourceLabel([]));
			assert.isNotEmpty(Zotero.Pharos.Discovery.sourceLabel(null));
			assert.isNotEmpty(Zotero.Pharos.Discovery.sourceLabel('arxiv'));
		});
	});

	describe("#statusStringID()", function () {
		it("should name each of the four states", function () {
			for (let status of Zotero.Pharos.Discovery.STATUSES) {
				assert.equal(Zotero.Pharos.Discovery.statusStringID(status),
					'pharos-discovery-status-' + status);
				assertLocalized('pharos-discovery-status-' + status);
			}
		});

		it("should fail closed on a status it does not know", function () {
			// An unrecognised status must never read as success.
			assert.equal(Zotero.Pharos.Discovery.statusStringID('queued'),
				'pharos-discovery-status-error');
			assert.equal(Zotero.Pharos.Discovery.statusStringID(undefined),
				'pharos-discovery-status-error');
		});

		it("should call a persisted running row unfinished, not in progress", function () {
			// POST /api/discovery/search is synchronous and commits only on
			// success, so a stored `running` row is a request that died. Wording
			// it as "searching" would invent a progress indication for work that
			// is not happening.
			var value = Zotero.getString('pharos-discovery-status-running');
			assert.isNotEmpty(value);
			assertLocalized('pharos-discovery-status-running-hint');
		});
	});

	describe("#searchProblem()", function () {
		function problem(form) {
			var p = Zotero.Pharos.Discovery.searchProblem(Object.assign({
				query: 'kv cache', sources: ['arxiv'], limit: 20,
			}, form));
			return p && p.id;
		}

		it("should let a well-formed search through", function () {
			assert.isNull(Zotero.Pharos.Discovery.searchProblem({
				query: 'kv cache', sources: ['arxiv', 'openalex'], limit: 20,
			}));
		});

		it("should measure the trimmed query", function () {
			// Pydantic's min_length applies to the raw string while run_search
			// re-checks the trimmed one, so "  " passes the schema and fails the
			// service with a 400. Trimming first turns that into a form hint.
			assert.equal(problem({ query: '   ' }), 'pharos-discovery-need-query');
			assert.equal(problem({ query: ' a ' }), 'pharos-discovery-need-query');
			assert.equal(problem({ query: 'x'.repeat(501) }),
				'pharos-discovery-query-too-long');
		});

		it("should require a source", function () {
			assert.equal(problem({ sources: [] }), 'pharos-discovery-need-source');
			assert.equal(problem({ sources: null }), 'pharos-discovery-need-source');
		});

		it("should keep the limit inside what the backend accepts", function () {
			assert.equal(problem({ limit: 0 }), 'pharos-discovery-limit-range');
			assert.equal(problem({ limit: 51 }), 'pharos-discovery-limit-range');
			assert.equal(problem({ limit: 2.5 }), 'pharos-discovery-limit-range');
			assert.equal(problem({ limit: NaN }), 'pharos-discovery-limit-range');
		});
	});

	describe("#analysisFailure()", function () {
		it("should say the rules result survived a missing provider", function () {
			var failure = Zotero.Pharos.Discovery.analysisFailure(httpError(409, 'no reader'));
			assert.equal(failure.id, 'pharos-discovery-analyze-no-provider');
			assertLocalized(failure.id);
		});

		it("should say the rules result survived a provider failure", function () {
			var failure = Zotero.Pharos.Discovery.analysisFailure(httpError(503, 'upstream'));
			assert.equal(failure.id, 'pharos-discovery-analyze-provider-failed');
			assertLocalized(failure.id);
		});

		it("should explain a result with no abstract", function () {
			// An OpenAlex record whose inverted abstract index was missing has
			// abstract == "", and the web client falls through to the raw detail.
			assert.equal(
				Zotero.Pharos.Discovery.analysisFailure(httpError(400, 'no abstract')).id,
				'pharos-discovery-analyze-no-abstract'
			);
		});

		it("should fall back to the server's own reason", function () {
			var failure = Zotero.Pharos.Discovery.analysisFailure(httpError(500, 'boom'));
			assert.equal(failure.id, 'pharos-discovery-analyze-failed');
			assert.equal(failure.args.error, 'boom');
		});

		it("should recognise being signed out and being unreachable", function () {
			assert.equal(
				Zotero.Pharos.Discovery.analysisFailure(new Zotero.Pharos.API.SignedOutError()).id,
				'pharos-error-signed-out-detail'
			);
			assert.equal(
				Zotero.Pharos.Discovery.analysisFailure(httpError(0, 'status code 0')).id,
				'pharos-error-unreachable'
			);
		});
	});

	describe("#search()", function () {
		it("should trim the query and outlast the providers", async function () {
			captureRequests(makeSearch());
			await Zotero.Pharos.Discovery.search('  kv cache  ');
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'POST');
			assert.equal(requests[0].path, '/api/discovery/search');
			assert.equal(requests[0].options.body.query, 'kv cache');
			assert.equal(requests[0].options.timeout, 180000);
		});

		it("should omit project_id rather than send null", async function () {
			// SearchCreate is extra="forbid"; a key outside
			// {query, project_id, sources, limit} is a 422 rather than an
			// ignored field.
			captureRequests(makeSearch());
			await Zotero.Pharos.Discovery.search('kv cache');
			assert.notProperty(requests[0].options.body, 'project_id');
			assert.notProperty(requests[0].options.body, 'projectID');
			assert.sameMembers(Object.keys(requests[0].options.body),
				['query', 'sources', 'limit']);
		});

		it("should send project_id when a project is linked", async function () {
			captureRequests(makeSearch());
			await Zotero.Pharos.Discovery.search('kv cache', { projectID: 'p1' });
			assert.equal(requests[0].options.body.project_id, 'p1');
		});

		it("should not turn a run where every provider died into an exception",
			async function () {
				// The backend answers 201 with status "error" and the reasons,
				// deliberately, so that the attempt is still a saved record.
				captureRequests(makeSearch({
					status: 'error',
					result_count: 0,
					results: [],
					errors: { arxiv: 'timed out', openalex: 'HTTP 500' },
				}));
				var search = await Zotero.Pharos.Discovery.search('kv cache');
				assert.equal(search.status, 'error');
				assert.lengthOf(Object.keys(search.errors), 2);
			});
	});

	describe("#getSearches()", function () {
		it("should ask for the whole history by default", async function () {
			captureRequests([]);
			await Zotero.Pharos.Discovery.getSearches();
			assert.equal(requests[0].path, '/api/discovery/searches');
		});

		it("should scope to a project when asked", async function () {
			captureRequests([]);
			await Zotero.Pharos.Discovery.getSearches({ projectID: 'p 1/2' });
			assert.equal(requests[0].path,
				'/api/discovery/searches?project_id=' + encodeURIComponent('p 1/2'));
		});
	});

	describe("#addToProject()", function () {
		it("should file a whole selection in one pass", async function () {
			captureRequests({});
			var outcome = await Zotero.Pharos.Discovery.addToProject({
				projectID: 'p1',
				resultIDs: ['a', 'b', 'c'],
			});
			assert.deepEqual(outcome, { added: 3, skipped: 0, failed: 0, failedIDs: [] });
			assert.lengthOf(requests, 3);
		});

		it("should not re-send results the project already has", async function () {
			captureRequests({});
			var outcome = await Zotero.Pharos.Discovery.addToProject({
				projectID: 'p1',
				resultIDs: ['a', 'b', 'c'],
				existingResultIDs: new Set(['a', 'c']),
			});
			assert.equal(outcome.added, 1);
			assert.equal(outcome.skipped, 2);
			assert.lengthOf(requests, 1);
		});

		it("should resolve with the counts rather than reject on a partial failure",
			async function () {
				// Rejecting would lose the selection, which is exactly what the
				// user needs kept in order to retry the ones that failed.
				captureRequests((method, path, options) => {
					if (options.body.result_id == 'b') {
						throw httpError(503, 'upstream');
					}
					return {};
				});
				var outcome = await Zotero.Pharos.Discovery.addToProject({
					projectID: 'p1',
					resultIDs: ['a', 'b'],
				});
				assert.equal(outcome.added, 1);
				assert.equal(outcome.failed, 1);
				assert.deepEqual(outcome.failedIDs, ['b']);
			});

		it("should resolve even when nothing landed", async function () {
			captureRequests(() => {
				throw httpError(500, 'boom');
			});
			var outcome = await Zotero.Pharos.Discovery.addToProject({
				projectID: 'p1',
				resultIDs: ['a', 'b'],
			});
			assert.equal(outcome.added, 0);
			assert.equal(outcome.failed, 2);
		});

		it("should throw for a caller that supplied nothing to do", function () {
			// A programming error rather than a user state; the window guards
			// both first and shows a form hint.
			assert.throws(() => Zotero.Pharos.Discovery.addToProject({
				projectID: '', resultIDs: ['a'],
			}));
			assert.throws(() => Zotero.Pharos.Discovery.addToProject({
				projectID: 'p1', resultIDs: [],
			}));
		});
	});

	describe("the discovery window", function () {
		it("should open and report being signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			var win = await loadWindow("chrome://zotero/content/pharosDiscovery.xhtml");
			try {
				await win.Zotero_Pharos_Discovery.initialized;
				assert.isNotEmpty(win.document.getElementById('pharos-ds-hint').textContent);
				assert.isTrue(win.document.getElementById('pharos-discovery-search').disabled);
			}
			finally {
				win.close();
			}
		});

		it("should offer the first-use explanation before any run", async function () {
			await withWindow((method, path) => {
				if (path == '/api/projects') {
					return [];
				}
				return [];
			}, function (win, doc) {
				assert.isFalse(doc.getElementById('pharos-ds-empty').hidden);
				assert.isNotEmpty(doc.getElementById('pharos-ds-empty-title').textContent);
				assert.isTrue(doc.getElementById('pharos-ds-run').hidden);
			});
		});

		it("should not dress a rules extraction up as a Chinese AI summary",
			async function () {
				// The regression that shipped: a rules result rendered
				// `summary_zh || abstract`, so the raw English abstract sat in the
				// box an AI summary occupies with nothing to tell them apart.
				await withWindow((method, path) => {
					if (path == '/api/discovery/search') {
						return makeSearch();
					}
					return [];
				}, async function (win, doc) {
					doc.getElementById('pharos-discovery-query').value = 'kv cache';
					await win.Zotero_Pharos_Discovery.search();

					var card = doc.querySelector('.pharos-ds-card');
					assert.ok(card, 'no card rendered');

					var trick = card.querySelector('.pharos-ds-trick-v');
					assert.isNotEmpty(trick.textContent);
					assert.notInclude(trick.textContent, 'ZZQQ');

					// The abstract has its own labelled block, and only there.
					var abstract = card.querySelector('.pharos-ds-abs-v');
					assert.include(abstract.textContent, 'ZZQQ');

					// The chip is present on every card, never conditional: a
					// missing one is what made the two modes indistinguishable.
					var mode = card.querySelector('.pharos-ds-mode');
					assert.isNotEmpty(mode.textContent);
					assert.isFalse(mode.classList.contains('is-ai'));

					// The warning is visible text, not a tooltip, and the raw
					// server sentence stays inspectable in the title.
					var warn = card.querySelector('.pharos-ds-warn');
					assert.ok(warn, 'no analysis warning rendered');
					assert.isNotEmpty(warn.querySelector('.pharos-ds-warn-text').textContent);
					assert.include(warn.getAttribute('title'), 'without a model');

					// Nothing claims a model read it.
					assert.notOk(card.querySelector('.pharos-ds-model'));
				});
			});

		it("should name the model once one has read the paper", async function () {
			await withWindow((method, path) => {
				if (path == '/api/discovery/search') {
					return makeSearch({
						results: [makeResult({
							analysis_mode: 'llm',
							analysis_model: 'qwen-max',
							analysis_warning: null,
							summary_zh: '一段中文摘要。',
							core_trick: '把 KV cache 压成低秩。',
						})],
					});
				}
				return [];
			}, async function (win, doc) {
				doc.getElementById('pharos-discovery-query').value = 'kv cache';
				await win.Zotero_Pharos_Discovery.search();

				var card = doc.querySelector('.pharos-ds-card');
				assert.isTrue(card.querySelector('.pharos-ds-mode').classList.contains('is-ai'));
				assert.include(card.querySelector('.pharos-ds-model').textContent, 'qwen-max');
				assert.include(card.querySelector('.pharos-ds-trick-v').textContent, 'KV cache');
				// The AI variant is the only one styled as such.
				assert.isTrue(card.querySelector('.pharos-ds-trick').classList.contains('is-ai'));
				// An llm reading has nothing to warn about.
				assert.notOk(card.querySelector('.pharos-ds-warn'));
			});
		});

		it("should render a run where every source failed as a saved run",
			async function () {
				await withWindow((method, path) => {
					if (path == '/api/discovery/search') {
						return makeSearch({
							status: 'error',
							result_count: 0,
							results: [],
							errors: { arxiv: 'timed out', openalex: 'HTTP 500' },
						});
					}
					return [];
				}, async function (win, doc) {
					doc.getElementById('pharos-discovery-query').value = 'kv cache';
					await win.Zotero_Pharos_Discovery.search();

					assert.isFalse(doc.getElementById('pharos-ds-run').hidden);
					assert.isTrue(doc.getElementById('pharos-ds-status')
						.classList.contains('is-error'));

					// One row per provider: joining them detaches each reason
					// from the name it belongs to.
					var rows = doc.querySelectorAll('.pharos-ds-error-row');
					assert.lengthOf(rows, 2);
					assert.include(rows[0].textContent, 'arXiv');

					// Still an empty state, but the heading says it failed rather
					// than that nothing matched.
					assert.isFalse(doc.getElementById('pharos-ds-empty').hidden);
					assert.equal(doc.getElementById('pharos-ds-empty-title').textContent,
						Zotero.getString('pharos-discovery-error'));
					assert.isNotEmpty(doc.getElementById('pharos-ds-notice').textContent);
				});
			});

		it("should explain a run that never finished without pretending it is running",
			async function () {
				await withWindow((method, path) => {
					if (path == '/api/discovery/search') {
						return makeSearch({ status: 'running' });
					}
					return [];
				}, async function (win, doc) {
					doc.getElementById('pharos-discovery-query').value = 'kv cache';
					await win.Zotero_Pharos_Discovery.search();

					assert.isFalse(doc.getElementById('pharos-ds-runwarn').hidden);
					assert.isTrue(doc.getElementById('pharos-ds-status')
						.classList.contains('is-running'));
				});
			});

		it("should spell out an archived project in the picker", async function () {
			// Services.prompt.select took `projects.map(p => p.name)` and dropped
			// the status, so a paper could be filed into an archived project with
			// nothing on screen to say so.
			await withWindow((method, path) => {
				if (path == '/api/projects') {
					return [
						{ id: 'p1', name: 'Live Work', status: 'active', sources: [] },
						{ id: 'p2', name: 'Old Work', status: 'archived', sources: [] },
					];
				}
				return [];
			}, function (win, doc) {
				var options = Array.from(
					doc.getElementById('pharos-ds-file-project').options
				).map(o => o.textContent);
				assert.include(options.join('\n'), 'Live Work');
				var archived = options.find(text => text.includes('Old Work'));
				assert.notEqual(archived, 'Old Work', 'archived suffix dropped');
			});
		});

		it("should list past runs from one request and reopen one without another",
			async function () {
				// GET /api/discovery/searches serialises through the same
				// search_out() as the detail endpoint, so the rail already holds
				// every result. A per-item fetch would be a wasted round trip.
				await withWindow((method, path) => {
					if (path == '/api/discovery/searches') {
						return [makeSearch({ id: 's9', query: 'earlier question' })];
					}
					return [];
				}, async function (win, doc) {
					var items = doc.querySelectorAll('.pharos-ds-hitem');
					assert.lengthOf(items, 1);
					assert.include(items[0].textContent, 'earlier question');

					var before = requests.length;
					items[0].click();
					await Zotero.Promise.delay(50);
					assert.equal(requests.length, before, 'reopening refetched the run');
					assert.equal(doc.getElementById('pharos-ds-run-query').textContent,
						'earlier question');
					assert.lengthOf(doc.querySelectorAll('.pharos-ds-card'), 1);
				});
			});

		it("should refuse to search on a query the backend would reject",
			async function () {
				await withWindow(() => [], async function (win, doc) {
					doc.getElementById('pharos-discovery-query').value = ' a ';
					var before = requests.length;
					await win.Zotero_Pharos_Discovery.search();
					assert.equal(requests.length, before, 'sent a query that is too short');
					assert.isNotEmpty(
						doc.getElementById('pharos-ds-form-error').textContent
					);
				});
			});
	});
});
