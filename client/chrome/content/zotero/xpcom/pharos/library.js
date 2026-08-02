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
 * Putting a paper Pharos found into the Zotero library.
 *
 * Shared by the daily digest and literature discovery, which surface different
 * kinds of paper but land them the same way: an item, the model's reading as a
 * child note, and the PDF attached.
 *
 * Note this deliberately does NOT go through the backend's own import
 * endpoints. Those file a paper in the Pharos library, which is the web
 * client's; the point here is to land it in Zotero, where the reader, the
 * annotations and the citation machinery already are.
 */
Zotero.Pharos.Library = new function () {
	/**
	 * @param {Object} options
	 * @param {String} options.itemType - a Zotero item type
	 * @param {Object} options.fields - Zotero field name -> value; empty values
	 *     are skipped, so callers need not filter their own nulls
	 * @param {String[]} [options.authors] - display names
	 * @param {String} [options.noteHTML] - saved as a child note when non-empty
	 * @param {String} [options.pdfURL]
	 * @param {String} [options.pdfTitle]
	 * @param {Integer} [options.libraryID]
	 * @param {Integer[]} [options.collections]
	 * @return {Promise<Zotero.Item>}
	 */
	this.saveExternalPaper = async function ({
		itemType,
		fields,
		authors,
		noteHTML,
		pdfURL,
		pdfTitle,
		libraryID,
		collections,
	}) {
		libraryID = libraryID || Zotero.Libraries.userLibraryID;

		let item = new Zotero.Item(itemType);
		item.libraryID = libraryID;
		for (let [name, value] of Object.entries(fields || {})) {
			if (value === null || value === undefined || value === '') {
				continue;
			}
			// A field an item type does not have would throw and lose the whole
			// item; skipping is right because these come from external metadata
			// whose shape varies by source.
			if (!Zotero.ItemFields.isValidForType(
				Zotero.ItemFields.getID(name), Zotero.ItemTypes.getID(itemType)
			)) {
				Zotero.debug(`Pharos: skipping field ${name}, not valid for ${itemType}`);
				continue;
			}
			item.setField(name, value);
		}
		if (authors && authors.length) {
			item.setCreators(authors.map(name => ({
				creatorType: 'author',
				// The comma flag tells cleanAuthor whether the name is
				// "Last, First" or "First Last"; sources disagree on which.
				...Zotero.Utilities.cleanAuthor(name, 'author', name.includes(',')),
			})));
		}
		if (collections && collections.length) {
			item.setCollections(collections);
		}
		await item.saveTx();

		// A note rather than Extra: this is prose, and Extra is a metadata field
		// that ends up in exports and citations.
		if (noteHTML) {
			let note = new Zotero.Item('note');
			note.libraryID = libraryID;
			note.parentItemID = item.id;
			note.setNote(noteHTML);
			await note.saveTx();
		}

		if (pdfURL) {
			try {
				await Zotero.Attachments.importFromURL({
					libraryID,
					url: pdfURL,
					parentItemID: item.id,
					contentType: 'application/pdf',
					title: pdfTitle || 'PDF',
				});
			}
			catch (e) {
				// The metadata is worth keeping even when the PDF is behind a
				// paywall or the host refuses us, so this does not undo the item.
				Zotero.logError(e);
			}
		}

		return item;
	};

	/**
	 * Build note HTML from a heading, a body paragraph, and labelled sections.
	 *
	 * Everything is escaped. These strings are model output stored server-side,
	 * and building note HTML from them raw would let a crafted paper inject
	 * markup into the reader's own library.
	 *
	 * @param {Object} options
	 * @param {String} options.title
	 * @param {String} [options.summary]
	 * @param {Array} [options.sections] - [{label, text}], empty text skipped
	 * @param {String} [options.footer]
	 * @return {String}
	 */
	this.buildNote = function ({ title, summary, sections, footer }) {
		let esc = str => Zotero.Utilities.htmlSpecialChars(String(str));
		let parts = [`<h2>${esc(title)}</h2>`];
		if (summary) {
			parts.push(`<p>${esc(summary)}</p>`);
		}
		let rows = (sections || [])
			.filter(s => s && s.text)
			.map(s => `<li><strong>${esc(s.label)}</strong>: ${esc(s.text)}</li>`);
		if (rows.length) {
			parts.push(`<ul>${rows.join('')}</ul>`);
		}
		// An array gets one paragraph per entry. A caller with two footer lines
		// cannot just join them with "\n": this is HTML, where a newline is
		// whitespace, and the two would render welded into one sentence -- which
		// matters when the first line is the "a model wrote this" disclosure and
		// the second is unrelated metadata.
		for (let line of (Array.isArray(footer) ? footer : [footer]).filter(Boolean)) {
			parts.push(`<p>${esc(line)}</p>`);
		}
		return parts.join('\n');
	};
};
