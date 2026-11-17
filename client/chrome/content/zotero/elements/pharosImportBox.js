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

"use strict";

{
	/**
	 * Paste a link, get the paper: the field at the right end of the tab bar,
	 * beside the tabs menu arrow.
	 *
	 * It is in the tab bar rather than the rail because the tab bar is what is
	 * on screen both in the library and inside a reader tab, which is where a
	 * link is most often at hand.
	 *
	 * Everything that talks to Zotero.Pharos.Import is importText(); the field's
	 * Enter, a paste into the field, and Cmd/Ctrl+V on the item list all end
	 * there. The item-list shortcut is a capturing listener on the window, so
	 * upstream's item tree and ZoteroPane stay untouched, and a clipboard that
	 * is not a recognisable link is not intercepted at all.
	 */
	class PharosImportBox extends XULElementBase {
		content = MozXULElement.parseXULToFragment(`
			<html:button class="pharos-import-toggle" type="button"
				data-l10n-id="pharos-import-button-tooltip"/>
			<html:div class="pharos-import-field">
				<html:span class="pharos-import-icon"/>
				<html:input class="pharos-import-input" type="text"
					spellcheck="false" autocomplete="off"
					data-l10n-id="pharos-import-placeholder"/>
				<html:span class="pharos-import-status" role="status"
					aria-live="polite" hidden="true"/>
				<html:span class="pharos-import-target"/>
				<html:span class="pharos-import-key" aria-hidden="true">↵</html:span>
				<html:div class="pharos-import-bar"/>
			</html:div>
		`);

		/**
		 * Below this width of the title bar (the area to the right of the module
		 * rail) the field would leave the tabs too little room, and it folds
		 * into a button. 720 keeps at least ~400px for tabs plus the native
		 * buttons with the 200px field in place; a 1100px window with the rail
		 * at its default width has ~860px, so the field is normally open.
		 */
		static NARROW_BELOW = 720;

		/** How long a success stays in the field. */
		static OK_MS = 2000;

		/** Longer when the hint is clickable (reader tab), so there is time to. */
		static OK_CLICKABLE_MS = 6000;

		init() {
			this._toggle = this.querySelector('.pharos-import-toggle');
			this._field = this.querySelector('.pharos-import-field');
			this._input = this.querySelector('.pharos-import-input');
			this._status = this.querySelector('.pharos-import-status');
			this._target = this.querySelector('.pharos-import-target');
			this._importing = false;
			this._timer = null;
			this._pending = null;

			this._input.addEventListener('keydown', (event) => {
				if (event.isComposing) {
					return;
				}
				if (event.key == 'Enter') {
					event.preventDefault();
					this.importText(this._input.value);
				}
				else if (event.key == 'Escape') {
					event.preventDefault();
					this._clearStatus();
					this._input.value = '';
					this._input.blur();
					this._collapseIfIdle();
				}
			});
			// Paste is the whole gesture when what arrives is wholly a link:
			// no Enter. Typed text, and a paste with anything left over that is
			// not a link, wait for Enter.
			this._input.addEventListener('input', (event) => {
				this._clearStatus();
				if (event.inputType == 'insertFromPaste'
						&& this.isWholeLink(this._input.value)) {
					this.importText(this._input.value);
				}
			});
			this._input.addEventListener('focus', () => {
				// Coming back after a failure: the complaint goes and the text
				// that caused it is what is there to fix.
				this._clearStatus();
				this._renderTarget();
			});
			this._input.addEventListener('focusout', () => this._collapseIfIdle());

			// Clicking the field anywhere (the hint, the icon) reaches the input.
			this._field.addEventListener('mousedown', (event) => {
				if (event.target == this._input || this._importing || this._pending) {
					return;
				}
				event.preventDefault();
				this._input.focus();
			});
			this._status.addEventListener('click', () => this._followPending());
			this._status.addEventListener('keydown', (event) => {
				if (event.key == 'Enter' || event.key == ' ') {
					event.preventDefault();
					this._followPending();
				}
			});

			this._toggle.addEventListener('click', () => {
				this.setAttribute('open', 'true');
				this._input.focus();
			});

			this._onKey = event => this._handleItemListPaste(event);
			window.addEventListener('keydown', this._onKey, true);

			// The title bar is what the tabs share with this box.
			this._bar = document.getElementById('zotero-title-bar');
			if (this._bar && window.ResizeObserver) {
				this._resizeObserver = new ResizeObserver(() => this._measure());
				this._resizeObserver.observe(this._bar);
			}
			this._measure();
			this._renderTarget();
		}

		destroy() {
			if (this._onKey) {
				window.removeEventListener('keydown', this._onKey, true);
				this._onKey = null;
			}
			if (this._resizeObserver) {
				this._resizeObserver.disconnect();
				this._resizeObserver = null;
			}
			clearTimeout(this._timer);
		}

		get importing() {
			return this._importing;
		}

		/* ------------------------------------------------------- layout */

		_measure() {
			let width = this._bar ? this._bar.getBoundingClientRect().width : Infinity;
			// A hidden or not yet laid out bar reports 0; keep whatever state
			// there was rather than flipping to narrow for a moment.
			if (!width) {
				return;
			}
			this.setNarrow(width < PharosImportBox.NARROW_BELOW);
		}

		setNarrow(narrow) {
			if (narrow) {
				this.setAttribute('narrow', 'true');
			}
			else {
				this.removeAttribute('narrow');
				this.removeAttribute('open');
			}
		}

		/** Narrow, empty, unfocused, nothing to report: back to the button. */
		_collapseIfIdle() {
			if (this.getAttribute('narrow') != 'true') {
				return;
			}
			// focusout fires before the next element has focus.
			setTimeout(() => {
				if (!this.isConnected) {
					return;
				}
				let focused = this.contains(document.activeElement);
				if (!focused && !this._input.value && !this._importing
						&& !this._status.dataset.kind) {
					this.removeAttribute('open');
				}
			}, 0);
		}

		/** "→ Collection" for where the next import goes. */
		_renderTarget() {
			let label = '';
			try {
				let target = Zotero.Pharos.Import.getTarget();
				let id = target.collections && target.collections[0];
				let collection = id && Zotero.Collections.get(id);
				let library = Zotero.Libraries.get(target.libraryID);
				label = collection ? collection.name : (library ? library.name : '');
			}
			catch (e) {
				Zotero.logError(e);
			}
			this._target.textContent = label ? '→ ' + label : '';
			this._target.title = label;
		}

		/* ------------------------------------------------------- reading */

		/**
		 * True when every piece of `text` is a link or identifier. parse() reads
		 * the whole string and silently skips what it does not know, so "this
		 * https://arxiv.org/abs/1706.03762 please" parses to one identifier; for
		 * auto-import that is not "recognised", it is a sentence.
		 */
		isWholeLink(text) {
			text = (text || '').trim();
			if (!text || text.length > Zotero.Pharos.Import.MAX_TEXT_LENGTH) {
				return false;
			}
			return text.split(/\s+/).every(
				chunk => Zotero.Pharos.Import.parse(chunk).length > 0
			);
		}

		/* --------------------------------------------------- item list paste */

		/**
		 * Cmd/Ctrl+V with the item list focused.
		 *
		 * Not handled while focus is in any text field, the search box or the
		 * note editor -- there a paste means paste -- nor with a modifier beside
		 * the accelerator, nor while a Pharos module covers the library.
		 */
		_handleItemListPaste(event) {
			let accel = Zotero.isMac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
			if (!accel || event.shiftKey || event.altKey || event.key.toLowerCase() != 'v') {
				return;
			}
			if (this._currentModule() != 'library' || this._importing) {
				return;
			}
			// The event's target is the focused element, and is what the checks
			// read -- which also keeps them testable without real focus.
			let active = event.target;
			if (!active || !active.closest || !active.closest('#zotero-items-tree')) {
				return;
			}
			if (active.closest('input, textarea, [contenteditable], editable-text, search-textbox')) {
				return;
			}
			let text = Zotero.Utilities.Internal.getClipboard('text/plain');
			if (!text || text.length > Zotero.Pharos.Import.MAX_TEXT_LENGTH
					|| !Zotero.Pharos.Import.parse(text).length) {
				return;
			}
			event.preventDefault();
			event.stopPropagation();
			this.importText(text);
		}

		_rail() {
			return document.getElementById('pharos-rail');
		}

		_currentModule() {
			let rail = this._rail();
			return rail ? rail.module : 'library';
		}

		/* ---------------------------------------------------------- import */

		/**
		 * Import whatever links are in `text`, say what happened in the field.
		 * Returns the Import result, or null if one is already running or there
		 * was nothing to do.
		 *
		 * In the library the new items are selected. In a reader tab the user
		 * stays where they are: the field says what happened, and clicking that
		 * says "show me", which switches to the library and selects.
		 */
		async importText(text) {
			text = (text || '').trim();
			if (this._importing || !text) {
				return null;
			}
			this._setBusy(true);
			let result;
			try {
				result = await Zotero.Pharos.Import.importFromText(text);
			}
			catch (e) {
				Zotero.logError(e);
				result = { imported: [], existing: [], failed: [{ identifier: null, reason: 'other' }] };
			}
			finally {
				this._setBusy(false);
			}

			let items = [...result.imported, ...result.existing];
			let clickable = false;
			if (items.length) {
				let ids = items.map(i => i.id);
				if (this._inLibraryTab()) {
					await this._reveal(ids);
				}
				else {
					this._pending = ids;
					clickable = true;
				}
				this._input.value = '';
			}
			await this._report(result, clickable);
			return result;
		}

		_inLibraryTab() {
			try {
				return typeof Zotero_Tabs == 'undefined' || Zotero_Tabs.selectedType == 'library';
			}
			catch (e) {
				return true;
			}
		}

		async _reveal(ids) {
			// From another module, back to the library so the new item is where
			// the user can see it.
			let rail = this._rail();
			if (rail && rail.module != 'library') {
				rail.module = 'library';
			}
			try {
				if (typeof Zotero_Tabs != 'undefined' && Zotero_Tabs.selectedID != 'zotero-pane') {
					Zotero_Tabs.select('zotero-pane');
				}
				await Zotero.getActiveZoteroPane().selectItems(ids);
			}
			catch (e) {
				Zotero.logError(e);
			}
		}

		async _followPending() {
			let ids = this._pending;
			if (!ids) {
				return;
			}
			this._pending = null;
			await this._reveal(ids);
			this._clearStatus();
		}

		_setBusy(on) {
			this._importing = on;
			this._input.disabled = on;
			this._field.classList.toggle('is-busy', on);
			this._toggle.classList.toggle('is-busy', on);
			if (on) {
				this._pending = null;
				this._showStatus('pharos-import-busy', null, 'busy');
			}
		}

		async _report(result, clickable) {
			let { imported, existing, failed } = result;
			if (failed.length) {
				let reason = await document.l10n.formatValue(
					'pharos-import-fail-' + failed[0].reason
				);
				if (imported.length || existing.length) {
					let done = imported.length + existing.length;
					await this._showStatus('pharos-import-partial', {
						done, total: done + failed.length, reason,
					}, 'error', clickable);
				}
				else {
					await this._showStatus('pharos-import-fail-' + failed[0].reason, null, 'error');
				}
				return;
			}
			if (imported.length) {
				await this._showStatus('pharos-import-done', { count: imported.length }, 'ok', clickable);
			}
			else {
				await this._showStatus('pharos-import-existing', { count: existing.length }, 'ok', clickable);
			}
		}

		/**
		 * @param {String} id - a pharos-import-* string
		 * @param {Object|null} args
		 * @param {String} kind - 'busy' | 'ok' | 'error'
		 * @param {Boolean} [clickable] - clicking shows the imported items
		 */
		async _showStatus(id, args, kind, clickable = false) {
			clearTimeout(this._timer);
			let text = await document.l10n.formatValue(id, args || undefined);
			let status = this._status;
			status.textContent = text;
			status.title = text;
			status.dataset.kind = kind;
			if (clickable) {
				status.setAttribute('role', 'button');
				status.tabIndex = 0;
			}
			else {
				status.setAttribute('role', 'status');
				status.removeAttribute('tabindex');
			}
			status.classList.toggle('is-clickable', clickable);
			status.hidden = false;
			this._field.dataset.state = kind;
			// A paste while folded still has to be able to say how it went.
			if (this.getAttribute('narrow') == 'true') {
				this.setAttribute('open', 'true');
			}
			// Success fades on its own; an error stays until the field is used.
			if (kind == 'ok') {
				this._timer = setTimeout(
					() => this._clearStatus(),
					clickable ? PharosImportBox.OK_CLICKABLE_MS : PharosImportBox.OK_MS
				);
			}
		}

		_clearStatus() {
			clearTimeout(this._timer);
			if (this._importing) {
				return;
			}
			this._pending = null;
			let status = this._status;
			status.hidden = true;
			status.textContent = '';
			status.title = '';
			status.classList.remove('is-clickable');
			status.setAttribute('role', 'status');
			status.removeAttribute('tabindex');
			delete status.dataset.kind;
			delete this._field.dataset.state;
			this._collapseIfIdle();
		}
	}

	customElements.define("pharos-import-box", PharosImportBox);
}
