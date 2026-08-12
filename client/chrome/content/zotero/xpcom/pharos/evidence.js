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
 * Save verbatim passages selected in the PDF reader as page-addressable
 * evidence.
 *
 * The reader's custom-event API is intentionally used instead of changing the
 * reader submodule.  It gives us the exact attachment that is open and the PDF
 * position that was selected, while keeping the integration removable when the
 * upstream reader changes.
 */
Zotero.Pharos.Evidence = new function () {
	const MAX_STATUS_LENGTH = 240;
	const MAX_COORD = 14400;
	const MAX_RECTS = 400;

	/**
	 * Resolve a Fluent value without routing parameterised ids through the old
	 * .properties bundle.  This module currently has no parameterised strings,
	 * but keeping the helper here makes the error path safe in every locale.
	 */
	function _str(id, args) {
		if (args === undefined) {
			return Zotero.getString(id);
		}
		let value = Zotero.ftl.formatValueSync(id, args);
		return value === null || value === undefined ? id : value;
	}

	function _isPDFReader(reader) {
		return !!reader && (reader.type == 'pdf' || reader._type == 'pdf');
	}

	function _quoteText(annotation) {
		if (!annotation || typeof annotation.text != 'string') {
			return '';
		}
		return annotation.text.trim();
	}

	/**
	 * Convert one reader rectangle [left, bottom, right, top] to the API's
	 * {x, y, w, h} PDF-point shape.  Reader positions are already in PDF user
	 * space; this is a shape conversion, not a screen-pixel conversion.
	 */
	function _rect(rect) {
		if (!Array.isArray(rect) || rect.length != 4) {
			return null;
		}
		let values = rect.slice(0, 4).map(Number);
		if (values.some(value => !Number.isFinite(value))) {
			return null;
		}
		let x = Math.min(values[0], values[2]);
		let y = Math.min(values[1], values[3]);
		let w = Math.abs(values[2] - values[0]);
		let h = Math.abs(values[3] - values[1]);
		// The backend refuses zero-area and absurdly large PDF rectangles.  Do
		// not send a partly-valid selection: omitting geometry preserves the
		// quote and its server-resolved page without inventing a region.
		if (!(w > 0 && h > 0)
				|| Math.abs(x) > MAX_COORD || Math.abs(y) > MAX_COORD
				|| Math.abs(w) > MAX_COORD || Math.abs(h) > MAX_COORD) {
			return null;
		}
		return { x, y, w, h };
	}

	/**
	 * Return valid geometry only for a selection contained on one page.
	 *
	 * `pageIndex` is deliberately checked and sent only as an untrusted one-based
	 * hint: it is zero-based in the reader, while the evidence API resolves and
	 * stores its authoritative page_no from extracted chunks. A selection with
	 * nextPageRects cannot be represented by the current evidence schema, so it
	 * gets quote-only evidence.
	 */
	this._rectsForAnnotation = function (annotation) {
		let position = annotation && annotation.position;
		if (!position || !Number.isInteger(position.pageIndex) || position.pageIndex < 0) {
			return null;
		}
		if (Array.isArray(position.nextPageRects) && position.nextPageRects.length) {
			return null;
		}
		if (!Array.isArray(position.rects) || !position.rects.length) {
			return null;
		}
		if (position.rects.length > MAX_RECTS) {
			return null;
		}
		let rects = position.rects.map(_rect);
		return rects.some(rect => !rect) ? null : rects;
	};

	function _setStatus(controls, text, state) {
		if (!controls || !controls.status || !controls.status.isConnected) {
			return;
		}
		controls.status.textContent = text;
		controls.status.dataset.state = state || '';
		controls.status.hidden = !text;
	}

	function _setBusy(controls, busy) {
		if (!controls) {
			return;
		}
		if (controls.button && controls.button.isConnected) {
			controls.button.disabled = !!busy;
			controls.button.setAttribute('aria-busy', busy ? 'true' : 'false');
		}
	}

	this._describeError = function (error) {
		if (error instanceof Zotero.Pharos.API.SignedOutError
				|| error?.name == 'PharosSignedOutError') {
			return _str('pharos-evidence-error-signed-out');
		}
		if (error?.status == 409) {
			return _str('pharos-evidence-error-not-in-paper');
		}
		let message = String(error?.message || error || '').trim();
		if (!message) {
			return _str('pharos-evidence-error');
		}
		return message.length > MAX_STATUS_LENGTH
			? message.slice(0, MAX_STATUS_LENGTH) + '…'
			: message;
	};

	/**
	 * Save one selection.  Exposed separately from the event handler so the
	 * request contract can be tested without constructing a reader window.
	 *
	 * @param {ReaderInstance} reader
	 * @param {Object} annotation - reader selection annotation
	 * @param {Object} [controls] - DOM controls used for status updates
	 * @return {Promise<Object>} stored EvidenceOut
	 */
	this.saveSelection = async function (reader, annotation, controls = null) {
		_setBusy(controls, true);
		_setStatus(controls, _str('pharos-evidence-saving'), 'saving');
		try {
			if (!_isPDFReader(reader)) {
				throw new Error(_str('pharos-evidence-error-pdf-only'));
			}
			if (!Zotero.Pharos.API.hasCredentials()) {
				throw new Zotero.Pharos.API.SignedOutError();
			}
			let text = _quoteText(annotation);
			if (!text) {
				throw new Error(_str('pharos-evidence-error-empty'));
			}

			// Resolve the attachment on the Reader, rather than looking at the
			// selected library item. A parent item may own several PDFs and the one
			// currently visible is the only honest source for this quote.
			let paperID = await Zotero.Pharos.Chat.resolvePaperID(reader._item);
			let body = {
				// eslint-disable-next-line camelcase -- API wire field
				paper_id: paperID,
				kind: 'quote',
				text,
			};
			let rects = this._rectsForAnnotation(annotation);
			if (rects) {
				// pageIndex is zero-based reader state, not evidence. The backend
				// treats this one-based value only as an untrusted occurrence hint and
				// adopts it after finding the exact quote in that page's chunks. Sending
				// both in one request also avoids a resolve-then-create race.
				// eslint-disable-next-line camelcase -- API wire field
				body.page_hint = annotation.position.pageIndex + 1;
				body.rects = rects;
			}
			let result = await Zotero.Pharos.API.request('POST', '/api/evidence', { body });
			_setStatus(controls, _str('pharos-evidence-saved'), 'success');
			return result;
		}
		catch (error) {
			_setStatus(controls, this._describeError(error), 'error');
			throw error;
		}
		finally {
			_setBusy(controls, false);
		}
	};

	this._renderTextSelectionPopup = ({ reader, doc, params, append }) => {
		// renderTextSelectionPopup is also emitted for EPUB/snapshot readers, and
		// signed-out users should not see an action that can only fail remotely.
		if (!_isPDFReader(reader) || !Zotero.Pharos.API.hasCredentials()) {
			return;
		}
		let annotation = params && params.annotation;
		if (!_quoteText(annotation)) {
			return;
		}

		let section = doc.createElement('div');
		section.className = 'pharos-evidence-section';
		section.dataset.pharosEvidenceSection = 'true';

		let button = doc.createElement('button');
		button.className = 'toolbar-button wide-button pharos-reader-evidence-button pharos-evidence-save-button';
		button.dataset.pharosEvidenceSave = 'true';
		button.dataset.pharosReaderEvidence = 'true';
		button.type = 'button';
		button.textContent = _str('pharos-evidence-save');
		button.title = _str('pharos-evidence-save');
		button.setAttribute('aria-label', _str('pharos-evidence-save'));

		let status = doc.createElement('span');
		status.className = 'pharos-evidence-status';
		status.dataset.pharosEvidenceStatus = 'true';
		status.setAttribute('role', 'status');
		status.hidden = true;
		status.style.display = 'block';
		status.style.paddingTop = '4px';
		status.style.whiteSpace = 'normal';
		status.style.color = 'var(--fill-secondary)';

		section.append(button, status);
		append(section);

		let busy = false;
		button.addEventListener('click', () => {
			if (busy) {
				return;
			}
			busy = true;
			this.saveSelection(reader, annotation, { button, status })
				.catch(() => {})
				.finally(() => {
					busy = false;
				});
		});
	};

	Zotero.Reader.registerEventListener('renderTextSelectionPopup', this._renderTextSelectionPopup);
};
