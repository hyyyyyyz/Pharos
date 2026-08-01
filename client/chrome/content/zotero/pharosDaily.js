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
 * The daily arXiv digest window.
 *
 * Its own window rather than a pane in the library: the digest is a list of
 * papers you do NOT have yet, and mixing them into the item tree would blur the
 * line between "my library" and "today's candidates".
 */
var Zotero_Pharos_Daily = new function () {
	let _refreshTimer = null;
	let _resolveInit;

	/**
	 * Settles once init() has finished, however it finished.
	 *
	 * Created here, when the script loads, rather than assigned from the onload
	 * handler: a caller that opens this window gets control back before onload
	 * has run, so an `initialized` that only exists afterwards is `undefined`
	 * exactly when someone needs to wait on it -- and `await undefined` succeeds
	 * silently, which made a test pass alone and fail on a busy machine.
	 */
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
		this._dateSelect = document.getElementById('pharos-daily-date');
		this._refreshButton = document.getElementById('pharos-daily-refresh');

		this._dateSelect.addEventListener('change', () => this.load(this._dateSelect.value));
		this._refreshButton.addEventListener('command', () => this.refresh());
		this._refreshButton.addEventListener('click', () => this.refresh());

		if (!Zotero.Pharos.API.hasCredentials()) {
			this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
			this._refreshButton.disabled = true;
			return;
		}

		await this.loadDates();
		await this.load(Zotero.Pharos.Daily.today());
	};

	this.destroy = function () {
		// Otherwise a sweep started here keeps polling after the window is gone.
		if (_refreshTimer) {
			clearTimeout(_refreshTimer);
			_refreshTimer = null;
		}
	};

	this._setStatus = function (text) {
		this._status.textContent = text || '';
		this._status.hidden = !text;
	};

	this.loadDates = async function () {
		try {
			let dates = await Zotero.Pharos.Daily.getDates();
			let today = Zotero.Pharos.Daily.today();
			// Today may have no run yet and so be absent from the list, but it is
			// the date the window opens on and has to be selectable.
			if (!dates.some(d => d.date == today)) {
				dates.unshift({ date: today, total: 0 });
			}
			this._dateSelect.replaceChildren();
			for (let entry of dates) {
				let option = document.createElement('option');
				option.value = entry.date;
				option.textContent = `${entry.date} (${entry.total})`;
				this._dateSelect.append(option);
			}
			this._dateSelect.value = today;
		}
		catch (e) {
			Zotero.logError(e);
		}
	};

	this.load = async function (date) {
		this._setStatus(Zotero.getString('pharos-daily-loading'));
		this._list.replaceChildren();
		try {
			let day = await Zotero.Pharos.Daily.getDay(date);
			this._render(day);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-daily-error'));
		}
	};

	this._render = function (day) {
		this._summary.textContent = Zotero.getString('pharos-daily-count', [day.total]);
		if (!day.papers.length) {
			this._setStatus(Zotero.getString('pharos-daily-empty'));
			return;
		}
		this._setStatus('');
		for (let paper of day.papers) {
			this._list.append(this._renderPaper(paper));
		}
	};

	this._renderPaper = function (paper) {
		let row = document.createElement('div');
		row.className = 'pharos-daily-paper';

		let title = document.createElement('div');
		title.className = 'pharos-daily-title';
		title.textContent = paper.title;
		row.append(title);

		let meta = document.createElement('div');
		meta.className = 'pharos-daily-meta';
		let bits = [];
		if (paper.authors && paper.authors.length) {
			// Author lists on arXiv routinely run to dozens of names and would
			// otherwise be the tallest thing in the row.
			bits.push(paper.authors.slice(0, 4).join(', ')
				+ (paper.authors.length > 4 ? ' et al.' : ''));
		}
		if (paper.matched_domain) {
			bits.push(paper.matched_domain);
		}
		if (typeof paper.score_recommendation == 'number') {
			bits.push(`★ ${paper.score_recommendation.toFixed(1)}`);
		}
		meta.textContent = bits.join(' · ');
		row.append(meta);

		if (paper.read_status == 'done' && paper.summary_zh) {
			let summary = document.createElement('div');
			summary.className = 'pharos-daily-summary-text';
			summary.textContent = paper.summary_zh;
			row.append(summary);
		}
		else if (paper.read_status == 'pending') {
			let pending = document.createElement('div');
			pending.className = 'pharos-daily-pending';
			pending.textContent = Zotero.getString('pharos-daily-unread');
			row.append(pending);
		}
		else if (paper.read_status == 'error') {
			let failed = document.createElement('div');
			failed.className = 'pharos-daily-pending';
			failed.textContent = paper.read_error
				|| Zotero.getString('pharos-daily-read-failed');
			row.append(failed);
		}

		let actions = document.createElement('div');
		actions.className = 'pharos-daily-actions';

		let save = document.createElement('button');
		save.textContent = Zotero.getString('pharos-daily-save');
		save.addEventListener('click', () => this.save(paper, save));
		actions.append(save);

		if (paper.arxiv_url) {
			let open = document.createElement('button');
			open.textContent = Zotero.getString('pharos-daily-open');
			open.addEventListener('click', () => Zotero.launchURL(paper.arxiv_url));
			actions.append(open);
		}

		row.append(actions);
		return row;
	};

	this.save = async function (paper, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = Zotero.getString('pharos-daily-saving');
		try {
			await Zotero.Pharos.Daily.saveToLibrary(paper);
			button.textContent = Zotero.getString('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			this._setStatus(e.message || Zotero.getString('pharos-daily-save-failed'));
		}
	};

	this.refresh = async function () {
		this._refreshButton.disabled = true;
		this._setStatus(Zotero.getString('pharos-daily-refreshing'));
		try {
			await Zotero.Pharos.Daily.refresh();
			// The sweep runs in the background, so the 202 says it started, not
			// that there is anything to show yet.
			this._poll();
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-daily-error'));
			this._refreshButton.disabled = false;
		}
	};

	this._poll = function () {
		const INTERVAL = 5000;
		const MAX_ATTEMPTS = 120; // ten minutes
		let attempts = 0;
		let tick = async () => {
			attempts++;
			try {
				let status = await Zotero.Pharos.Daily.getStatus();
				// `sweeping` is the date currently being swept, null when idle --
				// a direct signal, unlike inferring it from last_run.status, which
				// still reads "running" for a moment after the work is done.
				if (!status.sweeping || attempts >= MAX_ATTEMPTS) {
					this._refreshButton.disabled = false;
					await this.loadDates();
					await this.load(this._dateSelect.value);
					return;
				}
			}
			catch (e) {
				Zotero.logError(e);
				this._refreshButton.disabled = false;
				return;
			}
			_refreshTimer = setTimeout(tick, INTERVAL);
		};
		_refreshTimer = setTimeout(tick, INTERVAL);
	};
};
