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
 * they are plain BaseModels rather than the CamelModel used there. Nothing here
 * renames a wire field, because the day a key is camel-cased on the way in is
 * the day it has to be un-camel-cased on the way back out.
 *
 * Nothing in this module catches. SignedOutError, the {"detail": ...} the API
 * layer has already unwrapped into an Error with `.status`, and a transport
 * failure all reach the view, which is the only layer that can tell the reader
 * which of those happened. The single exception is findInLibrary(), which is a
 * badge and must never take the list down with it.
 */
Zotero.Pharos.Daily = new function () {
	/**
	 * The backend allows one read 90s (READ_TIMEOUT_SECONDS in
	 * pharos/daily/service.py). The API layer's default is 30s, which would abort
	 * a read the server is still perfectly happy to finish -- and the row would be
	 * written anyway, so the user sees a failure that did not happen.
	 */
	this.READ_TIMEOUT = 180000;

	/**
	 * Today's date in the digest's terms.
	 *
	 * Local, not UTC. The digest is keyed by the date the user is having, and a
	 * reader in UTC+8 asking at 09:00 for "today" must not be handed yesterday.
	 *
	 * The server's own today() is local to the server, so the two agree only in a
	 * shared timezone. A view must not assume this date appears in getDates().
	 *
	 * @return {String} YYYY-MM-DD
	 */
	this.today = function () {
		let now = new Date();
		let pad = n => String(n).padStart(2, '0');
		return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
	};

	/**
	 * Days with something in them for THIS account, newest first.
	 *
	 * Matching happens per request against the caller's own directions, so a day
	 * the shared sweep filled but this account matches nothing in is absent
	 * altogether rather than present with a total of zero.
	 *
	 * These counts are also the only honest progress indicator during a sweep:
	 * the reading phase commits one paper at a time, whereas a run row's
	 * fetched/read_done/read_failed columns are written once at the end and read
	 * zero for the whole time anyone would want to watch them.
	 *
	 * @return {Promise<Object[]>} [{ date, total, read, pending, failed }]
	 */
	this.getDates = async function () {
		let res = await Zotero.Pharos.API.request('GET', '/api/daily/dates');
		// A body of "null" or an empty response is not a day with no papers, but
		// the caller has nothing to do with the difference and every caller would
		// otherwise have to write the same guard before iterating.
		return Array.isArray(res) ? res : [];
	};

	/**
	 * One day's papers, best first and already capped.
	 *
	 * A day with no papers is a 200 with an empty list, never a 404. What tells
	 * the two empty days apart is `run`: null means this date was never swept in
	 * this install, non-null with no papers means it was swept and nothing the
	 * caller cares about came back. The two want different words on screen, so
	 * do not collapse them.
	 *
	 * @param {String} [date] - YYYY-MM-DD, defaulting to today. Anything else is
	 *     a 400 from the path regex rather than an empty day.
	 * @return {Promise<Object>} { date, total, run, papers }
	 */
	this.getDay = function (date) {
		return Zotero.Pharos.API.request(
			'GET', `/api/daily/${encodeURIComponent(date || this.today())}`
		);
	};

	/**
	 * Whether the reading layer is configured, and what the digest has done
	 * lately.
	 *
	 * Normalised here rather than in the view: every field below is optional on
	 * the wire, and a view left to invent its own defaults invents a different
	 * one at each use site.
	 *
	 * Two of those defaults are load-bearing:
	 *
	 *   - `llm_configured` absent means TRUE. Never accuse the operator of a
	 *     misconfiguration on the strength of a field that did not arrive.
	 *   - `sweeping` is the sweeper's own in-memory state and is the ONLY
	 *     authoritative "a sweep is running now". `last_run.status` reads
	 *     "running" forever for a row orphaned by a backend restart, so polling
	 *     on it polls forever.
	 *
	 * @return {Promise<Object>} { llm_configured, provider, directions, last_run,
	 *     today, sweeping }
	 */
	this.getStatus = async function () {
		let res = await Zotero.Pharos.API.request('GET', '/api/daily/status') || {};
		let provider = res.provider || null;
		return {
			llm_configured: res.llm_configured !== false,
			provider: provider && {
				name: provider.name || '',
				model: provider.model || '',
				base_url: provider.base_url || '',
				configured: !!provider.configured,
			},
			directions: Array.isArray(res.directions) ? res.directions : [],
			last_run: res.last_run || null,
			today: res.today || null,
			sweeping: res.sweeping || null,
		};
	};

	/**
	 * Ask the backend to sweep arXiv again.
	 *
	 * The run comes back already persisted, so its date and id can seed the
	 * progress display instead of waiting a poll interval for the first status.
	 * Its counters cannot: they are zero until the sweep finishes.
	 *
	 * @param {Object} [options]
	 * @param {String} [options.date] - YYYY-MM-DD, defaulting to the SERVER's
	 *     today, which is not necessarily this machine's
	 * @param {Integer} [options.days] - 1..7, a backfill window ending at `date`
	 * @param {Boolean} [options.reread] - re-read papers already read
	 * @return {Promise<Object>} the run row, status "running"
	 */
	this.refresh = function ({ date, days, reread } = {}) {
		// Built by omission rather than by listing: RefreshRequest is declared
		// extra="forbid", so a key it does not know is a 422, and a key it knows
		// carrying undefined would serialise to null and overrule the default.
		let body = {};
		if (date !== undefined) {
			body.date = date;
		}
		if (days !== undefined) {
			body.days = days;
		}
		if (reread !== undefined) {
			body.reread = reread;
		}
		return Zotero.Pharos.API.request('POST', '/api/daily/refresh', { body });
	};

	/**
	 * Have the model read one paper now. Blocking: the answer is the response.
	 *
	 * Three failures, and the view has to tell them apart:
	 *
	 *   - A 200 whose read_status is "error" and whose read_error is set. The
	 *     provider failed and the row was written; the promise RESOLVES. Treating
	 *     a resolved promise as success shows a spinner turning into an unchanged
	 *     card with nothing said.
	 *   - 503. No provider is configured, nothing was attempted and nothing was
	 *     written. The fix is configuration, not a retry.
	 *   - 404. No such paper, or it has no abstract to read.
	 *
	 * What comes back is the whole paper as THIS caller sees it -- relevance and
	 * recommendation are scored against their directions, not a shared rubric --
	 * so replace the row wholesale rather than merging fields into the old one.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object>} the updated DailyPaperOut
	 */
	this.readPaper = function (paperID) {
		return Zotero.Pharos.API.request(
			'POST',
			`/api/daily/papers/${encodeURIComponent(paperID)}/read`,
			{ timeout: this.READ_TIMEOUT }
		);
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
	 * The Zotero item this digest paper was already saved as, if any.
	 *
	 * Matched on archiveID rather than on imported_paper_id: that column names a
	 * row in the Pharos web library, is a single column on a SHARED row, and is
	 * blanked by the API for anyone who does not own it. archiveID is what
	 * saveToLibrary() writes, so it is the only field that answers "is this one
	 * already in MY library".
	 *
	 * A trashed item is deliberately NOT found -- someone who binned it should be
	 * offered the import again.
	 *
	 * @param {Object} paper
	 * @param {Object} [options]
	 * @param {Integer} [options.libraryID]
	 * @return {Promise<Zotero.Item|null>}
	 */
	this.findInLibrary = async function (paper, { libraryID } = {}) {
		if (!paper || !paper.arxiv_id) {
			return null;
		}
		try {
			let search = new Zotero.Search();
			search.libraryID = libraryID || Zotero.Libraries.userLibraryID;
			search.addCondition('archiveID', 'is', `arXiv:${paper.arxiv_id}`);
			let ids = await search.search();
			return ids.length ? Zotero.Items.get(ids[0]) : null;
		}
		catch (e) {
			// The badge fails OFF rather than wrong, and never takes the list with it.
			Zotero.logError(e);
			return null;
		}
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
