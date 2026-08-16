/*
	***** BEGIN LICENSE BLOCK *****

	Copyright © 2026 Pharos Contributors
	                 https://pharos.selab.top

	This file is part of Pharos, which is derived from Zotero.

	Pharos is free software: you can redistribute it and/or modify
	it under the terms of the GNU Affero General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	Pharos is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU Affero General Public License for more details.

	You should have received a copy of the GNU Affero General Public License
	along with Pharos.  If not, see <http://www.gnu.org/licenses/>.

	***** END LICENSE BLOCK *****
*/

/**
 * The model console: a personal OpenAI-compatible provider for AI 对话.
 *
 * This is the one model-backed surface an ordinary account configures from the
 * rail. Everything else model-backed (translation, the daily reader) keeps its
 * own server-side configuration for now, so this pane deliberately exposes
 * nothing but the AI conversation provider.
 *
 * The API key follows the same rule as every other credential in Pharos: it
 * travels over HTTPS once, is encrypted server-side with
 * PHAROS_CREDENTIAL_SECRET, and is never returned to this client. The backend
 * only reports whether a key is stored, so an existing key's field shows a
 * placeholder and saving without retyping keeps it.
 */
var Zotero_Pharos_Models = new function () {
	let _resolveInit;

	/** See pharosDaily.js for why this is created here rather than in onload. */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});

	/**
	 * A Fluent string with arguments.
	 *
	 * Not Zotero.getString(): handed params, that routes to the .properties
	 * bundle, where none of these ids exist. Fluent's own formatter is what
	 * reads pharos.ftl.
	 */
	function _fmt(id, args) {
		return Zotero.ftl.formatValueSync(id, args);
	}

	let _view = null;

	function _setMessage(text) {
		_view.querySelector('#pharos-models-message').textContent = text || '';
	}

	function _setBusy(busy) {
		for (let id of ['pharos-models-save', 'pharos-models-clear']) {
			_view.querySelector(`#${id}`).disabled = busy;
		}
	}

	/**
	 * Paint the current provider state.
	 *
	 * `personal` means this account has its own provider; `server` means the
	 * instance-wide fallback serves AI 对话; `none` means neither exists and
	 * every question will say so.
	 */
	function _renderProvider(status) {
		let current = _view.querySelector('#pharos-models-current');
		if (!status || !status.configured) {
			current.hidden = true;
			return;
		}
		current.hidden = false;
		let model = _view.querySelector('#pharos-models-current-model');
		model.textContent = status.model || '—';
		let source = _view.querySelector('#pharos-models-current-source');
		source.textContent = Zotero.getString(
			status.source == 'personal'
				? 'pharos-models-source-personal'
				: status.source == 'server'
					? 'pharos-models-source-server'
					: 'pharos-models-source-none'
		);
	}

	/**
	 * Fill the form from the saved provider without ever seeing its key.
	 */
	function _renderForm(status) {
		let view = _view;
		if (status) {
			if (status.base_url && !view.querySelector('#pharos-models-base-url').value) {
				view.querySelector('#pharos-models-base-url').value = status.base_url;
			}
			if (status.model && !view.querySelector('#pharos-models-model').value) {
				view.querySelector('#pharos-models-model').value = status.model;
			}
			view.querySelector('#pharos-models-temperature').value = status.temperature;
			view.querySelector('#pharos-models-max-tokens').value = status.max_output_tokens;
			// The backend never returns the key; the placeholder says one is
			// stored, and an empty save means "keep the stored key".
			let key = view.querySelector('#pharos-models-api-key');
			if (status.has_credential) {
				key.placeholder = Zotero.getString('pharos-models-api-key-stored');
			}
		}
	}

	this.init = async function () {
		_view = document.getElementById('pharos-models-root');
		let view = _view;
		view.querySelector('#pharos-models-temperature').value = '0.25';
		view.querySelector('#pharos-models-max-tokens').value = '4096';
		try {
			await this.refresh();
		}
		catch (e) {
			Zotero.logError(e);
		}
		finally {
			_resolveInit();
		}
	};

	/**
	 * Re-read the provider and repaint. Also called by the rail's onShown().
	 */
	this.refresh = async function () {
		let status = await Zotero.Pharos.API.request('GET', '/api/ai/provider');
		_renderProvider(status);
		_renderForm(status);
		return status;
	};

	/**
	 * The rail calls this whenever the module is shown again; a provider
	 * changed in the web app since the view loaded should be visible here.
	 */
	this.onShown = async function () {
		try {
			await this.refresh();
		}
		catch (e) {
			Zotero.logError(e);
		}
	};

	this.save = async function () {
		let view = _view;
		let baseURL = view.querySelector('#pharos-models-base-url').value.trim();
		let model = view.querySelector('#pharos-models-model').value.trim();
		let key = view.querySelector('#pharos-models-api-key').value.trim();
		let temperature = parseFloat(view.querySelector('#pharos-models-temperature').value);
		let maxTokens = parseInt(view.querySelector('#pharos-models-max-tokens').value, 10);

		if (!baseURL || !model) {
			_setMessage(Zotero.getString('pharos-models-error-incomplete'));
			return;
		}
		if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
			_setMessage(Zotero.getString('pharos-models-error-temperature'));
			return;
		}
		if (!Number.isFinite(maxTokens) || maxTokens < 256 || maxTokens > 128000) {
			_setMessage(Zotero.getString('pharos-models-error-max-tokens'));
			return;
		}

		_setBusy(true);
		_setMessage(Zotero.getString('pharos-models-saving'));
		try {
			await Zotero.Pharos.API.request('PUT', '/api/ai/provider', {
				body: {
					base_url: baseURL,
					model,
					temperature,
					max_output_tokens: maxTokens,
					// Omitted when blank: the backend keeps the stored key, and
					// an explicit null would clear it.
					...(key ? { api_key: key } : {}),
				},
			});
			view.querySelector('#pharos-models-api-key').value = '';
			_setMessage('');
			await this.refresh();
			// The chat pane reads the provider through the backend on its own
			// next request; nothing local caches the model.
			_setMessage(Zotero.getString('pharos-models-saved'));
		}
		catch (e) {
			Zotero.logError(e);
			_setMessage(e.message || Zotero.getString('pharos-models-error-save'));
		}
		finally {
			_setBusy(false);
		}
	};

	this.clear = async function () {
		_setBusy(true);
		_setMessage(Zotero.getString('pharos-models-clearing'));
		try {
			await Zotero.Pharos.API.request('DELETE', '/api/ai/provider');
			_setMessage('');
			await this.refresh();
			_setMessage(Zotero.getString('pharos-models-cleared'));
		}
		catch (e) {
			Zotero.logError(e);
			_setMessage(e.message || Zotero.getString('pharos-models-error-clear'));
		}
		finally {
			_setBusy(false);
		}
	};
};

// A fixed name so pharosRail.js can call onShown() without knowing which
// script this module loaded under.
var PharosView = Zotero_Pharos_Models;
