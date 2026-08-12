"use strict";

describe("Zotero.Pharos.Evidence", function () {
	let Evidence;
	let originalRequest;
	let originalHasCredentials;
	let originalResolvePaperID;
	let win;

	before(function () {
		Evidence = Zotero.Pharos.Evidence;
		originalRequest = Zotero.Pharos.API.request;
		originalHasCredentials = Zotero.Pharos.API.hasCredentials;
		originalResolvePaperID = Zotero.Pharos.Chat.resolvePaperID;
	});

	before(async function () {
		win = await loadZoteroPane();
	});

	after(function () {
		win.close();
	});

	afterEach(function () {
		Zotero.Pharos.API.request = originalRequest;
		Zotero.Pharos.API.hasCredentials = originalHasCredentials;
		Zotero.Pharos.Chat.resolvePaperID = originalResolvePaperID;
	});

	it("should convert one-page reader rectangles to API geometry", function () {
		assert.deepEqual(
			Evidence._rectsForAnnotation({
				text: "A sufficiently long quotation",
				position: {
					pageIndex: 0,
					rects: [[120, 700, 220, 688], [120, 680, 190, 668]],
				},
			}),
			[
				{ x: 120, y: 688, w: 100, h: 12 },
				{ x: 120, y: 668, w: 70, h: 12 },
			]
		);
	});

	it("should omit geometry for a multi-page or invalid selection", function () {
		assert.isNull(Evidence._rectsForAnnotation({
			position: {
				pageIndex: 0,
				rects: [[1, 2, 3, 4]],
				nextPageRects: [[1, 2, 3, 4]],
			},
		}));
		assert.isNull(Evidence._rectsForAnnotation({
			position: { pageIndex: 0, rects: [[1, 2, 1, 4]] },
		}));
	});

	it("should resolve the open attachment and post a quote", async function () {
		Zotero.Pharos.API.hasCredentials = () => true;
		Zotero.Pharos.Chat.resolvePaperID = async attachment => {
			assert.equal(attachment.id, 17);
			return "paper-17";
		};
		let request = sinon.stub().resolves({ id: "evidence-1", locator: "page", page_no: 3 });
		Zotero.Pharos.API.request = request;
		let status = { isConnected: true, hidden: false, dataset: {}, textContent: "" };
		let button = { isConnected: true, disabled: false, setAttribute: sinon.spy() };

		let result = await Evidence.saveSelection(
			{ type: "pdf", _item: { id: 17 } },
			{
				text: "A sufficiently long quotation",
				position: { pageIndex: 2, rects: [[10, 20, 110, 32]] },
			},
			{ status, button }
		);

		assert.equal(result.id, "evidence-1");
		assert.isTrue(request.calledOnce);
		assert.equal(request.firstCall.args[0], "POST");
		assert.equal(request.firstCall.args[1], "/api/evidence");
		assert.deepEqual(request.firstCall.args[2].body, {
			paper_id: "paper-17",
			kind: "quote",
			text: "A sufficiently long quotation",
			page_hint: 3,
			rects: [{ x: 10, y: 20, w: 100, h: 12 }],
		});
		assert.equal(status.textContent, Zotero.getString("pharos-evidence-saved"));
		assert.isFalse(button.disabled);
	});

	it("should send the selected occurrence as a one-based untrusted page hint", async function () {
		Zotero.Pharos.API.hasCredentials = () => true;
		Zotero.Pharos.Chat.resolvePaperID = async () => "paper-1";
		let request = sinon.stub().resolves({
			id: "evidence-repeated", locator: "page", page_no: 3,
		});
		Zotero.Pharos.API.request = request;

		await Evidence.saveSelection(
			{ type: "pdf", _item: { id: 1 } },
			{
				text: "A sufficiently long repeated quotation",
				position: { pageIndex: 2, rects: [[10, 20, 110, 32]] },
			}
		);

		assert.isTrue(request.calledOnce);
		assert.equal(request.firstCall.args[1], "/api/evidence");
		assert.equal(request.firstCall.args[2].body.page_hint, 3);
		assert.deepEqual(
			request.firstCall.args[2].body.rects,
			[{ x: 10, y: 20, w: 100, h: 12 }]
		);
	});

	it("should save quote-only evidence when the reader position is not safe", async function () {
		Zotero.Pharos.API.hasCredentials = () => true;
		Zotero.Pharos.Chat.resolvePaperID = async () => "paper-1";
		let request = sinon.stub().resolves({ id: "evidence-2" });
		Zotero.Pharos.API.request = request;

		await Evidence.saveSelection(
			{ type: "pdf", _item: { id: 1 } },
			{
				text: "A sufficiently long quotation",
				position: {
					pageIndex: 0,
					rects: [[10, 20, 110, 32]],
					nextPageRects: [[10, 20, 110, 32]],
				},
			}
		);

		assert.notProperty(request.firstCall.args[2].body, "page_hint");
		assert.notProperty(request.firstCall.args[2].body, "rects");
	});

	it("should expose a clear error when the quote is not in extracted pages", async function () {
		Zotero.Pharos.API.hasCredentials = () => true;
		Zotero.Pharos.Chat.resolvePaperID = async () => "paper-1";
		let error = new Error("This text does not appear in the extracted pages");
		error.status = 409;
		Zotero.Pharos.API.request = sinon.stub().rejects(error);
		let status = { isConnected: true, hidden: false, dataset: {}, textContent: "" };
		let button = { isConnected: true, disabled: false, setAttribute: sinon.spy() };

		let caught = await getPromiseError(Evidence.saveSelection(
			{ type: "pdf", _item: { id: 1 } },
			{ text: "A sufficiently long quotation", position: { pageIndex: 0, rects: [] } },
			{ status, button }
		));

		assert.strictEqual(caught, error);
		assert.equal(status.textContent, Zotero.getString("pharos-evidence-error-not-in-paper"));
		assert.equal(status.dataset.state, "error");
		assert.isFalse(button.disabled);
	});

	it("should render the save action only for signed-in PDF selections", function () {
		let doc = win.document;
		let appended = [];
		let event = {
			reader: { type: "pdf", _item: { id: 1 } },
			doc,
			params: { annotation: { text: "A sufficiently long quotation", position: { pageIndex: 0, rects: [[1, 2, 3, 4]] } } },
			append: (...nodes) => appended.push(...nodes),
		};

		Zotero.Pharos.API.hasCredentials = () => false;
		Evidence._renderTextSelectionPopup(event);
		assert.lengthOf(appended, 0);

		Zotero.Pharos.API.hasCredentials = () => true;
		Evidence._renderTextSelectionPopup(event);
		assert.lengthOf(appended, 1);
		assert.ok(appended[0].querySelector('[data-pharos-evidence-save="true"]'));
	});
});
