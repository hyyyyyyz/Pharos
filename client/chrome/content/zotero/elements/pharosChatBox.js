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

		/** Whether the stored turns have already been painted for this item. */
		_historyLoaded = false;

		_busy = false;

		/** Aborts the in-flight reply when the user switches items mid-answer. */
		_abort = null;

		/**
		 * Bumped on every item change. Async work captures it and paints only
		 * while it still matches.
		 *
		 * The AbortController above cannot stand in for this. It is created
		 * inside send(), so at item-change time there is usually nothing to
		 * abort, and more fundamentally Zotero.Pharos.API.request() goes
		 * through Zotero.HTTP and accepts no signal at all -- only stream()
		 * does. A history load therefore cannot be cancelled, only ignored, so
		 * the guard has to sit on the painting side rather than the request
		 * side. Without it, switching items twice quickly paints the first
		 * paper's thread into the second paper's box.
		 */
		_generation = 0;

		get item() {
			return this._item;
		}

		set item(item) {
			// The item pane re-runs this on every render pass, not only on a
			// switch: any edit to the item brings it back round with the same
			// item. Whether the section belongs on screen is re-decided every
			// time, because that is exactly what such an edit can change -- an
			// item with no PDF acquires one, and the section has to appear.
			// Everything below is about the *thread*, which a re-render must
			// not disturb: wiping it because a tag was added would be the same
			// amnesia this section already had, and reloading it would refetch
			// from the server on every keystroke in the info pane.
			let switched = !this._item || !item || this._item.id != item.id;
			super.item = item;
			this.hidden = !Zotero.Pharos.Chat.canChat(item);
			if (!switched) {
				return;
			}
			// Switching items while a reply is streaming would otherwise append
			// the rest of it under the new paper.
			this._cancel();
			// Invalidates any history load still in flight for the old item.
			this._generation++;
			this._conversationID = null;
			this._historyLoaded = false;
			if (!this.hidden) {
				this._reset();
				// Deliberately not awaited: the section renders now and fills in
				// when the backend answers.
				this._restore(item, this._generation);
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
			// Stops a history load in flight from painting into a section that
			// is being taken down.
			this._generation++;
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
		 * @return {Element}
		 */
		_buildMessage(role, text) {
			let el = this.ownerDocument.createElement('div');
			el.className = `pharos-chat-message is-${role}`;
			el.textContent = text;
			return el;
		}

		/**
		 * @param {String} role - 'user' or 'assistant'
		 * @param {String} text
		 * @return {Element} so a streaming reply can keep appending to it
		 */
		_addMessage(role, text) {
			let el = this._buildMessage(role, text);
			this._messages.append(el);
			this._messages.scrollTop = this._messages.scrollHeight;
			return el;
		}

		/**
		 * Redraw the thread the backend has been appending to.
		 *
		 * Only runs when the paper id is already known, which means the user has
		 * asked something about this paper in this session. Resolving it
		 * otherwise means uploading the file, and doing that because an item was
		 * selected would upload the library one arrow key at a time -- the same
		 * reason this section stays inert until a question is sent. What that
		 * leaves is a thread older than this process, and send() picks that one
		 * up at the point where the upload is being paid for anyway.
		 *
		 * @param {Zotero.Item} item
		 * @param {Number} generation
		 */
		async _restore(item, generation) {
			let attachment = Zotero.Pharos.Chat.getAttachment(item);
			let paperID = Zotero.Pharos.Chat.getKnownPaperID(attachment);
			if (!paperID || !Zotero.Pharos.API.hasCredentials()) {
				return;
			}
			try {
				// Never getOrCreate here: selecting an item is not a reason to
				// write a conversation row on the server.
				let conversation = await Zotero.Pharos.Chat.getLatestConversation(paperID);
				if (!conversation || generation != this._generation) {
					return;
				}
				this._conversationID = conversation.id;
				await this._renderHistory(conversation.id, generation);
			}
			catch (e) {
				// The composer is untouched on purpose. A thread that could not
				// be redrawn still takes a new question, and answering it is
				// worth more than reporting that the scrollback is missing.
				Zotero.logError(e);
			}
		}

		/**
		 * Paint a conversation's stored turns above whatever is already shown.
		 *
		 * Always at the top, because everything stored is older than anything
		 * this session has painted -- which is what lets send() call this after
		 * it has already echoed the user's question.
		 *
		 * @param {String} conversationID
		 * @param {Number} generation
		 */
		async _renderHistory(conversationID, generation) {
			if (this._historyLoaded || generation != this._generation) {
				return;
			}
			// Claimed before the first await: _restore() and send() can both
			// reach here for the same item, and a second pass would paint the
			// whole thread twice. Released again on failure so that the later
			// caller is a real retry rather than a no-op.
			this._historyLoaded = true;
			let messages;
			try {
				messages = await Zotero.Pharos.Chat.getMessages(conversationID);
			}
			catch (e) {
				this._historyLoaded = false;
				throw e;
			}
			if (generation != this._generation || !messages.length) {
				return;
			}
			let fragment = this.ownerDocument.createDocumentFragment();
			for (let message of messages) {
				fragment.append(this._buildMessage(message.role, message.content));
			}
			this._messages.insertBefore(fragment, this._messages.firstChild);
			this._messages.scrollTop = this._messages.scrollHeight;
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
			let generation = this._generation;
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
					// That conversation is usually not new -- it is whichever one
					// this paper already had, from an earlier session or another
					// client -- and the backend is about to answer with all of it
					// in context. Paint it before the answer arrives, so the reply
					// is not the first sign that the model remembers.
					try {
						await this._renderHistory(conversation.id, generation);
					}
					catch (e) {
						// Scrollback is a convenience; the question is not.
						Zotero.logError(e);
					}
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
