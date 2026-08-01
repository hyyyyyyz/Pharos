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
	this.saveToLibrary = async function (paper, { libraryID, collections } = {}) {
		libraryID = libraryID || Zotero.Libraries.userLibraryID;

		let item = new Zotero.Item('preprint');
		item.libraryID = libraryID;
		item.setField('title', paper.title);
		if (paper.abstract) {
			item.setField('abstractNote', paper.abstract);
		}
		if (paper.arxiv_id) {
			item.setField('archiveID', `arXiv:${paper.arxiv_id}`);
			item.setField('repository', 'arXiv');
		}
		if (paper.arxiv_url) {
			item.setField('url', paper.arxiv_url);
		}
		if (paper.published_at) {
			// The API returns an ISO timestamp; Zotero wants a date field it can
			// parse, and the day is the part that matters for a preprint.
			item.setField('date', String(paper.published_at).slice(0, 10));
		}
		item.setCreators(
			(paper.authors || []).map(name => ({
				creatorType: 'author',
				...Zotero.Utilities.cleanAuthor(name, 'author', name.includes(',')),
			}))
		);
		if (collections && collections.length) {
			item.setCollections(collections);
		}
		await item.saveTx();

		// The model's reading goes in as a note rather than into Extra: it is
		// prose, it is long, and Extra is a metadata field that ends up in
		// exports and citations.
		let noteText = this.buildNote(paper);
		if (noteText) {
			let note = new Zotero.Item('note');
			note.libraryID = libraryID;
			note.parentItemID = item.id;
			note.setNote(noteText);
			await note.saveTx();
		}

		if (paper.pdf_url) {
			try {
				await Zotero.Attachments.importFromURL({
					libraryID,
					url: paper.pdf_url,
					parentItemID: item.id,
					contentType: 'application/pdf',
					title: `${paper.arxiv_id || 'arXiv'}.pdf`,
				});
			}
			catch (e) {
				// The metadata is worth keeping even when arXiv refuses the PDF,
				// so this does not undo the item.
				Zotero.logError(e);
			}
		}

		return item;
	};

	/**
	 * The model's reading of a paper, as note HTML.
	 *
	 * @param {Object} paper
	 * @return {String} empty if the paper has not been read yet
	 */
	this.buildNote = function (paper) {
		if (paper.read_status != 'done') {
			return '';
		}
		let esc = str => Zotero.Utilities.htmlSpecialChars(String(str));
		let parts = [`<h2>${esc(paper.title)}</h2>`];

		if (paper.summary_zh) {
			parts.push(`<p>${esc(paper.summary_zh)}</p>`);
		}

		const HIGHLIGHT_KEYS = ['contribution', 'innovation', 'method', 'results'];
		if (paper.highlights) {
			let rows = HIGHLIGHT_KEYS
				.filter(key => paper.highlights[key])
				.map(key => `<li><strong>${esc(Zotero.getString('pharos-daily-highlight-' + key))}</strong>: `
					+ `${esc(paper.highlights[key])}</li>`);
			if (rows.length) {
				parts.push(`<ul>${rows.join('')}</ul>`);
			}
		}

		if (paper.matched_domain) {
			parts.push(`<p>${esc(Zotero.getString('pharos-daily-matched'))}: `
				+ `${esc(paper.matched_domain)}`
				+ (paper.matched_keywords && paper.matched_keywords.length
					? ` (${esc(paper.matched_keywords.join(', '))})`
					: '')
				+ '</p>');
		}

		return parts.join('\n');
	};
};
