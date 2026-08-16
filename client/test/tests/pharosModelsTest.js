describe("Zotero.Pharos.Models console", function () {
	var win, view;
	var origRequest = null;
	var requests = [];

	function captureRequests(responder) {
		requests = [];
		if (origRequest === null) {
			origRequest = Zotero.Pharos.API.request;
		}
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

	before(async function () {
		captureRequests({
			configured: false,
			has_credential: false,
			base_url: '',
			model: '',
			temperature: 0.25,
			max_output_tokens: 4096,
			source: 'none',
			can_store_credential: true,
		});
		win = await loadWindow("chrome://zotero/content/pharosModels.xhtml");
		view = win.document.getElementById('pharos-models-root');
	});

	after(async function () {
		restoreRequests();
		win.close();
	});

	it("should read the current provider on load", function () {
		assert.lengthOf(requests, 1);
		assert.equal(requests[0].method, 'GET');
		assert.equal(requests[0].path, '/api/ai/provider');
	});

	it("should show which model is in use", async function () {
		captureRequests({
			configured: true,
			has_credential: true,
			base_url: 'https://api.deepseek.com',
			model: 'deepseek-chat',
			temperature: 0.25,
			max_output_tokens: 4096,
			source: 'personal',
			can_store_credential: true,
		});
		await win.Zotero_Pharos_Models.refresh();
		var current = win.document.getElementById('pharos-models-current');
		assert.isFalse(current.hidden);
		assert.equal(
			win.document.getElementById('pharos-models-current-model').textContent,
			'deepseek-chat'
		);
		assert.equal(
			win.document.getElementById('pharos-models-api-key').placeholder,
			Zotero.getString('pharos-models-api-key-stored'),
			"the stored key is described, never echoed"
		);
	});

	it("should save the provider and omit the key when blank", async function () {
		captureRequests(null);
		win.document.getElementById('pharos-models-base-url').value = 'https://api.deepseek.com';
		win.document.getElementById('pharos-models-model').value = 'deepseek-chat';
		win.document.getElementById('pharos-models-api-key').value = '';
		win.document.getElementById('pharos-models-temperature').value = '0.3';
		win.document.getElementById('pharos-models-max-tokens').value = '8192';
		await win.Zotero_Pharos_Models.save();
		assert.equal(requests[0].method, 'PUT');
		assert.equal(requests[0].path, '/api/ai/provider');
		assert.equal(requests[0].options.body.base_url, 'https://api.deepseek.com');
		assert.equal(requests[0].options.body.model, 'deepseek-chat');
		assert.equal(requests[0].options.body.temperature, 0.3);
		assert.equal(requests[0].options.body.max_output_tokens, 8192);
		assert.notOk(requests[0].options.body.api_key,
			"a blank key means keep the stored one, not clear it");
	});

	it("should refuse a malformed form without calling the server", async function () {
		captureRequests(null);
		win.document.getElementById('pharos-models-base-url').value = '';
		win.document.getElementById('pharos-models-model').value = '';
		await win.Zotero_Pharos_Models.save();
		assert.lengthOf(requests, 0, "no request for an incomplete form");
		win.document.getElementById('pharos-models-base-url').value = 'https://x.example';
		win.document.getElementById('pharos-models-model').value = 'm';
		win.document.getElementById('pharos-models-temperature').value = '9';
		await win.Zotero_Pharos_Models.save();
		assert.lengthOf(requests, 0, "no request for an out-of-range temperature");
	});

	it("should clear the personal provider", async function () {
		captureRequests(null);
		await win.Zotero_Pharos_Models.clear();
		assert.equal(requests[0].method, 'DELETE');
		assert.equal(requests[0].path, '/api/ai/provider');
	});

	it("should expose onShown for the rail", function () {
		assert.isFunction(win.PharosView.onShown);
	});
});
