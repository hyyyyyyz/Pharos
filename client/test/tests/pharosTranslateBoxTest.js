describe("Pharos translation section", function () {
	var win, doc, box;
	var stubs = [];
	var pdfBytes, origRequest;

	function stub(object, method) {
		var s = sinon.stub(object, method);
		stubs.push(s);
		return s;
	}

	/** The section is signed-out-hidden, so nearly every test needs this. */
	function signedIn() {
		return stub(Zotero.Pharos.API, 'hasCredentials').returns(true);
	}

	/**
	 * What the section will render for an id.
	 *
	 * The same fallback the section itself uses, so these tests pin the id and
	 * the wiring rather than a translation that has not landed yet -- and keep
	 * passing unchanged once it does.
	 */
	function label(id, args) {
		let value = null;
		try {
			value = Zotero.ftl.formatValueSync(id, args);
		}
		catch {
			// Same as the section: a missing id shows as itself.
		}
		return value || id;
	}

	function show(item) {
		box.item = item;
		return box;
	}

	function buttonLabels() {
		return Array.from(box.querySelectorAll('.pharos-translate-action'))
			.map(b => b.textContent);
	}

	function pill() {
		return box.querySelector('.pharos-translate-pill');
	}

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

	function restoreBackend() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	}

	/** A paper whose PDF has been translated once, with no job record left. */
	async function translatedPaper(mode, both) {
		let item = await createDataObject('item');
		let attachment = await importPDFAttachment(item);
		stubBackend({ status: 'done', has_mono: true, has_dual: !!both });
		await Zotero.Pharos.Translate.translateItems(
			[attachment], mode || Zotero.Pharos.Translate.MODE_MONO
		);
		restoreBackend();
		// The point of the exercise: what the section shows for a paper whose
		// job this process has forgotten.
		Zotero.Pharos.Translate._clearJobs();
		let translations = Zotero.Items.get(item.getAttachments())
			.filter(a => a.id != attachment.id);
		return { item, attachment, translations };
	}

	before(async function () {
		win = await loadZoteroPane();
		doc = win.document;
		box = doc.getElementById('zotero-editpane-pharos-translate');
		let file = getTestDataDirectory();
		file.append('test.pdf');
		pdfBytes = (await IOUtils.read(file.path)).buffer;
	});

	after(function () {
		win.close();
	});

	beforeEach(function () {
		Zotero.Pharos.Translate._clearJobs();
	});

	afterEach(function () {
		restoreBackend();
		while (stubs.length) {
			stubs.pop().restore();
		}
		box.item = null;
	});

	it("should exist in the item pane", function () {
		assert.ok(box, "the section element is in itemDetails");
		assert.equal(box.dataset.pane, 'pharos-translate');
	});

	describe("registration", function () {
		// Resolved per test rather than in before(): the sidenav is assigned to
		// itemDetails during the pane's own initialization.
		function sidenav() {
			return win.ZoteroPane.itemPane._itemDetails.sidenav;
		}

		it("should be a built-in pane", function () {
			// Not being listed makes the sidenav treat it as a plugin's, which
			// sends it looking for a plugin icon it will never find -- and the
			// button renders blank rather than throwing.
			assert.include(sidenav()._builtInPanes, 'pharos-translate');
		});

		it("should be the last pane in the default order", function () {
			// It is hidden strictly more often than pharos-chat is (no PDF, and
			// also signed out), and reordering writes back a list computed from
			// *visible* positions. A pane that is sometimes hidden has to be at
			// the tail or the visible and persisted orders diverge.
			let order = sidenav()._defaultPanes;
			assert.equal(order[order.length - 1], 'pharos-translate');
			assert.isBelow(order.indexOf('pharos-chat'), order.indexOf('pharos-translate'));
		});

		it("should carry a header id rather than hardcoded text", function () {
			assert.equal(
				box.querySelector('collapsible-section').dataset.l10nId,
				'section-pharos-translate'
			);
		});
	});

	describe("when it does not apply", function () {
		it("should hide itself when the user is signed out", async function () {
			// Every control would be a button that cannot work and every state a
			// claim that cannot be checked.
			stub(Zotero.Pharos.API, 'hasCredentials').returns(false);
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			assert.isTrue(show(item).hidden);
		});

		it("should hide itself for an item with no PDF", async function () {
			signedIn();
			var item = await createDataObject('item');
			assert.isTrue(show(item).hidden);
		});

		it("should hide itself for a note", async function () {
			signedIn();
			var note = await createDataObject('item', { itemType: 'note' });
			assert.isTrue(show(note).hidden);
		});

		it("should hide itself for a non-PDF attachment", async function () {
			signedIn();
			var attachment = await importFileAttachment('test.png');
			assert.isTrue(show(attachment).hidden);
		});

		it("should survive being handed no item at all", function () {
			signedIn();
			assert.isTrue(show(null).hidden);
		});
	});

	describe("when nothing local is known", function () {
		it("should show itself for a paper with a PDF", async function () {
			signedIn();
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			assert.isFalse(show(item).hidden);
		});

		it("should not claim the paper is untranslated", async function () {
			// The account is shared with the web client, so "not translated" is
			// a claim about the account made from a library -- and wrong for
			// exactly the user who has been translating on the web. The section
			// says what it can substantiate and explains the boundary.
			signedIn();
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			show(item);

			assert.equal(pill().textContent, label('pharos-translate-state-unknown'));
			assert.isTrue(pill().classList.contains('is-unknown'));
			var note = box.querySelector('.pharos-translate-note');
			assert.isFalse(note.hidden, "the boundary is stated, not implied");
			assert.equal(note.textContent, label('pharos-translate-state-unknown-detail'));
		});

		it("should offer both modes", async function () {
			signedIn();
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			show(item);

			assert.deepEqual(buttonLabels(), [
				Zotero.getString('pharos-translate-menu-mono'),
				Zotero.getString('pharos-translate-menu-dual'),
			]);
		});

		it("should show no progress apparatus", async function () {
			signedIn();
			var item = await createDataObject('item');
			await importPDFAttachment(item);
			show(item);

			assert.isTrue(box.querySelector('.pharos-translate-track').hidden);
			assert.isTrue(box.querySelector('.pharos-translate-steps').hidden);
			assert.isTrue(box.querySelector('.pharos-translate-error').hidden);
		});
	});

	describe("when a translation exists", function () {
		it("should report it as translated and offer to open it", async function () {
			this.timeout(20000);
			signedIn();
			var { item, translations } = await translatedPaper();
			show(item);

			assert.equal(pill().textContent, label('pharos-translate-state-translated'));
			assert.include(buttonLabels(), label('pharos-translate-action-open'));
			var open = Array.from(box.querySelectorAll('.pharos-translate-action'))
				.find(b => b.textContent == label('pharos-translate-action-open'));
			assert.equal(open.title, translations[0].getField('title'),
				"the button says which file it opens");
		});

		it("should offer only the rendering the paper does not have", async function () {
			this.timeout(20000);
			// Running mono again on a paper that has a mono translation spends
			// minutes of engine time to arrive back where it started, and leaves
			// two identically-named attachments behind.
			signedIn();
			var { item } = await translatedPaper();
			show(item);

			var labels = buttonLabels();
			assert.notInclude(labels, Zotero.getString('pharos-translate-menu-mono'));
			assert.include(labels, Zotero.getString('pharos-translate-menu-dual'));
		});

		it("should name each rendering when there are two", async function () {
			this.timeout(20000);
			// "Open Translation" twice over is a coin toss between the two.
			signedIn();
			var { item } = await translatedPaper(Zotero.Pharos.Translate.MODE_DUAL, true);
			show(item);

			var labels = buttonLabels();
			assert.include(labels, label('pharos-translate-action-open-named',
				{ name: Zotero.getString('pharos-translate-suffix-mono') }));
			assert.include(labels, label('pharos-translate-action-open-named',
				{ name: Zotero.getString('pharos-translate-suffix-dual') }));
			assert.notInclude(labels, Zotero.getString('pharos-translate-menu-mono'));
			assert.notInclude(labels, Zotero.getString('pharos-translate-menu-dual'));
		});

		it("should offer the way back from a translation to its original", async function () {
			this.timeout(20000);
			signedIn();
			var { attachment, translations } = await translatedPaper();
			show(translations[0]);

			assert.isFalse(box.hidden);
			assert.equal(pill().textContent, label('pharos-translate-state-is-translation'));
			var labels = buttonLabels();
			assert.deepEqual(labels, [label('pharos-translate-action-open-original')]);
			var back = box.querySelector('.pharos-translate-action');
			assert.equal(back.title, attachment.getField('title'));
		});
	});

	describe("when the last run failed", function () {
		it("should show the reason, truncated, and offer a retry", async function () {
			this.timeout(20000);
			// The progress dialog is closed by now -- that is the whole reason
			// this section exists. Untruncated, a Python traceback would push
			// the retry button off the bottom of the item pane.
			signedIn();
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({
				status: 'error',
				error: 'Traceback (most recent call last):\n' + 'x'.repeat(4000),
			});
			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);
			show(item);

			assert.equal(pill().textContent, label('pharos-translate-state-failed'));
			var error = box.querySelector('.pharos-translate-error');
			assert.isFalse(error.hidden);
			assert.isAtMost(error.textContent.length, 201);
			assert.isTrue(error.textContent.endsWith('…'));
			assert.deepEqual(buttonLabels(), [label('pharos-translate-action-retry')]);
		});

		it("should still offer an older translation alongside the failure", async function () {
			this.timeout(30000);
			signedIn();
			var { item, attachment } = await translatedPaper();
			stubBackend({ status: 'error', error: 'engine exploded' });
			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_DUAL
			);
			show(item);

			assert.equal(pill().textContent, label('pharos-translate-state-failed'));
			assert.deepEqual(buttonLabels(), [
				label('pharos-translate-action-retry'),
				label('pharos-translate-action-open'),
			]);
		});
	});

	describe("while a job runs", function () {
		it("should report progress and offer no second start", async function () {
			this.timeout(20000);
			// A queued paper that still offered "translate" would be queued
			// twice by one impatient click.
			signedIn();
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: false });
			show(item);

			var promise = Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			assert.equal(pill().textContent, label('pharos-translate-state-translating'));
			assert.isTrue(pill().classList.contains('is-running'));
			assert.isFalse(box.querySelector('.pharos-translate-track').hidden);
			assert.deepEqual(buttonLabels(), [label('pharos-translate-action-queue')]);

			await promise;
		});

		it("should redraw itself from the poll loop", async function () {
			this.timeout(30000);
			// Progress comes out of a poll loop, not out of the data layer, so
			// Zotero.Notifier never hears about it: without the state listener
			// the section would sit at "queued" until the file was imported.
			signedIn();
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			var seen = [];
			// Registered after the section's own, so the Set is walked in that
			// order and this reads the pane the section has just painted.
			var watcher = () => {
				seen.push({
					pill: pill().textContent,
					steps: Array.from(box.querySelectorAll('.pharos-translate-step'))
						.map(s => s.className),
					title: box.querySelector('.pharos-translate-step')?.title,
					width: box.querySelector('.pharos-translate-bar').style.width,
				});
			};
			var replies = [
				{ status: 'running', stage: 'Translating the body text', progress: 42 },
				{ status: 'done', has_mono: true, has_dual: false },
			];
			origRequest = Zotero.Pharos.API.request;
			Zotero.Pharos.API.request = async function (method, path) {
				if (method == 'GET' && path.startsWith('/api/jobs/')) {
					return replies.length > 1 ? replies.shift() : replies[0];
				}
				if (method == 'POST' && path == '/api/papers') {
					return { id: 'paper-1' };
				}
				if (method == 'POST' && /^\/api\/papers\/[^/]+\/translate$/.test(path)) {
					return { id: 'job-1' };
				}
				return pdfBytes;
			};
			show(item);
			Zotero.Pharos.Translate.addStateListener(watcher);
			try {
				await Zotero.Pharos.Translate.translateItems(
					[attachment], Zotero.Pharos.Translate.MODE_MONO
				);
			}
			finally {
				Zotero.Pharos.Translate.removeStateListener(watcher);
			}

			var running = seen.find(s => s.width == '42%');
			assert.ok(running, "the bar followed the poll loop");
			assert.equal(running.pill,
				label('pharos-translate-state-translating-percent', { percent: 42 }));
			// The engine's raw label is long prose. It is demoted to a tooltip
			// rather than discarded, and the three-step stepper is what the user
			// reads: step two active, step one already done.
			assert.deepEqual(running.steps, [
				'pharos-translate-step is-done',
				'pharos-translate-step is-active',
				'pharos-translate-step is-todo',
			]);
			assert.equal(running.title, label('pharos-translate-stage-tooltip',
				{ stage: 'Translating the body text' }));
		});

		it("should settle on translated when the job lands", async function () {
			this.timeout(20000);
			signedIn();
			var item = await createDataObject('item');
			var attachment = await importPDFAttachment(item);
			stubBackend({ status: 'done', has_mono: true, has_dual: false });
			show(item);
			await Zotero.Pharos.Translate.translateItems(
				[attachment], Zotero.Pharos.Translate.MODE_MONO
			);

			assert.equal(pill().textContent, label('pharos-translate-state-translated'));
			assert.isTrue(box.querySelector('.pharos-translate-track').hidden);
			assert.include(buttonLabels(), label('pharos-translate-action-open'));
		});
	});

	describe("rendering cost", function () {
		it("should not touch the network on selection", async function () {
			this.timeout(20000);
			// This renders on every selection change, including an arrow key
			// held down over a list. A request here would be one per row
			// scrolled past -- and an upload, not a GET, because the backend
			// addresses papers by content hash.
			signedIn();
			var { item, attachment, translations } = await translatedPaper();
			origRequest = Zotero.Pharos.API.request;
			Zotero.Pharos.API.request = function () {
				throw new Error("the item pane went to the network");
			};

			show(item);
			assert.isFalse(box.hidden);
			show(attachment);
			assert.isFalse(box.hidden);
			show(translations[0]);
			assert.isFalse(box.hidden);
		});
	});
});
