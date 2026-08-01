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
 * Literature discovery.
 *
 * Searches arXiv and OpenAlex through the backend, which ranks what comes back
 * and has a model read each result, then lands anything worth keeping in the
 * local library. This is the "discover" end of the arc that translation and
 * chat sit at the other end of.
 *
 * Zotero can already search these sources through its translators; what this
 * adds is the reading -- a Chinese summary and a breakdown of contribution,
 * trick, method, results and limitations, attached to the item as a note.
 */
Zotero.Pharos.Discovery = new function () {
	/** What the backend will search. Both are open, neither needs a key. */
	this.SOURCES = ['arxiv', 'openalex'];

	/**
	 * Run a search. Returns once the backend has queried every source.
	 *
	 * @param {String} query
	 * @param {Object} [options]
	 * @param {String[]} [options.sources]
	 * @param {Integer} [options.limit]
	 * @return {Promise<Object>} the search, with its results
	 */
	this.search = function (query, { sources, limit } = {}) {
		return Zotero.Pharos.API.request('POST', '/api/discovery/search', {
			body: {
				query,
				sources: sources || this.SOURCES,
				limit: limit || 20,
			},
			// A search fans out to external providers and has to wait for the
			// slowest of them, so it outlasts the default request timeout.
			timeout: 180000,
		});
	};

	/**
	 * Past searches, so a session can be reopened rather than re-run.
	 *
	 * @return {Promise<Object[]>}
	 */
	this.getSearches = function () {
		return Zotero.Pharos.API.request('GET', '/api/discovery/searches');
	};

	/**
	 * @param {String} searchID
	 * @return {Promise<Object>}
	 */
	this.getSearch = function (searchID) {
		return Zotero.Pharos.API.request('GET', `/api/discovery/searches/${searchID}`);
	};

	/**
	 * Have the model read one result properly.
	 *
	 * Results arrive analysed by rules; this upgrades one to an LLM reading. It
	 * is per-result rather than automatic for the whole page because reading
	 * twenty papers costs twenty model calls, and most searches are skimmed.
	 *
	 * @param {String} resultID
	 * @return {Promise<Object>} the re-analysed result
	 */
	this.analyze = function (resultID) {
		return Zotero.Pharos.API.request(
			'POST', `/api/discovery/results/${resultID}/analyze`, { timeout: 180000 }
		);
	};

	/**
	 * Save a result into the local Zotero library.
	 *
	 * @param {Object} result - a LiteratureResultOut
	 * @param {Object} [options] - libraryID, collections
	 * @return {Promise<Zotero.Item>}
	 */
	this.saveToLibrary = function (result, { libraryID, collections } = {}) {
		let isPreprint = this.isPreprint(result);
		return Zotero.Pharos.Library.saveExternalPaper({
			itemType: isPreprint ? 'preprint' : 'journalArticle',
			fields: {
				title: result.title,
				abstractNote: result.abstract,
				date: result.year ? String(result.year) : null,
				DOI: result.doi,
				url: result.url,
				// The venue means different things to the two item types, and
				// setting the wrong one silently drops it.
				...(isPreprint
					? { repository: result.venue || 'arXiv' }
					: { publicationTitle: result.venue }),
			},
			authors: result.authors,
			noteHTML: this.buildNote(result),
			pdfURL: result.pdf_url,
			pdfTitle: `${result.doi || result.title}.pdf`.slice(0, 120),
			libraryID,
			collections,
		});
	};

	/**
	 * Whether a result should be filed as a preprint rather than an article.
	 *
	 * A DOI is not the test: arXiv mints DOIs too. What decides it is whether
	 * any source that returned this paper is a preprint server.
	 *
	 * @param {Object} result
	 * @return {Boolean}
	 */
	this.isPreprint = function (result) {
		let sources = result.sources || [];
		if (sources.includes('arxiv') && sources.length == 1) {
			return true;
		}
		// OpenAlex indexes preprints too and says so in the venue.
		return !result.venue || /arxiv|preprint|biorxiv|medrxiv/i.test(result.venue);
	};

	/**
	 * The model's reading of a result, as note HTML.
	 *
	 * @param {Object} result
	 * @return {String} empty when nothing has been written about it
	 */
	this.buildNote = function (result) {
		const SECTION_KEYS = ['contribution', 'core_trick', 'method', 'results', 'limitations'];
		let sections = SECTION_KEYS.map(key => ({
			label: Zotero.getString('pharos-discovery-section-' + key.replace('_', '-')),
			text: result[key],
		}));
		if (!result.summary_zh && !sections.some(s => s.text)) {
			return '';
		}

		let footer = null;
		if (result.analysis_mode == 'rules') {
			// Said plainly, because a rules-based reading is a placeholder and a
			// note that does not admit it would be mistaken for the model's.
			footer = Zotero.getString('pharos-discovery-rules-note');
		}
		else if (result.analysis_warning) {
			footer = result.analysis_warning;
		}

		return Zotero.Pharos.Library.buildNote({
			title: result.title,
			summary: result.summary_zh,
			sections,
			footer,
		});
	};
};
