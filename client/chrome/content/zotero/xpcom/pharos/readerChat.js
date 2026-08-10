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
 * Put AI chat where a person reading a paper can actually reach it.
 *
 * The chat itself is an item-pane section because that lets the library and the
 * reader share one implementation. A reader tab, however, starts with its
 * entire context pane collapsed, so the section and even its sidenav icon are
 * otherwise invisible. This bridge adds a reader-native toolbar affordance and
 * opens the existing section only after the reader tab exists -- changing the
 * splitter's construction-time state hangs main-window startup.
 */
Zotero.Pharos.ReaderChat = new function () {
	const PANE_ID = 'pharos-chat';
	const AUTO_OPEN_MARKER = 'pharosChatAutoOpened';
	const AUTO_OPEN_PENDING = 'pharosChatAutoOpening';

	/**
	 * Whether async work still belongs to the reader the user is looking at.
	 *
	 * A tab id alone is not enough: the tab may have been closed while a pane was
	 * rendering, and a later reader can make the main window valid again. The
	 * manager identity check distinguishes that stale instance from the live one.
	 */
	function _isCurrentReader(reader, details = null) {
		let win = reader && reader._window;
		if (!reader?.tabID || !win || win.closed
				|| win.Zotero_Tabs?.selectedID != reader.tabID
				|| Zotero.Reader.getByTabID(reader.tabID) !== reader) {
			return false;
		}
		if (details && (!details.isConnected
				|| win.ZoteroContextPane?.context?._getItemContext(reader.tabID) !== details)) {
			return false;
		}
		return true;
	}

	function _buildIcon(doc) {
		let svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
		svg.setAttribute('width', '16');
		svg.setAttribute('height', '16');
		svg.setAttribute('viewBox', '0 0 16 16');
		svg.setAttribute('aria-hidden', 'true');

		let path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
		path.setAttribute('fill', 'currentColor');
		path.setAttribute('fill-rule', 'evenodd');
		path.setAttribute('clip-rule', 'evenodd');
		path.setAttribute(
			'd',
			'M1 2H15V12H8.7L5 15V12H1V2ZM2 3V11H6V12.9L8.35 11H14V3H2ZM4 6H12V7H4V6ZM4 8H9V9H4V8Z'
		);
		svg.append(path);
		return svg;
	}

	async function _getItemDetails(reader) {
		let win = reader && reader._window;
		if (!_isCurrentReader(reader) || !win?.ZoteroContextPane?.context) {
			return null;
		}
		let context = win.ZoteroContextPane.context;
		// The reader React tree and the main-window context pane are notified by
		// different observers. A very fast click can land between them.
		for (let i = 0; i < 50; i++) {
			let details = context._getItemContext(reader.tabID);
			if (details) {
				return details;
			}
			await Zotero.Promise.delay(10);
			if (!_isCurrentReader(reader)) {
				return null;
			}
		}
		return null;
	}

	/**
	 * Open and reveal the AI section for a reader tab.
	 *
	 * @param {ReaderInstance} reader
	 * @param {Object} [options]
	 * @param {Boolean} [options.focus] - focus the composer for an explicit click
	 * @return {Promise<Boolean>} whether the section was available
	 */
	this.open = async function (reader, { focus = false } = {}) {
		let win = reader && reader._window;
		let details = await _getItemDetails(reader);
		if (!details || !_isCurrentReader(reader, details)) {
			return false;
		}
		let pane = null;
		for (let i = 0; i < 50; i++) {
			pane = details.getEnabledPane(PANE_ID);
			if (pane?.initialized && pane._section) {
				break;
			}
			await Zotero.Promise.delay(10);
			if (!_isCurrentReader(reader, details)) {
				return false;
			}
		}
		if (!pane?.initialized || !pane._section || !pane.isConnected
				|| !_isCurrentReader(reader, details)) {
			return false;
		}
		let attachment = reader._item;
		if (!Zotero.Pharos.Chat.canChat(attachment)) {
			return false;
		}

		// While collapsed this only records the destination. Besides avoiding a
		// measurement during first render, the await creates a cancellation point:
		// switching tabs here must not open the shared pane for the new tab.
		if (win.ZoteroContextPane.collapsed) {
			await details.scrollToPane(PANE_ID, 'instant');
			if (!_isCurrentReader(reader, details)) {
				return false;
			}
		}

		// These mutate one shared main-window surface, so they happen together only
		// after the last await has proved this reader still owns that surface.
		win.ZoteroContextPane.context.mode = 'item';
		pane.open = true;
		details.setPrimaryPane(PANE_ID);
		if (win.ZoteroContextPane.collapsed) {
			win.ZoteroContextPane.collapsed = false;
		}

		// Expanding starts ItemDetails.render() asynchronously. Awaiting a real
		// post-expand scroll lets that render and the section layout settle before
		// an explicit toolbar click moves focus into the composer.
		await details.scrollToPane(PANE_ID, 'instant');
		if (!_isCurrentReader(reader, details)) {
			return false;
		}
		pane = details.getEnabledPane(PANE_ID);
		if (!pane?.initialized || !pane._section || !pane.isConnected) {
			return false;
		}
		// A saved section state can finish restoring during the expansion render.
		pane.open = true;
		if (focus) {
			let input = pane.querySelector('.pharos-chat-input');
			if (input?.isConnected && _isCurrentReader(reader, details)) {
				input.focus();
			}
		}

		// The item pane represents the parent bibliographic item, which can own
		// several PDFs. Pass the Reader's attachment explicitly so preparation is
		// about the file actually open on screen rather than an arbitrary sibling.
		// Preparation remains fire-and-report: panel opening never waits on upload.
		try {
			Promise.resolve(pane.prepare?.(attachment)).catch(Zotero.logError);
		}
		catch (e) {
			Zotero.logError(e);
		}
		return true;
	};

	/** Open once for a newly-created reader context, never on every tab switch. */
	this.autoOpen = async function (reader) {
		let details = await _getItemDetails(reader);
		if (!details || !_isCurrentReader(reader, details)
				|| details.dataset[AUTO_OPEN_MARKER]
				|| details.dataset[AUTO_OPEN_PENDING]) {
			return false;
		}
		details.dataset[AUTO_OPEN_PENDING] = 'true';
		try {
			let opened = await this.open(reader);
			if (!_isCurrentReader(reader, details)) {
				return false;
			}
			if (opened) {
				details.dataset[AUTO_OPEN_MARKER] = 'true';
			}
			return opened;
		}
		finally {
			delete details.dataset[AUTO_OPEN_PENDING];
		}
	};

	this._renderToolbar = ({ reader, doc, append }) => {
		// A standalone reader window has no main-window context pane to host the
		// shared item-details section. Do not advertise a button that cannot work.
		if (!reader.tabID || !Zotero.Pharos.Chat.canChat(reader._item)) {
			return;
		}
		let label = Zotero.getString('pane-pharos-chat');
		let button = doc.createElement('button');
		button.className = 'toolbar-button pharos-reader-chat-button';
		button.dataset.pharosReaderChat = 'true';
		button.title = label;
		button.setAttribute('aria-label', label);
		button.append(_buildIcon(doc));

		let text = doc.createElement('span');
		text.className = 'pharos-reader-chat-label';
		text.textContent = label;
		button.append(text);
		button.addEventListener('click', () => {
			this.open(reader, { focus: true }).catch(Zotero.logError);
		});
		append(button);
		// renderToolbar is delivered only after the reader UI exists, making it
		// the reliable first chance to reveal the context pane without creating a
		// reader-initialization cycle. autoOpen's per-tab marker makes repeated
		// React renders harmless and preserves a later manual collapse.
		this.autoOpen(reader).catch(Zotero.logError);
	};

	Zotero.Reader.registerEventListener('renderToolbar', this._renderToolbar);
};
