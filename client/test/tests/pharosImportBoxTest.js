describe("Pharos import box (tab bar)", function () {
	// Own sandbox: a global sinon.restore() would also undo stubs other suites
	// set up for themselves.
	var win, doc, rail, box, input;
	var sandbox = sinon.createSandbox();
	var FAIL = reason => ({ imported: [], existing: [], failed: [{ identifier: { arXiv: 'x' }, reason }] });

	before(async function () {
		win = await loadZoteroPane();
		doc = win.document;
		rail = doc.getElementById('pharos-rail');
		box = doc.getElementById('pharos-import-box');
		input = box._input;
	});

	beforeEach(async function () {
		await selectLibrary(win);
		rail.module = 'library';
		box.setNarrow(false);
		box._clearStatus();
		input.value = '';
		// Nothing here reaches arXiv.
		sandbox.stub(Zotero.Pharos.Import, 'translateIdentifier');
	});

	afterEach(function () {
		sandbox.restore();
	});

	after(async function () {
		win.Zotero_Tabs.closeAll();
		win.ZoteroPane.itemsView.selection.clearSelection();
		await Zotero.Promise.delay(500);
		win.close();
	});

	function key(k, target = input) {
		target.dispatchEvent(new win.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
	}

	function paste(text) {
		input.value = text;
		input.dispatchEvent(new win.InputEvent('input', { inputType: 'insertFromPaste', bubbles: true }));
	}

	// The unit-test window is often not OS-active, and then focus() moves
	// document.activeElement without firing focus/focusout. Fire what is missing,
	// so these tests are about the box and not about the window manager.
	function focusInput() {
		var fired = false;
		var on = () => fired = true;
		input.addEventListener('focus', on);
		input.focus();
		input.removeEventListener('focus', on);
		if (!fired) {
			input.dispatchEvent(new win.FocusEvent('focus'));
		}
	}

	function blurInput() {
		var fired = false;
		var on = () => fired = true;
		input.addEventListener('focusout', on);
		input.blur();
		input.removeEventListener('focusout', on);
		if (!fired) {
			input.dispatchEvent(new win.FocusEvent('focusout', { bubbles: true }));
		}
	}

	async function settle() {
		await new Promise(r => setTimeout(r, 0));
		await Zotero.Promise.delay(20);
	}

	it("should sit in the tab bar toolbar, before the tabs menu button", function () {
		var next = box.nextElementSibling;
		assert.equal(box.parentNode.id, 'zotero-tabs-toolbar');
		assert.equal(next.id, 'zotero-tb-tabs-menu');
	});

	it("should have an accessible name and a status node", function () {
		assert.ok(input.getAttribute('aria-label'));
		assert.equal(box._status.getAttribute('role'), 'status');
		assert.ok(box._toggle.getAttribute('aria-label') || box._toggle.hasAttribute('data-l10n-id'));
	});

	describe("typing and pasting", function () {
		it("should import on Enter", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			input.value = 'https://arxiv.org/abs/1706.03762';
			key('Enter');
			await settle();
			assert.equal(stub.firstCall.args[0], 'https://arxiv.org/abs/1706.03762');
		});

		it("should not import typed text without Enter", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			input.value = 'https://arxiv.org/abs/1706.03762';
			input.dispatchEvent(new win.InputEvent('input', { inputType: 'insertText', bubbles: true }));
			await settle();
			assert.isFalse(stub.called);
		});

		it("should import a pasted link at once", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			paste('https://arxiv.org/pdf/1706.03762');
			await settle();
			assert.isTrue(stub.calledOnce);
		});

		it("should import several pasted links", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			paste('arXiv:1706.03762\n10.1000/xyz123');
			await settle();
			assert.isTrue(stub.calledOnce);
		});

		it("should not import a paste with text it cannot read left over", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			paste('see https://arxiv.org/abs/1706.03762 for details');
			await settle();
			assert.isFalse(stub.called);
			assert.equal(input.value, 'see https://arxiv.org/abs/1706.03762 for details');
		});

		it("should not import a paste that is no link", async function () {
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('other'));
			paste('hello');
			await settle();
			assert.isFalse(stub.called);
		});

		it("should clear and blur on Esc", function () {
			input.focus();
			input.value = 'abc';
			key('Escape');
			assert.equal(input.value, '');
			assert.notEqual(doc.activeElement, input);
		});
	});

	describe("while working", function () {
		it("should disable the field and show the beam and 'importing'", async function () {
			var release;
			sandbox.stub(Zotero.Pharos.Import, 'importFromText').returns(
				new Promise(resolve => release = resolve)
			);
			var pending = box.importText('https://arxiv.org/abs/1706.03762');
			assert.isTrue(input.disabled);
			assert.isTrue(box._field.classList.contains('is-busy'));
			await settle();
			assert.equal(box._status.dataset.kind, 'busy');
			assert.isFalse(box._status.hidden);
			release(FAIL('other'));
			await pending;
			assert.isFalse(input.disabled);
			assert.isFalse(box._field.classList.contains('is-busy'));
		});

		it("should ignore a second import while one runs", async function () {
			var release;
			var stub = sandbox.stub(Zotero.Pharos.Import, 'importFromText').returns(
				new Promise(resolve => release = resolve)
			);
			var pending = box.importText('arXiv:1706.03762');
			assert.isNull(await box.importText('arXiv:1706.03763'));
			release(FAIL('other'));
			await pending;
			assert.isTrue(stub.calledOnce);
		});
	});

	describe("what the field says", function () {
		it("should say how many were imported, and fade", async function () {
			var item = await createDataObject('item');
			sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(
				{ imported: [item], existing: [], failed: [] }
			);
			sandbox.stub(PharosImportBoxClass(), 'OK_MS').value(30);
			input.value = 'arXiv:1706.03762';
			await box.importText('arXiv:1706.03762');
			assert.equal(box._status.dataset.kind, 'ok');
			assert.include(box._status.textContent, '1');
			assert.equal(input.value, '');
			assert.deepEqual(win.ZoteroPane.getSelectedItems(true), [item.id]);
			await Zotero.Promise.delay(100);
			assert.isTrue(box._status.hidden);
		});

		it("should say a paper is already in the library, and select it", async function () {
			var item = await createDataObject('item');
			sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(
				{ imported: [], existing: [item], failed: [] }
			);
			await box.importText('arXiv:1706.03762');
			assert.equal(box._status.dataset.kind, 'ok');
			assert.isNotEmpty(box._status.textContent);
			assert.deepEqual(win.ZoteroPane.getSelectedItems(true), [item.id]);
		});

		for (let reason of ['unrecognized', 'not-found', 'network', 'rate-limited', 'read-only', 'other']) {
			it(`should give a one-line reason for ${reason}`, async function () {
				sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL(reason));
				input.value = 'arXiv:1706.03762';
				await box.importText('arXiv:1706.03762');
				var expected = await doc.l10n.formatValue('pharos-import-fail-' + reason);
				assert.equal(box._status.dataset.kind, 'error');
				assert.equal(box._status.textContent, expected);
				assert.equal(box._field.dataset.state, 'error');
			});
		}

		it("should bring the input back on focus after a failure", async function () {
			sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(FAIL('network'));
			input.value = 'arXiv:1706.03762';
			await box.importText('arXiv:1706.03762');
			assert.equal(input.value, 'arXiv:1706.03762');
			assert.isFalse(box._status.hidden);
			focusInput();
			assert.isTrue(box._status.hidden);
			assert.isUndefined(box._field.dataset.state);
			assert.equal(input.value, 'arXiv:1706.03762');
		});
	});

	describe("in a reader tab", function () {
		var tabID;

		before(async function () {
			var attachment = await importFileAttachment('test.pdf');
			var reader = await Zotero.Reader.open(attachment.id);
			tabID = reader.tabID;
		});

		after(function () {
			win.Zotero_Tabs.close(tabID);
		});

		beforeEach(function () {
			win.Zotero_Tabs.select(tabID);
		});

		it("should not leave the reader, and switch only when the message is clicked", async function () {
			var item = await createDataObject('item');
			sandbox.stub(Zotero.Pharos.Import, 'importFromText').resolves(
				{ imported: [item], existing: [], failed: [] }
			);
			assert.equal(win.Zotero_Tabs.selectedID, tabID);
			await box.importText('arXiv:1706.03762');
			assert.equal(win.Zotero_Tabs.selectedID, tabID);
			assert.isTrue(box._status.classList.contains('is-clickable'));
			box._status.click();
			await settle();
			assert.equal(win.Zotero_Tabs.selectedID, 'zotero-pane');
			assert.deepEqual(win.ZoteroPane.getSelectedItems(true), [item.id]);
		});
	});

	describe("narrow bar", function () {
		it("should fold into a button and unfold in place", function () {
			box.setNarrow(true);
			var cs = win.getComputedStyle.bind(win);
			assert.equal(cs(box._field).display, 'none');
			assert.notEqual(cs(box._toggle).display, 'none');
			box._toggle.click();
			assert.equal(cs(box._field).display, 'flex');
			assert.equal(doc.activeElement, input);
		});

		it("should fold back when it loses focus empty", async function () {
			box.setNarrow(true);
			box._toggle.click();
			blurInput();
			await settle();
			assert.isFalse(box.hasAttribute('open'));
		});

		it("should stay open while it holds text", async function () {
			box.setNarrow(true);
			box._toggle.click();
			input.value = 'abc';
			blurInput();
			await settle();
			assert.isTrue(box.hasAttribute('open'));
		});

		it("should switch with the width of the title bar", function () {
			box.setNarrow(false);
			var bar = doc.getElementById('zotero-title-bar');
			sandbox.stub(bar, 'getBoundingClientRect').returns({ width: 500 });
			box._measure();
			assert.isTrue(box.hasAttribute('narrow'));
			bar.getBoundingClientRect.returns({ width: 1000 });
			box._measure();
			assert.isFalse(box.hasAttribute('narrow'));
		});
	});

	describe("Cmd/Ctrl+V on the item list", function () {
		var importText, target;

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
			importText = sandbox.stub(box, 'importText').resolves(null);
			win.ZoteroPane.itemsView.focus();
			// Same reason as focusInput(): aim at the list itself.
			target = doc.querySelector('#zotero-items-tree .virtualized-table');
		});

		it("should import a recognisable clipboard", function () {
			sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('https://arxiv.org/abs/1706.03762');
			var event = press(target);
			assert.isTrue(event.defaultPrevented);
			assert.isTrue(importText.calledOnce);
		});

		it("should leave any other clipboard alone", function () {
			sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('some copied sentence');
			var event = press(target);
			assert.isFalse(event.defaultPrevented);
			assert.isFalse(importText.called);
		});

		it("should leave an empty clipboard alone", function () {
			sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns(null);
			var event = press(target);
			assert.isFalse(event.defaultPrevented);
		});

		it("should do nothing while a text field has focus", function () {
			sandbox.stub(Zotero.Utilities.Internal, 'getClipboard').returns('https://arxiv.org/abs/1706.03762');
			var field = doc.createElement('input');
			doc.getElementById('zotero-items-tree').appendChild(field);
			var event = press(field);
			field.remove();
			assert.isFalse(event.defaultPrevented);
			assert.isFalse(importText.called);
		});
	});

	function PharosImportBoxClass() {
		return box.constructor;
	}
});
