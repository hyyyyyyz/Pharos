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
 * The daily arXiv digest.
 *
 * The backend sweeps arXiv against the directions the user wrote, has a model
 * read what it finds, and keeps the result per day. This is the client's view of
 * that, plus the one thing only the client can do: put a paper into the local
 * library, PDF and all, so it is there to read and annotate.
 *
 * Note the backend's daily schemas are snake_case, unlike the AI chat ones --
 * they are plain BaseModels rather than the CamelModel used there.
 */
Zotero.Pharos.Daily = new function () {
	/**
	 * Today's date in the digest's terms.
	 *
	 * Local, not UTC. The digest is keyed by the date the user is having, and a
	 * reader in UTC+8 asking at 09:00 for "today" must not be handed yesterday.
	 *
	 * @return {String} YYYY-MM-DD
	 */
	this.today = function () {
		let now = new Date();
		let pad = n => String(n).padStart(2, '0');
		return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
	};

	/**
	 * @param {String} [date] - YYYY-MM-DD, defaulting to today
	 * @return {Promise<Object>} { date, total, run, papers }
	 */
	this.getDay = function (date) {
		return Zotero.Pharos.API.request('GET', `/api/daily/${date || this.today()}`);
	};

	/**
	 * Recent days that have something in them, newest first.
	 *
	 * @return {Promise<Object[]>}
	 */
	this.getDates = function () {
		return Zotero.Pharos.API.request('GET', '/api/daily/dates');
	};

	/**
	 * @return {Promise<Object>} whether the reading model is configured, and
	 *     what the digest has done lately
	 */
	this.getStatus = function () {
		return Zotero.Pharos.API.request('GET', '/api/daily/status');
	};

	/**
	 * Ask the backend to sweep arXiv again.
	 *
	 * @return {Promise<Object>} the run, which starts in progress
	 */
	this.refresh = function () {
		return Zotero.Pharos.API.request('POST', '/api/daily/refresh');
	};

	/**
	 * Save a digest paper into the local Zotero library.
	 *
	 * Deliberately NOT the backend's /import endpoint: that files the paper in
	 * the Pharos library, which is the web client's library. Here the point is to
	 * get it into Zotero, where the reader, the annotations and the citation
	 * machinery already are.
	 *
	 * @param {Object} paper - a DailyPaperOut
	 * @param {Object} [options]
	 * @param {Integer} [options.libraryID]
	 * @param {Integer[]} [options.collections]
	 * @return {Promise<Zotero.Item>} the created item
	 */
	this.saveToLibrary = function (paper, { libraryID, collections } = {}) {
		return Zotero.Pharos.Library.saveExternalPaper({
			itemType: 'preprint',
			fields: {
				title: paper.title,
				abstractNote: paper.abstract,
				archiveID: paper.arxiv_id ? `arXiv:${paper.arxiv_id}` : null,
				repository: paper.arxiv_id ? 'arXiv' : null,
				url: paper.arxiv_url,
				// The API returns an ISO timestamp; the day is the part that
				// matters for a preprint.
				date: paper.published_at ? String(paper.published_at).slice(0, 10) : null,
			},
			authors: paper.authors,
			noteHTML: this.buildNote(paper),
			pdfURL: paper.pdf_url,
			pdfTitle: `${paper.arxiv_id || 'arXiv'}.pdf`,
			libraryID,
			collections,
		});
	};

	/**
	 * The model's reading of a paper, as note HTML.
	 *
	 * @param {Object} paper
	 * @return {String} empty if the paper has not been read yet
	 */
	this.buildNote = function (paper) {
		// Branching on read_status rather than on whether summary_zh is truthy,
		// which the backend's own schema asks clients to do.
		if (paper.read_status != 'done') {
			return '';
		}

		const HIGHLIGHT_KEYS = ['contribution', 'innovation', 'method', 'results'];
		let footer = null;
		if (paper.matched_domain) {
			footer = `${Zotero.getString('pharos-daily-matched')}: ${paper.matched_domain}`
				+ (paper.matched_keywords && paper.matched_keywords.length
					? ` (${paper.matched_keywords.join(', ')})`
					: '');
		}

		return Zotero.Pharos.Library.buildNote({
			title: paper.title,
			summary: paper.summary_zh,
			sections: HIGHLIGHT_KEYS.map(key => ({
				label: Zotero.getString('pharos-daily-highlight-' + key),
				text: paper.highlights && paper.highlights[key],
			})),
			footer,
		});
	};
};
