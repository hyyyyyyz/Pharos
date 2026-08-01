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
 * The literature discovery window.
 *
 * Shares the digest window's stylesheet and structure: both are lists of papers
 * the user does not have yet, with one action that matters -- put it in the
 * library.
 */
var Zotero_Pharos_Discovery = new function () {
	let _resolveInit;

	/** Settles once init() has finished. See pharosDaily.js for why this is
	 *  created here rather than assigned from the onload attribute. */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		this._list = document.getElementById('pharos-daily-list');
		this._status = document.getElementById('pharos-daily-status');
		this._summary = document.getElementById('pharos-daily-summary');
		this._query = document.getElementById('pharos-discovery-query');
		this._button = document.getElementById('pharos-discovery-search');

		this._button.addEventListener('command', () => this.search());
		this._button.addEventListener('click', () => this.search());
		this._query.addEventListener('keydown', (event) => {
			if (event.key == 'Enter') {
				event.preventDefault();
				this.search();
			}
		});

		if (!Zotero.Pharos.API.hasCredentials()) {
			this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
			this._button.disabled = true;
			this._query.disabled = true;
			return;
		}

		this._setStatus(Zotero.getString('pharos-discovery-hint'));
		this._query.focus();
	};

	this._setStatus = function (text) {
		this._status.textContent = text || '';
		this._status.hidden = !text;
	};

	this.search = async function () {
		let query = this._query.value.trim();
		if (!query) {
			return;
		}
		this._button.disabled = true;
		this._list.replaceChildren();
		this._summary.textContent = '';
		this._setStatus(Zotero.getString('pharos-discovery-searching'));
		try {
			let search = await Zotero.Pharos.Discovery.search(query);
			this._render(search);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-discovery-error'));
		}
		finally {
			this._button.disabled = false;
		}
	};

	this._render = function (search) {
		this._summary.textContent = Zotero.getString(
			'pharos-discovery-count', [search.result_count]
		);

		// A search where every provider failed is still a recorded run, and the
		// backend returns it with status=error and the per-provider reasons
		// rather than a 5xx. Showing those beats a bare "no results".
		let errors = Object.entries(search.errors || {});
		if (errors.length) {
			this._setStatus(errors.map(([source, message]) => `${source}: ${message}`).join(' · '));
		}
		else if (!search.results.length) {
			this._setStatus(Zotero.getString('pharos-discovery-empty'));
			return;
		}
		else {
			this._setStatus('');
		}

		for (let result of search.results) {
			this._list.append(this._renderResult(result));
		}
	};

	this._renderResult = function (result) {
		let row = document.createElement('div');
		row.className = 'pharos-daily-paper';

		let title = document.createElement('div');
		title.className = 'pharos-daily-title';
		title.textContent = result.title;
		row.append(title);

		let meta = document.createElement('div');
		meta.className = 'pharos-daily-meta';
		let bits = [];
		if (result.authors && result.authors.length) {
			bits.push(result.authors.slice(0, 4).join(', ')
				+ (result.authors.length > 4 ? ' et al.' : ''));
		}
		if (result.year) {
			bits.push(result.year);
		}
		if (result.venue) {
			bits.push(result.venue);
		}
		if (typeof result.citation_count == 'number') {
			bits.push(Zotero.getString('pharos-discovery-citations', [result.citation_count]));
		}
		bits.push((result.sources || []).join(' + '));
		meta.textContent = bits.join(' · ');
		row.append(meta);

		let summary = document.createElement('div');
		summary.className = 'pharos-daily-summary-text';
		summary.textContent = result.summary_zh || result.abstract || '';
		row.append(summary);

		let actions = document.createElement('div');
		actions.className = 'pharos-daily-actions';

		let save = document.createElement('button');
		save.textContent = Zotero.getString('pharos-daily-save');
		save.addEventListener('click', () => this.save(result, save));
		actions.append(save);

		if (result.analysis_mode == 'rules') {
			// Only offered where it would change something: a result the model
			// has already read does not need reading again.
			let analyze = document.createElement('button');
			analyze.textContent = Zotero.getString('pharos-discovery-analyze');
			analyze.addEventListener('click', () => this.analyze(result, row, analyze));
			actions.append(analyze);
		}

		if (result.url) {
			let open = document.createElement('button');
			open.textContent = Zotero.getString('pharos-discovery-open');
			open.addEventListener('click', () => Zotero.launchURL(result.url));
			actions.append(open);
		}

		row.append(actions);
		return row;
	};

	this.analyze = async function (result, row, button) {
		button.disabled = true;
		button.textContent = Zotero.getString('pharos-discovery-analyzing');
		try {
			let updated = await Zotero.Pharos.Discovery.analyze(result.id);
			row.replaceWith(this._renderResult(updated));
		}
		catch (e) {
			Zotero.logError(e);
			button.disabled = false;
			button.textContent = Zotero.getString('pharos-discovery-analyze');
			this._setStatus(e.message || Zotero.getString('pharos-discovery-error'));
		}
	};

	this.save = async function (result, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = Zotero.getString('pharos-daily-saving');
		try {
			await Zotero.Pharos.Discovery.saveToLibrary(result);
			button.textContent = Zotero.getString('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			this._setStatus(e.message || Zotero.getString('pharos-daily-save-failed'));
		}
	};
};
