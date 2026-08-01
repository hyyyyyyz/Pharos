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
	const { ItemPaneSectionElementBase } = ChromeUtils.importESModule(
		"chrome://zotero/content/elements/itemPaneSectionElementBase.mjs",
		{ global: "current" }
	);

	/**
	 * Ask questions about the open paper, in the item pane beside it.
	 *
	 * The section is deliberately inert until the user sends something. Resolving
	 * the paper means uploading it and having the backend extract its text, which
	 * costs bandwidth and a model's context window; doing that merely because a
	 * pane scrolled into view would charge the user for a question they never
	 * asked.
	 */
	class PharosChatBox extends ItemPaneSectionElementBase {
		content = MozXULElement.parseXULToFragment(`
			<collapsible-section data-l10n-id="section-pharos-chat" data-pane="pharos-chat">
				<html:div class="body">
					<html:div class="pharos-chat-messages" tabindex="0"/>
					<html:div class="pharos-chat-status" role="status"/>
					<html:div class="pharos-chat-composer">
						<html:textarea class="pharos-chat-input" rows="2"
							data-l10n-id="pharos-chat-placeholder" data-l10n-attrs="placeholder"/>
						<html:button class="pharos-chat-send" data-l10n-id="pharos-chat-send"/>
					</html:div>
				</html:div>
			</collapsible-section>
		`);

		_conversationID = null;

		_busy = false;

		/** Aborts the in-flight reply when the user switches items mid-answer. */
		_abort = null;

		get item() {
			return this._item;
		}

		set item(item) {
			super.item = item;
			// Switching items while a reply is streaming would otherwise append
			// the rest of it under the new paper.
			this._cancel();
			this._conversationID = null;
			this.hidden = !Zotero.Pharos.Chat.canChat(item);
			if (!this.hidden) {
				this._reset();
			}
		}

		init() {
			this._messages = this.querySelector('.pharos-chat-messages');
			this._status = this.querySelector('.pharos-chat-status');
			this._input = this.querySelector('.pharos-chat-input');
			this._send = this.querySelector('.pharos-chat-send');

			this._send.addEventListener('command', () => this.send());
			this._send.addEventListener('click', () => this.send());
			this._input.addEventListener('keydown', (event) => {
				// Enter sends; Shift-Enter is a newline. A chat box that needed a
				// button click for every message would be exhausting, and a
				// multi-line question still has to be possible.
				if (event.key == 'Enter' && !event.shiftKey) {
					event.preventDefault();
					this.send();
				}
			});
		}

		destroy() {
			this._cancel();
		}

		_cancel() {
			if (this._abort) {
				this._abort.abort();
				this._abort = null;
			}
			this._busy = false;
		}

		_reset() {
			this._messages.replaceChildren();
			this._setStatus('');
			if (this._input) {
				this._input.value = '';
			}
		}

		_setStatus(text) {
			this._status.textContent = text || '';
			this._status.hidden = !text;
		}

		/**
		 * @param {String} role - 'user' or 'assistant'
		 * @param {String} text
		 * @return {Element} so a streaming reply can keep appending to it
		 */
		_addMessage(role, text) {
			let el = this.ownerDocument.createElement('div');
			el.className = `pharos-chat-message is-${role}`;
			el.textContent = text;
			this._messages.append(el);
			this._messages.scrollTop = this._messages.scrollHeight;
			return el;
		}

		async send() {
			if (this._busy) {
				return;
			}
			let text = this._input.value.trim();
			if (!text) {
				return;
			}

			if (!Zotero.Pharos.API.hasCredentials()) {
				this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
				return;
			}

			this._busy = true;
			this._send.disabled = true;
			this._input.value = '';
			this._addMessage('user', text);

			// Captured now: `this.item` can change under us while awaiting, and
			// the reply belongs to the paper the question was asked about.
			let item = this.item;
			this._abort = new AbortController();
			let signal = this._abort.signal;

			try {
				if (!this._conversationID) {
					this._setStatus(Zotero.getString('pharos-chat-status-connecting'));
					let attachment = Zotero.Pharos.Chat.getAttachment(item);
					let paperID = await Zotero.Pharos.Chat.resolvePaperID(attachment);
					await Zotero.Pharos.Chat.ensureContext(
						paperID, status => this._setStatus(status)
					);
					let conversation = await Zotero.Pharos.Chat.getOrCreateConversation(paperID);
					this._conversationID = conversation.id;
				}
				if (signal.aborted) {
					return;
				}

				this._setStatus(Zotero.getString('pharos-chat-status-thinking'));
				let bubble = this._addMessage('assistant', '');
				await Zotero.Pharos.Chat.sendMessage(this._conversationID, text, {
					signal,
					onDelta: (delta) => {
						this._setStatus('');
						bubble.textContent += delta;
						this._messages.scrollTop = this._messages.scrollHeight;
					},
				});
				if (!bubble.textContent) {
					bubble.remove();
					this._setStatus(Zotero.getString('pharos-chat-error-empty'));
				}
			}
			catch (e) {
				if (e.name == 'AbortError') {
					return;
				}
				Zotero.logError(e);
				this._setStatus(
					e instanceof Zotero.Pharos.API.SignedOutError
						? Zotero.getString('pharos-error-signed-out-detail')
						: (e.message || Zotero.getString('pharos-chat-error-failed'))
				);
			}
			finally {
				this._busy = false;
				this._send.disabled = false;
				this._abort = null;
			}
		}
	}

	customElements.define("pharos-chat-box", PharosChatBox);
}
