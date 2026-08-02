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
 * and extracts a structure from each abstract, then lands anything worth
 * keeping in the local library. This is the "discover" end of the arc that
 * translation and chat sit at the other end of.
 *
 * Zotero can already search these sources through its translators; what this
 * adds is the reading -- a breakdown of contribution, trick, method, results
 * and limitations, attached to the item as a note.
 *
 * The load-bearing distinction in here is HOW that breakdown was produced. A
 * fresh search comes back `analysis_mode: "rules"`: sentences cut out of the
 * English abstract by cue matching, with `summary_zh` empty. Only an explicit
 * per-result analyze() call sends a paper to a model and fills `summary_zh` in.
 * Presenting one as the other is the failure this module's helpers exist to
 * make impossible, so every one of them fails closed toward "rules".
 */
Zotero.Pharos.Discovery = new function () {
	/**
	 * A Fluent string with no arguments.
	 *
	 * Wrapped rather than called bare because Zotero.getString() THROWS in
	 * en-US for an id it cannot resolve, and these helpers run inside a render
	 * loop where one throw would take the whole window down to a blank pane.
	 * The id is at least a legible placeholder, and the throw is logged.
	 *
	 * Note that this is only safe for ids with NO arguments. Handed a params
	 * argument, Zotero.getString routes to the .properties bundle, where no
	 * pharos-* id exists -- callers that need arguments use
	 * Zotero.ftl.formatValueSync directly.
	 */
	function _str(id) {
		try {
			return Zotero.getString(id);
		}
		catch (e) {
			Zotero.logError(e);
			return id;
		}
	}

	function _text(value) {
		return value === null || value === undefined ? '' : String(value).trim();
	}


	//
	// Constants
	//
	// These mirror SearchCreate in backend/pharos/api/research_schemas.py and
	// projects.run_search. They are the client-side guard that stops a 422
	// round trip; they are not the authority.
	//

	/** What the backend will search. Both are open, neither needs a key. */
	this.SOURCES = ['arxiv', 'openalex'];

	/**
	 * How each provider is spelled to a reader.
	 *
	 * The wire ids are lowercase; "arxiv" and "openalex" are not how either
	 * project writes its own name, and a UI that prints the raw id looks like
	 * it is leaking a database column.
	 */
	this.SOURCE_NAMES = { arxiv: 'arXiv', openalex: 'OpenAlex' };

	/** Every status a persisted run can carry. See statusStringID(). */
	this.STATUSES = ['running', 'complete', 'partial', 'error'];

	this.MIN_QUERY = 2;
	this.MAX_QUERY = 500;
	this.MIN_LIMIT = 1;
	this.MAX_LIMIT = 50;
	this.DEFAULT_LIMIT = 20;


	//
	// Network
	//

	/**
	 * Run a search. Returns once the backend has queried every source.
	 *
	 * A run where every provider died is NOT a rejection: the backend returns
	 * 201 with status "error", result_count 0 and a populated `errors` map, so
	 * that the attempt is still a saved, reopenable record. Callers must render
	 * that, not throw it away.
	 *
	 * @param {String} query
	 * @param {Object} [options]
	 * @param {String[]} [options.sources]
	 * @param {Integer} [options.limit]
	 * @param {String} [options.projectID] - link the run to a project
	 * @return {Promise<Object>} the search, with its results
	 */
	this.search = function (query, { sources, limit, projectID } = {}) {
		let body = {
			// Pydantic's min_length applies to the raw string while the service
			// re-checks the trimmed one, so a query of two spaces would pass the
			// schema and fail the service with a 400. Trimming here means the
			// string that was measured is the string that gets sent.
			query: _text(query),
			sources: sources || this.SOURCES,
			limit: limit || this.DEFAULT_LIMIT,
			// SearchCreate is extra="forbid", and the key is spread in rather
			// than sent as null: null is accepted, but omission is what "no
			// project" means, and no other key may appear here at all.
			...(projectID && typeof projectID == 'string' ? { project_id: projectID } : {}),
		};
		return Zotero.Pharos.API.request('POST', '/api/discovery/search', {
			body,
			// A search fans out to external providers and has to wait for the
			// slowest of them, so it outlasts the default request timeout.
			timeout: 180000,
		});
	};

	/**
	 * Past searches, newest first, so a session can be reopened rather than
	 * re-run.
	 *
	 * Each element already carries its complete `results` array -- the list
	 * endpoint serialises through the same search_out() as the detail endpoint.
	 * A history rail therefore renders a past run with zero extra requests, and
	 * a per-item fetch would be a wasted round trip.
	 *
	 * @param {Object} [options]
	 * @param {String} [options.projectID] - only runs linked to this project
	 * @return {Promise<Object[]>}
	 */
	this.getSearches = function ({ projectID } = {}) {
		let path = '/api/discovery/searches';
		if (projectID && typeof projectID == 'string') {
			path += '?project_id=' + encodeURIComponent(projectID);
		}
		return Zotero.Pharos.API.request('GET', path);
	};

	/**
	 * One search, refreshed.
	 *
	 * Needed only to re-read a run that is already open; opening one from
	 * history does not need it, because getSearches() already carried the
	 * results. Rejects 404 for an id this account does not own.
	 *
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
	 * On 409 (no provider configured) and 503 (the provider failed) the stored
	 * row is untouched -- analyze_result raises before the first field
	 * assignment and the session is rolled back -- so the caller must leave the
	 * existing card content on screen and add an inline message beside it.
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


	//
	// Presentation helpers
	//
	// Sync, no network, and on the module rather than in the window so that
	// they can be pinned by tests without loading a window -- the same reason
	// isPreprint() and buildNote() live here.
	//

	/**
	 * A provider's display name.
	 *
	 * Never returns a bare lowercase wire id for a provider we know about.
	 *
	 * @param {String} id
	 * @return {String}
	 */
	this.sourceName = function (id) {
		return this.SOURCE_NAMES[String(id).toLowerCase()] || String(id);
	};

	/**
	 * A list of providers, joined for reading.
	 *
	 * Used for both meanings of `sources` on the wire: what a run REQUESTED
	 * (LiteratureSearchOut.sources, which may include a provider that then
	 * errored) and what corroborated a paper after de-duplication
	 * (LiteratureResultOut.sources). The join is localized because zh-CN uses an
	 * ideographic comma.
	 *
	 * @param {String[]} ids
	 * @return {String}
	 */
	this.sourceLabel = function (ids) {
		if (!ids || !Array.isArray(ids) || !ids.length) {
			return _str('pharos-discovery-source-unknown');
		}
		return ids.map(id => this.sourceName(id))
			.join(_str('pharos-discovery-source-separator'));
	};

	/**
	 * The string id for a run's status.
	 *
	 * Fails closed: a status this build does not recognise reads as a failure
	 * rather than as success, because the alternative is telling someone a run
	 * completed when nobody knows whether it did.
	 *
	 * @param {String} status
	 * @return {String}
	 */
	this.statusStringID = function (status) {
		return this.STATUSES.includes(status)
			? 'pharos-discovery-status-' + status
			: 'pharos-discovery-status-error';
	};

	/**
	 * Whether a result's analysis came from rules rather than a model.
	 *
	 * Deliberately `!= 'llm'` and not `== 'rules'`. Anything that is not exactly
	 * the string a model reading is recorded under -- a missing field, a value
	 * added by a newer backend, a half-built object -- has to read as rules,
	 * because the failure mode in the other direction is an English abstract
	 * extract presented as a Chinese AI summary.
	 *
	 * @param {Object} result
	 * @return {Boolean}
	 */
	this.isRules = function (result) {
		return !result || result.analysis_mode != 'llm';
	};

	/**
	 * @param {Object} result
	 * @return {String} string id for the analysis-mode chip
	 */
	this.modeStringID = function (result) {
		return this.isRules(result)
			? 'pharos-discovery-mode-rules'
			: 'pharos-discovery-mode-llm';
	};

	/**
	 * What to show in the "key idea" block, and what kind of thing it is.
	 *
	 * This must never read result.abstract and never read result.summary_zh.
	 * summary_zh is "" for every rules row, so `summary_zh || abstract` -- what
	 * this window did before -- puts the raw English abstract in the box a
	 * Chinese AI summary would occupy, with the same styling and nothing on
	 * screen to separate them. The abstract gets its own labelled block.
	 *
	 * The 'extracted' state is real content, not a placeholder: rule_summary()
	 * always fills core_trick, falling back to the first sentence and then to
	 * the cleaned title. The web client throws that away and prints "not
	 * generated yet"; half the honest content the backend produces is invisible
	 * as a result.
	 *
	 * @param {Object} result
	 * @return {Object} { text: String, state: 'ai'|'extracted'|'empty' }
	 */
	this.trick = function (result) {
		let text = _text(result && result.core_trick);
		if (!this.isRules(result)) {
			return text
				? { text, state: 'ai' }
				: { text: _str('pharos-discovery-trick-empty'), state: 'empty' };
		}
		return text
			? { text, state: 'extracted' }
			: { text: _str('pharos-discovery-trick-pending'), state: 'empty' };
	};

	/**
	 * The server's own warning about a rules-mode analysis, raw.
	 *
	 * English and server-authored, so it is the `title` of the warning block
	 * rather than its visible text -- the visible text is the localized
	 * pharos-discovery-mode-rules-detail. Keeping the raw sentence in the DOM
	 * means the wire value stays inspectable without being the only surface it
	 * has.
	 *
	 * analysis_warning and analysis_model never co-occur: analyze_result sets
	 * mode/model/warning together, and rule_summary sets the other pair.
	 *
	 * @param {Object} result
	 * @return {String} empty when there is nothing to warn about
	 */
	this.analysisWarning = function (result) {
		return this.isRules(result) && result && result.analysis_warning
			? String(result.analysis_warning)
			: '';
	};

	/**
	 * Turn an analyze() rejection into something a reader can act on.
	 *
	 * Ordered; first match wins. 409 and 503 are the two that matter most,
	 * because both leave the stored rules result completely intact and their
	 * strings say so -- dumping the server's raw error, which is what happened
	 * before, leaves no way to tell whether the earlier reading was lost.
	 *
	 * `.status` is reliable here: Zotero.Pharos.API._toReadableError copies it
	 * onto the wrapped Error when FastAPI supplied a detail, and otherwise
	 * returns the raw UnexpectedStatusException, which carries it too.
	 *
	 * @param {Error} e
	 * @return {Object} { id: String, args: Object|null }
	 */
	this.analysisFailure = function (e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			return { id: 'pharos-error-signed-out-detail', args: null };
		}
		if (e instanceof Zotero.HTTP.TimeoutException || !e || !e.message || e.status === 0) {
			return { id: 'pharos-error-unreachable', args: null };
		}
		if (e.status == 409) {
			return { id: 'pharos-discovery-analyze-no-provider', args: null };
		}
		if (e.status == 503) {
			return { id: 'pharos-discovery-analyze-provider-failed', args: null };
		}
		if (e.status == 400) {
			// An OpenAlex record whose inverted abstract index was missing has
			// abstract == "", and analyze_result refuses before calling anyone.
			return { id: 'pharos-discovery-analyze-no-abstract', args: null };
		}
		return {
			id: 'pharos-discovery-analyze-failed',
			args: { error: String(e.message).slice(0, 300) },
		};
	};

	/**
	 * Whether a form is submittable, and why not.
	 *
	 * Ordered the way someone fills the form in. Returning a string id rather
	 * than a sentence keeps this testable without a locale.
	 *
	 * @param {Object} form - query, sources, limit
	 * @return {Object|null} { id, args }, or null when the form may be sent
	 */
	this.searchProblem = function ({ query, sources, limit } = {}) {
		let clean = _text(query);
		if (clean.length < this.MIN_QUERY) {
			return { id: 'pharos-discovery-need-query', args: { min: this.MIN_QUERY } };
		}
		if (clean.length > this.MAX_QUERY) {
			return { id: 'pharos-discovery-query-too-long', args: { max: this.MAX_QUERY } };
		}
		if (!sources || !sources.length) {
			return { id: 'pharos-discovery-need-source', args: null };
		}
		if (!Number.isInteger(limit) || limit < this.MIN_LIMIT || limit > this.MAX_LIMIT) {
			return {
				id: 'pharos-discovery-limit-range',
				args: { min: this.MIN_LIMIT, max: this.MAX_LIMIT },
			};
		}
		return null;
	};

	/**
	 * File a whole selection into one project.
	 *
	 * Batch rather than per-paper because the alternative -- what this window
	 * did before -- is twelve modal pickers for twelve papers.
	 *
	 * Never rejects for a partial or total failure: it resolves with the counts
	 * even when nothing landed, so the caller can re-select exactly failedIDs
	 * and offer a retry rather than losing the selection to an exception. It
	 * DOES throw synchronously for a blank project or an empty selection, which
	 * are programming errors -- the window guards both and shows a form hint.
	 *
	 * @param {Object} options
	 * @param {String} options.projectID
	 * @param {String[]} options.resultIDs
	 * @param {Set|Array} [options.existingResultIDs] - already on the project
	 * @return {Promise<Object>} { added, skipped, failed, failedIDs }
	 */
	this.addToProject = function ({ projectID, resultIDs, existingResultIDs } = {}) {
		if (!projectID || typeof projectID != 'string') {
			throw new Error('Discovery.addToProject() needs a project id');
		}
		let ids = Array.from(resultIDs || []);
		if (!ids.length) {
			throw new Error('Discovery.addToProject() needs at least one result');
		}

		let existing = new Set(existingResultIDs || []);
		let outcome = { added: 0, skipped: 0, failed: 0, failedIDs: [] };
		let pending = [];
		for (let id of ids) {
			if (existing.has(id)) {
				outcome.skipped++;
			}
			else {
				pending.push(id);
			}
		}
		if (!pending.length) {
			return Promise.resolve(outcome);
		}

		return Promise.allSettled(
			pending.map(id => Zotero.Pharos.Projects.addSource(projectID, id))
		).then((settled) => {
			settled.forEach((entry, i) => {
				if (entry.status == 'fulfilled') {
					outcome.added++;
					return;
				}
				// Logged individually: the caller only gets counts, and a batch
				// of twenty that half failed has twenty different reasons.
				Zotero.logError(entry.reason);
				outcome.failed++;
				outcome.failedIDs.push(pending[i]);
			});
			return outcome;
		});
	};


	//
	// Library
	//

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

		// Every note says who wrote it. This one is filed permanently in the
		// library, looking exactly like the reader's own notes, while the window
		// that knew its provenance is closed -- so the disclosure has to travel
		// with the note rather than stay on the card.
		let footer = [];
		if (result.analysis_mode == 'rules') {
			// Said plainly, because a rules-based reading is a placeholder and a
			// note that does not admit it would be mistaken for the model's.
			footer.push(Zotero.getString('pharos-discovery-rules-note'));
		}
		else if (result.analysis_mode == 'llm') {
			footer.push(result.analysis_model
				? Zotero.ftl.formatValueSync('pharos-discovery-note-llm',
					{ model: result.analysis_model })
				: Zotero.getString('pharos-discovery-note-llm-unknown'));
			// The deep read leaves limitations at the rules value on purpose, so
			// a note that credits the whole list to the model would be wrong
			// about exactly one line of it.
			if (String(result.limitations || '').trim()) {
				footer.push(Zotero.getString('pharos-discovery-note-limitations'));
			}
		}
		// Not an `else`: a warning is worth carrying whatever the mode was.
		if (result.analysis_warning) {
			footer.push(result.analysis_warning);
		}

		return Zotero.Pharos.Library.buildNote({
			title: result.title,
			summary: result.summary_zh,
			sections,
			footer,
		});
	};
};
