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
 * Paste a link, get the paper.
 *
 * Every entry point (the rail's paste box, Cmd/Ctrl+V on the item list, "save to
 * library" in Daily Papers and Find Literature) lands a paper through here, and
 * here goes through the SAME translator pipeline as the lookup panel
 * (lookup.js): Zotero.Translate.Search with an identifier. For an arXiv paper
 * that is translators/arXiv.org.js, the file the Zotero Connector runs on an
 * arxiv.org page, so the item that comes out has the Connector's fields,
 * attachments and naming without Pharos assembling any of them. When upstream
 * improves the translator, this follows with no change here.
 *
 * What this module deliberately does NOT do: choose snapshots or PDF naming
 * (the user's existing Zotero preferences decide that, inside the item saver),
 * open the reader, translate, or start a chat. It imports and reports.
 */
Zotero.Pharos.Import = new function () {
	/**
	 * Identifier types a pasted string is allowed to produce. Upstream's
	 * extractIdentifiers also returns ISBNs and PMIDs, and a bare 10- or 13-digit
	 * number on the clipboard is far likelier to be a phone number or an order
	 * reference than a book. Pharos is a paper tool, so the box takes papers.
	 */
	this.TYPES = ['arXiv', 'DOI'];

	/** The clipboard is only inspected when it is this short; see parse(). */
	this.MAX_TEXT_LENGTH = 4000;

	/** Why an import failed. The UI has one sentence for each. */
	this.REASONS = ['unrecognized', 'not-found', 'network', 'rate-limited', 'read-only', 'other'];

	/**
	 * Identifiers in a piece of text.
	 *
	 * Upstream's extractIdentifiers() takes only the FIRST kind it finds in the
	 * whole string -- any DOI hides every arXiv ID beside it -- so the text is
	 * split into whitespace-separated pieces and each is asked on its own. That
	 * is what makes several links on separate lines, or an arXiv link next to a
	 * DOI, all come through. The rules themselves are still upstream's.
	 *
	 * @param {String} text
	 * @return {Object[]} [{arXiv: '1706.03762'} | {DOI: '10.…'}], in order and
	 *     without duplicates; empty when the text is not a recognisable link
	 */
	this.parse = function (text) {
		if (typeof text != 'string' || !text.trim()) {
			return [];
		}
		let found = [];
		let seen = new Set();
		for (let piece of text.split(/[\s ]+/)) {
			if (!piece) {
				continue;
			}
			for (let identifier of Zotero.Utilities.extractIdentifiers(piece)) {
				let type = Object.keys(identifier)[0];
				if (!this.TYPES.includes(type)) {
					continue;
				}
				let key = type + ':' + identifier[type];
				if (seen.has(key)) {
					continue;
				}
				seen.add(key);
				found.push(identifier);
			}
		}
		return found;
	};

	/**
	 * The arXiv ID or DOI to dedupe on, whichever the identifier is.
	 * @return {{type: String, value: String}|null}
	 */
	function _split(identifier) {
		if (!identifier) {
			return null;
		}
		for (let type of Zotero.Pharos.Import.TYPES) {
			if (identifier[type]) {
				return { type, value: String(identifier[type]) };
			}
		}
		return null;
	}

	/**
	 * The item already in the library for an identifier, if any.
	 *
	 * arXiv is matched on archiveID == "arXiv:<id>", exactly, because that is
	 * what both the arXiv translator and the older Pharos save path write. A DOI
	 * is matched on the DOI field. Zotero.Search leaves trashed items out unless
	 * asked, which is the intended answer: someone who binned a paper should be
	 * able to bring it back by pasting it again.
	 *
	 * Fails open: a search that throws means "not found", so a database hiccup
	 * costs a duplicate at worst, never the ability to import.
	 *
	 * @param {Object} identifier
	 * @param {Integer} [libraryID]
	 * @return {Promise<Zotero.Item|null>}
	 */
	this.findExisting = async function (identifier, libraryID) {
		let parts = _split(identifier);
		if (!parts) {
			return null;
		}
		try {
			let search = new Zotero.Search();
			search.libraryID = libraryID || Zotero.Libraries.userLibraryID;
			if (parts.type == 'arXiv') {
				search.addCondition('archiveID', 'is', `arXiv:${parts.value}`);
			}
			else {
				search.addCondition('DOI', 'is', Zotero.Utilities.cleanDOI(parts.value) || parts.value);
			}
			let ids = await search.search();
			return ids.length ? Zotero.Items.get(ids[0]) : null;
		}
		catch (e) {
			Zotero.logError(e);
			return null;
		}
	};

	/**
	 * Where a paste lands, read off the main window the way lookup.js reads it:
	 * the selected library, and the selected collection if there is one.
	 *
	 * @param {Object} [pane] - a ZoteroPane; defaults to the active one
	 * @return {{libraryID: Integer, collections: Integer[]|false, error: String|null}}
	 *     `error` is 'read-only' when the library cannot take new items or files
	 */
	this.getTarget = function (pane) {
		pane = pane || Zotero.getActiveZoteroPane();
		let libraryID = Zotero.Libraries.userLibraryID;
		let collections = false;
		try {
			if (pane && pane.collectionsView) {
				libraryID = pane.getSelectedLibraryIDs()[0] || libraryID;
				let selected = pane.getSelectedCollections();
				collections = selected.length ? selected.map(c => c.id) : false;
			}
		}
		catch (e) {
			// An unusual selection (a mixed multi-select, say) falls back to the
			// user's library root rather than refusing.
			Zotero.logError(e);
		}
		let library = Zotero.Libraries.get(libraryID);
		// Both: a group can be editable for metadata while its files are not
		// (upstream checks the pair before saving attachments).
		let error = library && library.editable && library.filesEditable ? null : 'read-only';
		return { libraryID, collections, error };
	};

	/**
	 * Map whatever a failed translation threw onto one of REASONS.
	 *
	 * Translators fail with plain Errors whose only stable content is the
	 * message, plus Zotero.HTTP exceptions that carry a status, so this reads
	 * both. Anything unrecognised is 'other' rather than guessed at.
	 *
	 * @param {Error|*} e
	 * @return {String}
	 */
	this.classifyError = function (e) {
		let status = e && (e.status || (e.xmlhttp && e.xmlhttp.status));
		let text = [e && e.name, e && e.message, typeof e == 'string' ? e : '']
			.filter(Boolean).join(' ');
		if (status == 429 || /\b429\b|rate.?limit|too many requests/i.test(text)) {
			return 'rate-limited';
		}
		// arXiv answers an unknown ID with an empty Atom feed, which the
		// translator turns into a "no results" style error; a 404 from a DOI
		// resolver means the same thing.
		if (status == 404 || /\b404\b|not found|no results|no items|no translators|could not find|unrecognized|empty/i.test(text)) {
			return 'not-found';
		}
		if (status == 0 || status >= 500
				|| /timeout|timed out|offline|network|NS_ERROR|ECONN|ENOTFOUND|failed to fetch|unreachable|\b50[0-4]\b|Unexpected status/i.test(text)) {
			return 'network';
		}
		return 'other';
	};

	/**
	 * Run ONE identifier through the translator pipeline and save the result.
	 *
	 * This is the module's single seam to the network and the item saver, and it
	 * mirrors lookup.js step for step (Translate.Search, setIdentifier,
	 * getTranslators, setTranslator, translate with saveAttachments). It is also
	 * what the tests replace.
	 *
	 * @param {Object} identifier
	 * @param {Object} options
	 * @param {Integer} options.libraryID
	 * @param {Integer[]|false} [options.collections]
	 * @return {Promise<Zotero.Item[]>} the saved items; empty is a "not found"
	 * @throws whatever the translator threw
	 */
	this.translateIdentifier = async function (identifier, { libraryID, collections }) {
		let translate = new Zotero.Translate.Search();
		translate.setIdentifier(identifier);
		// Lenient about translators, as the lookup panel is: offer them all and
		// let the framework try them in priority order.
		let translators = await translate.getTranslators();
		if (!translators || !translators.length) {
			throw new Error('No translators available');
		}
		translate.setTranslator(translators);
		return translate.translate({
			libraryID,
			collections: collections || false,
			saveAttachments: true,
		});
	};

	/**
	 * Import identifiers one by one. A failure on one does not stop the rest.
	 *
	 * @param {Object[]} identifiers - as parse() returns
	 * @param {Object} [options]
	 * @param {Integer} [options.libraryID]
	 * @param {Integer[]|false} [options.collections]
	 * @param {Function} [options.onProgress] - called as (done, total)
	 * @return {Promise<{imported: Zotero.Item[], existing: Zotero.Item[],
	 *     failed: {identifier: Object, reason: String}[]}>}
	 */
	this.importIdentifiers = async function (identifiers, options = {}) {
		let result = { imported: [], existing: [], failed: [] };
		let target = this.getTarget();
		let libraryID = options.libraryID || target.libraryID;
		let collections = options.collections !== undefined ? options.collections : target.collections;

		if (!identifiers || !identifiers.length) {
			result.failed.push({ identifier: null, reason: 'unrecognized' });
			return result;
		}

		// An explicit libraryID overrides the window's, so the writability of the
		// library actually being written to is what is checked.
		let library = Zotero.Libraries.get(libraryID);
		if (!library || !library.editable || !library.filesEditable) {
			for (let identifier of identifiers) {
				result.failed.push({ identifier, reason: 'read-only' });
			}
			return result;
		}

		let done = 0;
		for (let identifier of identifiers) {
			try {
				let existing = await this.findExisting(identifier, libraryID);
				if (existing) {
					result.existing.push(existing);
					continue;
				}
				let items = await this.translateIdentifier(identifier, { libraryID, collections });
				if (items && items.length) {
					result.imported.push(...items);
				}
				else {
					result.failed.push({ identifier, reason: 'not-found' });
				}
			}
			catch (e) {
				Zotero.logError(e);
				result.failed.push({ identifier, reason: this.classifyError(e) });
			}
			finally {
				done++;
				if (options.onProgress) {
					try {
						options.onProgress(done, identifiers.length);
					}
					catch (e) {
						Zotero.logError(e);
					}
				}
			}
		}
		return result;
	};

	/**
	 * parse() then importIdentifiers(): what every entry point calls.
	 *
	 * Text with nothing recognisable is reported as one 'unrecognized' failure
	 * rather than thrown, so a caller has a single result shape to render.
	 *
	 * @param {String} text
	 * @param {Object} [options] - as importIdentifiers()
	 */
	this.importFromText = async function (text, options = {}) {
		return this.importIdentifiers(this.parse(text), options);
	};

	/**
	 * Save a paper Pharos already knows about (Daily Papers, Find Literature)
	 * through the translator pipeline, so it lands exactly like a pasted one.
	 *
	 * Returns null whenever the pipeline cannot do it -- no identifier, a
	 * read-only target, or a translator failure of any kind -- and the caller
	 * then uses Zotero.Pharos.Library.saveExternalPaper(), the path that has
	 * always worked. A paper that could be saved before must still be saveable.
	 *
	 * @param {Object} options
	 * @param {String} [options.arxivID]
	 * @param {String} [options.doi]
	 * @param {String} [options.noteHTML] - attached as a child note on success
	 * @param {Integer} [options.libraryID]
	 * @param {Integer[]} [options.collections]
	 * @return {Promise<Zotero.Item|null>}
	 */
	this.saveWithTranslator = async function ({ arxivID, doi, noteHTML, libraryID, collections }) {
		let identifier = arxivID ? { arXiv: arxivID } : doi ? { DOI: doi } : null;
		if (!identifier) {
			return null;
		}
		libraryID = libraryID || Zotero.Libraries.userLibraryID;
		try {
			let library = Zotero.Libraries.get(libraryID);
			if (!library || !library.editable || !library.filesEditable) {
				return null;
			}
			let items = await this.translateIdentifier(identifier, {
				libraryID,
				collections: collections && collections.length ? collections : false,
			});
			let item = items && items.find(i => i.isRegularItem());
			if (!item) {
				return null;
			}
			if (noteHTML) {
				let note = new Zotero.Item('note');
				note.libraryID = item.libraryID;
				note.parentItemID = item.id;
				note.setNote(noteHTML);
				await note.saveTx();
			}
			return item;
		}
		catch (e) {
			Zotero.debug('Pharos: translator import failed, falling back: ' + (e && e.message));
			return null;
		}
	};
};
