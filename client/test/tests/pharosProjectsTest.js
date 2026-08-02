describe("Zotero.Pharos.Projects", function () {
	// Every note this file makes is standalone, so it lands as a top-level item
	// in the library. Left behind, they change what later suites see when they
	// select or enumerate items -- three itemPane tests failed only when run
	// after this file. Child notes and attachments, which the other Pharos
	// suites create, hang off their parent and do not have that effect.
	var created = [];
	var stubs = [];

	async function track(promise) {
		let item = await promise;
		created.push(item.id);
		return item;
	}

	afterEach(function () {
		while (stubs.length) {
			stubs.pop().restore();
		}
	});

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
			assert.isTrue(Zotero.Pharos.Projects.canAdvance({
				stage: 'discovery', status: 'active',
			}));
		});

		it("should refuse to advance past the last stage", function () {
			assert.isFalse(Zotero.Pharos.Projects.canAdvance({
				stage: 'complete', status: 'active',
			}));
		});
	});

	describe("the string table", function () {
		// Drift here is silent: the window shows a blank stage label and, in
		// en-US, one unresolved id throws out of getString and takes the whole
		// render down with it.
		it("should know every stage the backend defines", function () {
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-stage-' + stage),
					`missing label for stage ${stage}`
				);
			}
		});

		it("should have a short label and a note for every stage", function () {
			// The timeline node and the subtitle under 研究路径. Both are new, and
			// both are built by concatenation from the same stage list.
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-stage-' + stage + '-short'),
					`missing short label for stage ${stage}`
				);
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-stage-' + stage + '-note'),
					`missing note for stage ${stage}`
				);
			}
		});

		it("should have a label for every artifact type", function () {
			for (let type of Zotero.Pharos.Projects.ARTIFACT_TYPES) {
				assert.isNotEmpty(
					// Every underscore, not just the first: experiment_plan is the
					// only type with one today, and a replace() without /g is what
					// made this worth centralising.
					Zotero.getString('pharos-projects-type-' + type.replace(/_/g, '-')),
					`missing label for type ${type}`
				);
			}
		});

		it("should keep the project state chips out of the artifact status namespace", function () {
			// statusLabel() builds its id by prefixing pharos-projects-status-.
			// A project state minted in that namespace would resolve through it
			// and put 进行中 on an artifact chip.
			assert.isNotEmpty(Zotero.getString('pharos-projects-state-active'));
			assert.isNotEmpty(Zotero.getString('pharos-projects-state-archived'));
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

		it("should survive a record type this build has never seen", async function () {
			// The label helpers are bare Zotero.getString() calls, and an id Fluent
			// cannot resolve THROWS in en-US (intl.js:178-180) while returning the
			// bare id in zh-CN. So a stage, type or status from a newer backend took
			// the whole export down -- in en-US only, which is not the locale this
			// is developed in. The throw is forced here so the test fails in either.
			var orig = Zotero.getString;
			Zotero.getString = function (...args) {
				if (args[0] == 'pharos-projects-type-a-new-type') {
					throw new Error('Localized string not available for ' + args[0]);
				}
				return orig.apply(Zotero, args);
			};
			try {
				var note = await track(Zotero.Pharos.Projects.saveArtifactAsNote({
					id: 'a6',
					title: 'From a newer backend',
					type: 'a_new_type',
					stage: 'claims',
					status: 'ready',
					body: 'Written anyway.',
				}, project));
				var html = note.getNote();
				assert.include(html, 'From a newer backend');
				assert.include(html, 'Written anyway.');
				// The key itself, which is at least a legible placeholder. What must
				// not happen is the note never being written at all.
				assert.include(html, 'a_new_type');
			}
			finally {
				Zotero.getString = orig;
			}
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
		var NOTICE = 'Pharos records research; it does not run experiments.';

		function project(overrides) {
			return Object.assign({
				id: 'p1',
				name: 'Long-context KV cache',
				description: 'What the project is about.',
				research_question: 'Does compression hurt recall?',
				stage: 'analysis',
				status: 'active',
				created_at: '2026-01-02T03:04:05',
				updated_at: '2026-02-03T04:05:06',
				automation_notice: NOTICE,
				source_count: 2,
				artifact_count: 3,
				sources: [
					{
						id: 's1',
						note: 'The baseline the recall claim rests on.',
						added_at: '2026-01-05T00:00:00',
						paper: { title: 'kv-cache.pdf', page_count: 12, deleted_at: null },
						result: {
							id: 'r1',
							title: 'KV Cache Compression for Long Context',
							authors: ['A One', 'B Two', 'C Three', 'D Four'],
							year: 2025,
							venue: 'NeurIPS',
							url: 'https://example.invalid/kv',
							sources: ['arxiv', 'openalex'],
							analysis_mode: 'llm',
							analysis_model: 'a-test-model',
							summary_zh: '模型写的中文速览。',
							core_trick: '分层量化。',
							analysis_warning: 'Only the abstract was available.',
						},
					},
					{
						id: 's2',
						note: null,
						added_at: '2026-01-06T00:00:00',
						paper: null,
						result: {
							id: 'r2',
							title: 'A Paper Nobody Read',
							authors: [],
							sources: ['openalex'],
							analysis_mode: 'rules',
							analysis_model: null,
							summary_zh: '',
							// What rule_summary() produces when no cue matched: the
							// paper's own cleaned title, restated.
							core_trick: 'A Paper Nobody Read',
						},
					},
				],
				artifacts: [
					{
						id: 'a1',
						stage: 'analysis',
						type: 'result',
						status: 'verified',
						title: 'Recall drops three points',
						body: '95% accuracy.',
						created_at: '2026-01-20T00:00:00',
						updated_at: '2026-02-01T00:00:00',
					},
					{
						id: 'a2',
						stage: 'ideation',
						type: 'hypothesis',
						status: 'draft',
						title: 'Layered quantisation should hold recall',
						body: '',
						created_at: '2026-01-09T00:00:00',
						updated_at: '2026-01-10T00:00:00',
					},
					{
						// Never edited, which is every record until someone PATCHes
						// it: ProjectArtifact.updated_at defaults to NULL.
						id: 'a3',
						stage: 'planning',
						type: 'experiment_plan',
						status: 'draft',
						title: 'Freeze the metrics before running anything',
						body: '',
						created_at: '2026-01-08T00:00:00',
						updated_at: null,
					},
				],
			}, overrides);
		}

		function stubBackend(projects) {
			stubs.push(sinon.stub(Zotero.Pharos.API, 'hasCredentials').returns(true));
			let list = sinon.stub(Zotero.Pharos.Projects, 'list').callsFake(
				async () => JSON.parse(JSON.stringify(projects)));
			let get = sinon.stub(Zotero.Pharos.Projects, 'get').callsFake(
				async id => JSON.parse(JSON.stringify(projects.find(p => p.id == id))));
			stubs.push(list, get);
			return { list, get };
		}

		function stub(name, fake) {
			let s = sinon.stub(Zotero.Pharos.Projects, name).callsFake(fake);
			stubs.push(s);
			return s;
		}

		/** An html:input rebuilt on every render keeps its draft in the module,
		 *  which only an `input` event updates. */
		function type(win, el, value) {
			el.value = value;
			el.dispatchEvent(new win.Event('input'));
		}

		async function open() {
			var win = await loadWindow("chrome://zotero/content/pharosProjects.xhtml");
			// loadWindow resolves on the load event, but init() is async and keeps
			// going after it. Asserting without waiting passes alone and fails
			// whenever the machine is busier.
			await win.Zotero_Pharos_Projects.initialized;
			return win;
		}

		it("should open and report being signed out", async function () {
			await Zotero.Pharos.API.setToken(null);
			var win = await open();
			try {
				var doc = win.document;
				// The window's contract for the no-token state: the polite region
				// says why, the write affordances are off, and neither the list
				// nor the desk pretends to have content.
				assert.isNotEmpty(doc.getElementById('pharos-pv-status').textContent);
				assert.isTrue(doc.getElementById('pharos-pv-new').disabled);
				assert.isTrue(doc.getElementById('pharos-pv-show-archived').disabled);
				assert.isEmpty(doc.getElementById('pharos-pv-list').children);
				assert.isEmpty(doc.getElementById('pharos-pv-desk').children);
			}
			finally {
				win.close();
			}
		});

		it("should show the automation notice as its own callout inside the records panel",
			async function () {
				// The two honesty fixes, pinned. The notice used to be a
				// .pharos-daily-pending at the TOP of the window: the lowest
				// contrast text in the sheet and the same treatment as the
				// "暂无内容。" placeholder, so it read as "nothing here" -- and it
				// sat far enough from the records that a result reading "95%
				// accuracy" could appear with no caveat anywhere near it.
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					var notice = doc.querySelector('.pharos-pv-automation');
					assert.ok(notice, "the automation notice is rendered");
					// Verbatim. Never through _fmt, never truncated, never a title=.
					assert.equal(notice.textContent, NOTICE);
					assert.notInclude(notice.className, 'pharos-daily');
					assert.equal(notice.getAttribute('role'), 'note');

					var panel = notice.closest('.pharos-pv-panel');
					assert.ok(panel, "sits inside a panel");
					assert.isTrue(panel.classList.contains('is-artifacts'),
						"and that panel is the records panel, not the top of the window");

					// Immediately above the records, so no record can be read
					// without it.
					assert.ok(notice.nextElementSibling);
					assert.isTrue(
						notice.nextElementSibling.classList.contains('pharos-pv-artifact-list'),
						"nothing comes between the notice and the first record"
					);

					// Out of the top of the desk: the header is what opens now.
					var desk = doc.getElementById('pharos-pv-desk');
					assert.isFalse(desk.firstElementChild.contains(notice));
				}
				finally {
					win.close();
				}
			});

		it("should load and apply its own stylesheet", async function () {
			// Five silent failures in one assertion block. A stylesheet that is
			// not linked, a [hidden] with no rule behind it (scss/base has no
			// global one, so an element toggled with .hidden = true stays on
			// screen), a notice that is not sticky, a notice painted in the old
			// low-contrast italic, and a sticky notice with a see-through
			// background all fail with nothing logged anywhere.
			stubBackend([project()]);
			var win = await open();
			try {
				var notice = win.document.querySelector('.pharos-pv-automation');
				var style = win.getComputedStyle(notice);
				assert.equal(style.position, 'sticky');
				assert.notEqual(style.fontStyle, 'italic');
				assert.notEqual(style.backgroundColor, 'rgba(0, 0, 0, 0)',
					"the amber callout, not a bare run of text");
				// OPAQUE. --pharos-pv-warn-soft is a ~14% wash, and nothing under
				// this element is opaque either -- .pharos-pv-panel declares no
				// background and .pharos-pv-artifact-card is padding only -- so a
				// translucent background here let record titles and bodies scroll
				// visibly through the one element whose whole purpose is to stay
				// legible above a record reading "95% accuracy". Only manifests
				// once a stage has enough records to scroll, so nothing catches it.
				assert.notInclude(style.backgroundColor, 'rgba(',
					"a sticky notice needs an opaque background");

				var error = win.document.getElementById('pharos-pv-error');
				assert.isTrue(error.hidden);
				assert.equal(win.getComputedStyle(error).display, 'none');
			}
			finally {
				win.close();
			}
		});

		it("should distinguish a model's reading from a rules extraction", async function () {
			// analysis_mode, analysis_model, sources, summary_zh, core_trick and
			// analysis_warning are all in the payload and none was rendered, so
			// the two were indistinguishable on screen.
			stubBackend([project()]);
			var win = await open();
			try {
				var cards = win.document.querySelectorAll('.pharos-pv-source-card');
				assert.lengthOf(cards, 2);

				// The chip says the MODE, in words. It printed result.analysis_model
				// before, so 「AI 深读」 appeared only when the backend had recorded
				// NO model, and the normal case was a bare `deepseek-chat` in a row
				// of lowercase wire ids -- read as a third retrieval source beside
				// arxiv and openalex, with the fact that a model wrote the paragraph
				// below never stated in words anywhere on the screen.
				// RESEARCH_WORKFLOW.md §10 item 4 asks for the mode to be shown.
				var llm = cards[0].querySelector('.pharos-pv-analysis');
				assert.ok(llm);
				assert.equal(llm.textContent, Zotero.getString('pharos-analysis-mode-llm'));
				assert.isTrue(llm.classList.contains('is-ai'));
				// The model is the footnote to it, and not a second capsule.
				var model = cards[0].querySelector('.pharos-pv-analysis-model');
				assert.ok(model, "the model is still named");
				assert.include(model.textContent, 'a-test-model');
				assert.notInclude(llm.textContent, 'a-test-model');
				// Display names, never the raw wire ids: sourceName() exists
				// precisely so no UI prints "arxiv".
				assert.deepEqual(
					Array.from(cards[0].querySelectorAll('.pharos-pv-source-src'),
						el => el.textContent),
					['arXiv', 'OpenAlex']
				);
				assert.equal(
					cards[0].querySelector('.pharos-pv-analysis-warning').textContent,
					'Only the abstract was available.'
				);
				assert.include(cards[0].textContent, '模型写的中文速览。');
				var trick = cards[0].querySelector('.pharos-pv-source-insight.is-ai');
				assert.ok(trick, "a model's core idea keeps the accent treatment");
				assert.include(trick.textContent, '分层量化。');
				// The desktop's own author line, which the web omits entirely.
				assert.include(cards[0].querySelector('.pharos-pv-source-meta').textContent,
					'et al.');
				assert.ok(cards[0].querySelector('.pharos-pv-source-paper'));

				var rules = cards[1].querySelector('.pharos-pv-analysis');
				assert.ok(rules);
				assert.equal(rules.textContent, Zotero.getString('pharos-analysis-mode-rules'));
				assert.isFalse(rules.classList.contains('is-ai'));
				assert.isNull(cards[1].querySelector('.pharos-pv-analysis-model'),
					"there is no model to name");

				// The disclosure is a visible block ABOVE the content, in the words
				// 文献探索 uses -- and it is the only sentence in this window that
				// says the other half of it: no full text was downloaded or read.
				var warn = cards[1].querySelector('.pharos-pv-analysis-warning');
				assert.ok(warn, "a rules summary says so rather than passing as a reading");
				assert.include(warn.textContent,
					Zotero.getString('pharos-discovery-mode-rules-detail'));
				// And it names where to get a model reading. The control that
				// does it lives only in 文献探索, so this window has to say which
				// window rather than name a button that is not on this screen.
				assert.include(warn.textContent,
					Zotero.getString('pharos-projects-source-rules-where'));
				// pharos-discovery-rules-note is note BODY copy, and it tells the
				// reader to press 「精读」 -- a control that exists only in the other
				// window, where it is called 生成核心思路.
				assert.notInclude(cards[1].textContent,
					Zotero.getString('pharos-discovery-rules-note'));

				// core_trick on a rules row is a sentence cut out of the English
				// abstract, or -- as here -- the paper's own title restated. Never
				// the accent this sheet reserves for a model's reading.
				var extracted = cards[1].querySelector('.pharos-pv-source-insight.is-extracted');
				assert.ok(extracted, "and the extraction is drawn as an extraction");
				assert.isFalse(extracted.classList.contains('is-ai'));
				assert.isNull(cards[1].querySelector('.pharos-pv-source-insight.is-ai'));
			}
			finally {
				win.close();
			}
		});

		it("should read a source with no analysis mode as a rules extraction", async function () {
			// Discovery.isRules() is `!= 'llm'` on purpose. This card's own test
			// was `== 'rules'` and it failed OPEN: a mode a newer backend added, or
			// a payload that reached this render path without the field, drew
			// 核心思路 with no disclaimer and no chip at all -- a rules extraction
			// with ZERO provenance rather than a mislabelled one, which is worse,
			// because the reader is handed nothing to distrust. Silent in every
			// locale, and unreachable only while the Pydantic Literal holds.
			var payload = project();
			delete payload.sources[0].result.analysis_mode;
			delete payload.sources[0].result.analysis_model;
			payload.sources[0].result.summary_zh = '';
			stubBackend([payload]);
			var win = await open();
			try {
				var card = win.document.querySelector('.pharos-pv-source-card');
				var chip = card.querySelector('.pharos-pv-analysis');
				assert.ok(chip, "the chip is never conditional on a field being set");
				assert.equal(chip.textContent, Zotero.getString('pharos-analysis-mode-rules'));
				assert.isFalse(chip.classList.contains('is-ai'));
				assert.isNull(card.querySelector('.pharos-pv-analysis-model'));

				var warn = card.querySelector('.pharos-pv-analysis-warning');
				assert.ok(warn);
				assert.include(warn.textContent,
					Zotero.getString('pharos-discovery-mode-rules-detail'));
				// And it names where to get a model reading. The control that
				// does it lives only in 文献探索, so this window has to say which
				// window rather than name a button that is not on this screen.
				assert.include(warn.textContent,
					Zotero.getString('pharos-projects-source-rules-where'));
				// The server's own English sentence stays inspectable without being
				// the only surface the fact has.
				assert.equal(warn.title, 'Only the abstract was available.');

				assert.isNull(card.querySelector('.pharos-pv-source-insight.is-ai'),
					"nothing on this card may read as a model's own reading");
			}
			finally {
				win.close();
			}
		});

		it("should draw the nine-stage path with per-stage counts", async function () {
			stubBackend([project()]);
			var win = await open();
			try {
				var doc = win.document;
				var nodes = doc.querySelectorAll('.pharos-pv-stage');
				assert.lengthOf(nodes, Zotero.Pharos.Projects.STAGES.length);
				var current = doc.querySelectorAll('.pharos-pv-stage.is-current');
				assert.lengthOf(current, 1, "exactly one stage is where the project is");
				assert.equal(
					current[0],
					nodes[Zotero.Pharos.Projects.STAGES.indexOf('analysis')]
				);
				// Everything before it is past; nothing after it is.
				assert.lengthOf(doc.querySelectorAll('.pharos-pv-stage.is-past'),
					Zotero.Pharos.Projects.STAGES.indexOf('analysis'));
				// The rewind control, which the desktop had no way to perform.
				assert.ok(doc.getElementById('pharos-pv-stage-select'));
				assert.ok(doc.getElementById('pharos-pv-stage-save'));
				assert.ok(doc.getElementById('pharos-pv-advance'));
			}
			finally {
				win.close();
			}
		});

		it("should filter the records to the viewed stage without moving the project",
			async function () {
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					let titles = () => Array.from(
						doc.querySelectorAll('.pharos-pv-artifact-title'),
						el => el.textContent
					);
					assert.deepEqual(titles(), ['Recall drops three points']);

					win.Zotero_Pharos_Projects.viewStage('ideation');
					assert.deepEqual(titles(), ['Layered quantisation should hold recall']);
					// Browsing history must not rewind the project itself.
					assert.isTrue(doc.querySelectorAll('.pharos-pv-stage')[
						Zotero.Pharos.Projects.STAGES.indexOf('analysis')
					].classList.contains('is-current'));
					assert.isTrue(doc.querySelectorAll('.pharos-pv-stage')[
						Zotero.Pharos.Projects.STAGES.indexOf('ideation')
					].classList.contains('is-viewed'));

					win.Zotero_Pharos_Projects.viewStage('drafting');
					assert.isEmpty(titles());
					// The stage-specific empty copy, not a generic placeholder.
					assert.include(
						doc.querySelector('.pharos-pv-panel.is-artifacts').textContent,
						Zotero.getString('pharos-projects-artifacts-empty-desc')
					);
				}
				finally {
					win.close();
				}
			});

		it("should count the records it is actually showing", async function () {
			// The head paired a kicker naming the VIEWED stage with the project's
			// whole-project total, directly above a list filtered to that one
			// stage -- so it could read 「文献探索 · 12 条研究记录」 over two cards,
			// and nothing in the row separated the scope of the one from the
			// other. The web renders viewedArtifacts.length here.
			stubBackend([project()]);
			var win = await open();
			try {
				var doc = win.document;
				let head = () => doc.querySelector(
					'.pharos-pv-panel.is-artifacts .pharos-pv-panel-count').textContent;
				let rows = () => doc.querySelectorAll(
					'.pharos-pv-panel.is-artifacts .pharos-pv-artifact-card').length;
				let expected = () => Zotero.ftl.formatValueSync(
					'pharos-projects-artifacts', { count: rows() });

				assert.equal(rows(), 1, "the project has three records, one here");
				assert.equal(head(), expected());

				win.Zotero_Pharos_Projects.viewStage('drafting');
				assert.equal(rows(), 0);
				assert.equal(head(), expected());
			}
			finally {
				win.close();
			}
		});

		it("should date a record that has never been edited", async function () {
			// ProjectArtifact.updated_at defaults to NULL and only a PATCH writes
			// it, so a freshly created record carried no timestamp at all and
			// there was no way to tell when it had been written. created_at is in
			// the same payload; the web falls back to it.
			stubBackend([project()]);
			var win = await open();
			try {
				var doc = win.document;
				win.Zotero_Pharos_Projects.viewStage('planning');
				var stamp = doc.querySelector('.pharos-pv-artifact-updated');
				assert.ok(stamp);
				assert.isNotEmpty(stamp.textContent);
				assert.include(stamp.textContent, '2026');
			}
			finally {
				win.close();
			}
		});

		it("should keep the project on screen while its detail request is in flight",
			async function () {
				// The list payload is not a stub: /api/projects returns full
				// ProjectOut rows with sources and artifacts eager-loaded, and the
				// window already holds them. select() used to null _project, and
				// _renderDesk() returns early for the loading state, so the entire
				// right-hand pane went blank on every project switch -- over data
				// the window had in hand. The web renders straight through.
				var second = project({ id: 'p2', name: 'Second Project' });
				var backend = stubBackend([project(), second]);
				var win = await open();
				try {
					var doc = win.document;
					var release;
					backend.get.callsFake(() => new Promise((resolve) => {
						release = () => resolve(JSON.parse(JSON.stringify(second)));
					}));

					var pending = win.Zotero_Pharos_Projects.select('p2');
					assert.equal(doc.querySelector('.pharos-pv-title').textContent,
						'Second Project', "the cached row is rendered, not a blank pane");
					assert.isNotEmpty(doc.querySelectorAll('.pharos-pv-stage'));
					assert.ok(doc.querySelector('.pharos-pv-panel.is-artifacts'));

					release();
					await pending;
					assert.equal(doc.querySelector('.pharos-pv-title').textContent,
						'Second Project');
				}
				finally {
					win.close();
				}
			});

		it("should not show the research question twice while it is being edited",
			async function () {
				// The edit form loads the same text into its own 研究问题 textarea
				// directly above, so an edit in progress sat next to the stale saved
				// copy of itself with nothing saying which was which.
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					assert.ok(doc.querySelector('.pharos-pv-question'));
					win.Zotero_Pharos_Projects.openEdit();
					assert.isNull(doc.querySelector('.pharos-pv-question'));
					assert.isNotEmpty(doc.querySelectorAll('.pharos-pv-edit textarea'));
					win.Zotero_Pharos_Projects.closeEdit();
					assert.ok(doc.querySelector('.pharos-pv-question'));
				}
				finally {
					win.close();
				}
			});

		it("should list projects as rows carrying stage, state and counts", async function () {
			stubBackend([project()]);
			var win = await open();
			try {
				var row = win.document.querySelector('.pharos-pv-item');
				assert.ok(row);
				assert.equal(row.querySelector('.pharos-pv-item-name').textContent,
					'Long-context KV cache');
				assert.isNotEmpty(row.querySelector('.pharos-pv-item-stage').textContent);
				assert.isNotEmpty(row.querySelector('.pharos-pv-item-meta').textContent);
				assert.ok(row.querySelector('.pharos-pv-state-dot'));
			}
			finally {
				win.close();
			}
		});

		it("should list archived projects by default and hide them on request",
			async function () {
				// The web's ProjectsView opens with 显示已归档 ON. The desktop opened
				// with it off, so the same account saw a full list in the browser
				// and, if everything it had was archived, 没有符合筛选的项目 over an
				// empty desk here. list(true) already fetched them either way.
				stubBackend([
					project(),
					project({ id: 'p2', name: 'A Shelved Project', status: 'archived' }),
				]);
				var win = await open();
				try {
					var doc = win.document;
					var toggle = doc.getElementById('pharos-pv-show-archived');
					assert.isTrue(toggle.checked);

					var rows = doc.querySelectorAll('.pharos-pv-item');
					assert.lengthOf(rows, 2);
					assert.isTrue(
						rows[1].querySelector('.pharos-pv-state-dot')
							.classList.contains('is-archived')
					);

					toggle.checked = false;
					toggle.dispatchEvent(new win.Event('change'));
					assert.lengthOf(doc.querySelectorAll('.pharos-pv-item'), 1);
					// The badge counts the ACCOUNT's projects, the way the web does,
					// so it stays 2 -- it is the only thing on screen that says the
					// filter is hiding something.
					assert.equal(doc.getElementById('pharos-pv-count').textContent, '2');
				}
				finally {
					win.close();
				}
			});

		it("should say which empty case it is in", async function () {
			// "还没有研究项目" and "everything is archived and the filter is hiding
			// it" used to be the same sentence -- and that sentence sent the
			// reader to the web app to do something this window can now do.
			stubBackend([project({ status: 'archived' })]);
			var win = await open();
			try {
				var doc = win.document;
				var toggle = doc.getElementById('pharos-pv-show-archived');
				toggle.checked = false;
				toggle.dispatchEvent(new win.Event('change'));
				assert.equal(
					doc.getElementById('pharos-pv-side-state').textContent,
					Zotero.getString('pharos-projects-none-matched')
				);
				assert.equal(doc.getElementById('pharos-pv-count').textContent, '1',
					"the account has one; the filter is what is hiding it");
			}
			finally {
				win.close();
			}
		});

		it("should offer a retry when the list cannot be loaded", async function () {
			stubs.push(sinon.stub(Zotero.Pharos.API, 'hasCredentials').returns(true));
			var error = new Error('Server said no.');
			error.status = 500;
			stubs.push(sinon.stub(Zotero.Pharos.Projects, 'list').rejects(error));
			var win = await open();
			try {
				var state = win.document.getElementById('pharos-pv-side-state');
				assert.isTrue(state.classList.contains('is-error'));
				// The backend's own detail, verbatim: those strings are written
				// for a person, and rewriting them means guessing which failure
				// occurred.
				assert.include(state.textContent, 'Server said no.');
				assert.include(state.textContent, Zotero.getString('pharos-projects-retry'));
			}
			finally {
				win.close();
			}
		});

		it("should say a transport failure is a transport failure", async function () {
			// request() hands back the raw Zotero.HTTP error for a connection that
			// never completed, whose message names the URL and "status code 0".
			// Only stream() maps that; the window has to.
			stubs.push(sinon.stub(Zotero.Pharos.API, 'hasCredentials').returns(true));
			stubs.push(sinon.stub(Zotero.Pharos.Projects, 'list')
				.rejects(new Error('...status code 0')));
			var win = await open();
			try {
				var state = win.document.getElementById('pharos-pv-side-state');
				assert.include(state.textContent, Zotero.getString('pharos-error-unreachable'));
				assert.include(state.textContent,
					Zotero.getString('pharos-daily-unreachable-hint'));
			}
			finally {
				win.close();
			}
		});

		describe("writing from the desktop", function () {
			it("should hold the create form until it has a name", async function () {
				// The backend answers 400 "name cannot be empty"; a round trip to
				// be told what the form already knows is a worse answer.
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					win.Zotero_Pharos_Projects.toggleCreate();
					var form = doc.getElementById('pharos-pv-create');
					assert.isFalse(form.hidden);
					var submit = form.querySelector('.pharos-pv-btn.is-primary');
					assert.isTrue(submit.disabled);
					type(win, doc.getElementById('pharos-pv-create-name'), 'A New Project');
					assert.isFalse(submit.disabled);
				}
				finally {
					win.close();
				}
			});

			it("should create a project when Enter is pressed in the name field",
				async function () {
					// HTML implicit submission needs either a submit button or
					// exactly one field that blocks it, and this form has neither:
					// every control is type="button" on purpose, and there are two
					// text inputs. So the `submit` handler is unreachable and Enter
					// did nothing at all -- no error, the key just did not work.
					stubBackend([project()]);
					var create = stub('create', async () => project({
						id: 'p9', name: 'Typed In',
					}));
					var win = await open();
					try {
						var doc = win.document;
						win.Zotero_Pharos_Projects.toggleCreate();
						var name = doc.getElementById('pharos-pv-create-name');
						type(win, name, 'Typed In');
						name.dispatchEvent(new win.KeyboardEvent('keydown', {
							key: 'Enter', bubbles: true, cancelable: true,
						}));
						assert.isTrue(create.calledOnce);
						assert.equal(create.firstCall.args[0].name, 'Typed In');
						// Let the create settle before the window goes away, or its
						// closing render runs against a torn-down document.
						await Zotero.Promise.delay(30);
					}
					finally {
						win.close();
					}
				});

			it("should not file the same paper into the library twice", async function () {
				// The saved state used to live on the button, and every _render()
				// rebuilds it -- an edit, an archive, a stage save, an inline
				// confirm, and the render inside _mutate()'s finally all do. Neither
				// call underneath de-duplicates: saveExternalPaper() does
				// `new Zotero.Item(itemType)` unconditionally, so the second click
				// wrote a second real item and both clicks reported success.
				stubBackend([project()]);
				var save = sinon.stub(Zotero.Pharos.Discovery, 'saveToLibrary').resolves({});
				stubs.push(save);
				var win = await open();
				try {
					var doc = win.document;
					let button = () => doc.querySelector(
						'.pharos-pv-source-card .pharos-pv-source-foot').lastElementChild;
					assert.equal(button().textContent, Zotero.getString('pharos-daily-save'));

					await win.Zotero_Pharos_Projects.saveSource(
						{ id: 's1', result: { id: 'r1' } }, button());
					assert.isTrue(save.calledOnce);

					// Any render at all rebuilds that foot.
					win.Zotero_Pharos_Projects.viewStage('ideation');
					assert.equal(button().textContent, Zotero.getString('pharos-daily-saved'));
					assert.isTrue(button().disabled);
					button().click();
					assert.isTrue(save.calledOnce, "a second click files no second copy");
				}
				finally {
					win.close();
				}
			});

			it("should not file the same record as a note twice", async function () {
				// saveArtifactAsNote() does `new Zotero.Item('note')` every time it
				// is called, so the duplicate is a second standalone note in the
				// user's library and only shows up in the item tree.
				stubBackend([project()]);
				var save = sinon.stub(Zotero.Pharos.Projects, 'saveArtifactAsNote').resolves({});
				stubs.push(save);
				var win = await open();
				try {
					var doc = win.document;
					let button = () => doc.querySelector(
						'.pharos-pv-artifact-card .pharos-pv-artifact-foot').lastElementChild;
					assert.equal(button().textContent,
						Zotero.getString('pharos-projects-save-note'));

					await win.Zotero_Pharos_Projects.saveArtifactNote(
						{ id: 'a1' }, project(), button());
					assert.isTrue(save.calledOnce);

					win.Zotero_Pharos_Projects.viewStage('analysis');
					assert.equal(button().textContent, Zotero.getString('pharos-daily-saved'));
					assert.isTrue(button().disabled);
					button().click();
					assert.isTrue(save.calledOnce, "a second click writes no second note");
				}
				finally {
					win.close();
				}
			});

			it("should apply the attributes-only placeholders", async function () {
				// A message that is only a .placeholder has no value, so
				// Zotero.getString() cannot read it at all and setting textContent
				// does nothing. Only data-l10n-id plus data-l10n-attrs applies it,
				// and getting that wrong leaves a bare unlabelled box.
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					win.Zotero_Pharos_Projects.toggleCreate();
					var form = doc.getElementById('pharos-pv-create');
					// Fluent translates on mutation, which has not run yet.
					await doc.l10n.translateFragment(form);
					for (let el of form.querySelectorAll('input[type="text"], textarea')) {
						assert.isNotEmpty(el.placeholder,
							`no placeholder on ${el.getAttribute('data-l10n-id')}`);
					}

					win.Zotero_Pharos_Projects.newArtifact();
					var editor = doc.querySelector('.pharos-pv-artifact-editor');
					await doc.l10n.translateFragment(editor);
					assert.isNotEmpty(editor.querySelector('input').placeholder);
					assert.isNotEmpty(editor.querySelector('textarea').placeholder);

					var select = doc.getElementById('pharos-pv-stage-select');
					await doc.l10n.translateFragment(select);
					assert.isNotEmpty(select.getAttribute('aria-label'));
				}
				finally {
					win.close();
				}
			});

			it("should clear a text field with \"\" rather than null", async function () {
				// patch_project model_dumps with exclude_none, so a null is DROPPED
				// rather than applied: update(id, {description: null}) succeeds and
				// changes nothing at all.
				stubBackend([project()]);
				var update = stub('update', async (id, patch) => Object.assign(
					project(), { description: patch.description }));
				var win = await open();
				try {
					win.Zotero_Pharos_Projects.openEdit();
					var areas = win.document.querySelectorAll('.pharos-pv-edit textarea');
					// name, research question, description -- the description is
					// the second textarea, the name being an input.
					type(win, areas[1], '');
					await win.Zotero_Pharos_Projects.saveProject();
					assert.isTrue(update.calledOnce);
					// And only the field that changed: an empty PATCH body is a
					// 400, and a full one would resend text nobody touched.
					assert.deepEqual(update.firstCall.args[1], { description: '' });
				}
				finally {
					win.close();
				}
			});

			it("should ask before a destructive action", async function () {
				stubBackend([project()]);
				var remove = stub('remove', async () => null);
				var win = await open();
				try {
					var doc = win.document;
					doc.querySelector('.pharos-pv-header-actions .is-danger-text').click();
					assert.ok(doc.querySelector('.pharos-pv-delete-confirm'),
						"the first click asks");
					assert.isFalse(remove.called, "and does not delete anything");
				}
				finally {
					win.close();
				}
			});

			it("should seed a new record from the stage being viewed", async function () {
				stubBackend([project()]);
				var win = await open();
				try {
					var doc = win.document;
					win.Zotero_Pharos_Projects.viewStage('planning');
					win.Zotero_Pharos_Projects.newArtifact();
					var editor = doc.querySelector('.pharos-pv-artifact-editor');
					assert.ok(editor);
					var selects = editor.querySelectorAll('select');
					assert.lengthOf(selects, 3);
					assert.equal(selects[0].value, 'planning');
					assert.equal(selects[1].value,
						Zotero.Pharos.Projects.DEFAULT_TYPE.planning);
					assert.equal(selects[2].value, 'draft');
					// Below the automation notice, so the caveat is above the
					// field the user is about to write a result into.
					assert.isTrue(editor.previousElementSibling
						.classList.contains('pharos-pv-automation'));
				}
				finally {
					win.close();
				}
			});

			it("should refetch the project after a record is written", async function () {
				// createArtifact returns only the new row, so source_count and
				// artifact_count on the cached project are now wrong -- and both
				// are rendered in the sidebar.
				var backend = stubBackend([project()]);
				var create = stub('createArtifact', async () => ({ id: 'a3' }));
				var win = await open();
				try {
					var doc = win.document;
					var gets = backend.get.callCount;
					win.Zotero_Pharos_Projects.newArtifact();
					type(win, doc.querySelector('.pharos-pv-artifact-editor input'),
						'A real result');
					await win.Zotero_Pharos_Projects.saveArtifact();
					assert.isTrue(create.calledOnce);
					assert.equal(backend.get.callCount, gets + 1);
				}
				finally {
					win.close();
				}
			});

			it("should render advance()'s own response rather than refetching", async function () {
				// project_out always serialises the eager-loaded sources and
				// artifacts. The comment this replaced claimed otherwise and paid
				// for a whole extra GET to work around something never true.
				var backend = stubBackend([project()]);
				stub('advance', async () => project({ stage: 'claims' }));
				var win = await open();
				try {
					var doc = win.document;
					var gets = backend.get.callCount;
					await win.Zotero_Pharos_Projects.advance();
					assert.equal(backend.get.callCount, gets);
					assert.isTrue(doc.querySelectorAll('.pharos-pv-stage')[
						Zotero.Pharos.Projects.STAGES.indexOf('claims')
					].classList.contains('is-current'));
					// An advance moves what the reader is looking at with it.
					assert.isTrue(doc.querySelectorAll('.pharos-pv-stage')[
						Zotero.Pharos.Projects.STAGES.indexOf('claims')
					].classList.contains('is-viewed'));
				}
				finally {
					win.close();
				}
			});

			it("should clear an evidence note with null, which is the one place null clears",
				async function () {
					// SourcePatch.note is str | None with NO default, and
					// patch_project_source passes body.note straight through. The
					// asymmetry with every other PATCH is deliberate on the
					// backend and must not be smoothed over here.
					stubBackend([project()]);
					var updateSource = stub('updateSource', async () => ({ id: 's1' }));
					var win = await open();
					try {
						var doc = win.document;
						win.Zotero_Pharos_Projects.editSourceNote(
							{ id: 's1', note: 'The baseline the recall claim rests on.' });
						type(win, doc.querySelector('.pharos-pv-note-editor textarea'), '   ');
						await win.Zotero_Pharos_Projects.saveSourceNote('s1');
						assert.isTrue(updateSource.calledOnce);
						assert.isNull(updateSource.firstCall.args[2]);
					}
					finally {
						win.close();
					}
				});

			it("should not offer advance on an archived project", async function () {
				stubBackend([
					project(),
					project({ id: 'p2', name: 'A Shelved Project', status: 'archived' }),
				]);
				var win = await open();
				try {
					var doc = win.document;
					await win.Zotero_Pharos_Projects.select('p2');
					assert.isTrue(doc.getElementById('pharos-pv-advance').disabled,
						"advance_project answers 409 for an archived project");
					// The stage select stays live: a PATCH only corrects recorded
					// metadata, and the backend allows it while archived.
					assert.isFalse(doc.getElementById('pharos-pv-stage-select').disabled);
					assert.include(doc.querySelector('.pharos-pv-state').textContent,
						Zotero.getString('pharos-projects-state-archived'));

					// And it stays on screen once the filter drops its row, because
					// 恢复 is the only way back and it lives here.
					var toggle = doc.getElementById('pharos-pv-show-archived');
					toggle.checked = false;
					toggle.dispatchEvent(new win.Event('change'));
					assert.isEmpty(doc.querySelectorAll('.pharos-pv-item.is-active'));
					assert.equal(doc.querySelector('.pharos-pv-title').textContent,
						'A Shelved Project');
				}
				finally {
					win.close();
				}
			});
		});
	});

	// Everything below depends on the amended Zotero.Pharos.Projects surface:
	// ARTIFACT_STATUSES, DEFAULT_TYPE, the five label helpers, and a canAdvance
	// that also reads status. Kept last so a run with -f still reports
	// everything above it.
	describe("the amended API surface", function () {
		it("should refuse to advance an archived project", function () {
			// advance_project answers 409 "Archived projects cannot advance;
			// reactivate the project first". Checking only the stage could not
			// misfire while archived projects were invisible; now that they are
			// listed, it enables a button that only ever 409s.
			assert.isFalse(Zotero.Pharos.Projects.canAdvance({
				stage: 'discovery', status: 'archived',
			}));
		});

		it("should know every artifact status the backend defines", function () {
			assert.isArray(Zotero.Pharos.Projects.ARTIFACT_STATUSES);
			for (let status of Zotero.Pharos.Projects.ARTIFACT_STATUSES) {
				assert.isNotEmpty(
					Zotero.getString('pharos-projects-status-' + status),
					`missing label for status ${status}`
				);
			}
		});

		it("should seed a default record type for every stage", function () {
			for (let stage of Zotero.Pharos.Projects.STAGES) {
				assert.include(
					Zotero.Pharos.Projects.ARTIFACT_TYPES,
					Zotero.Pharos.Projects.DEFAULT_TYPE[stage],
					`no default type for stage ${stage}`
				);
			}
		});

		it("should resolve every label through the module's own helpers", function () {
			// Centralised because the `_` to `-` mangling was inlined at four call
			// sites and one of them used replace('_', '-'), which replaces only
			// the first underscore.
			assert.equal(
				Zotero.Pharos.Projects.typeLabel('experiment_plan'),
				Zotero.getString('pharos-projects-type-experiment-plan')
			);
			assert.isNotEmpty(Zotero.Pharos.Projects.stageLabel('discovery'));
			assert.isNotEmpty(Zotero.Pharos.Projects.stageShort('discovery'));
			assert.isNotEmpty(Zotero.Pharos.Projects.stageNote('discovery'));
			assert.isNotEmpty(Zotero.Pharos.Projects.statusLabel('verified'));
		});

		it("should not keep a read function nothing calls", function () {
			// getArtifacts() returned exactly project.artifacts from the GET, in
			// the same order and the same user scope, and was never called.
			// DECISIONS §4's second named failure is capability built and never
			// wired.
			assert.isUndefined(Zotero.Pharos.Projects.getArtifacts);
		});
	});
});
