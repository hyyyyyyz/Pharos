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
	 * The four openings the empty state offers, in the web client's order.
	 *
	 * They are ids rather than sentences because the button's label and the
	 * question that gets sent are the same string, and a chip whose label had
	 * drifted from what it asked would be indistinguishable from one that
	 * worked.
	 */
	const STARTERS = [
		'pharos-chat-starter-contribution',
		'pharos-chat-starter-trick',
		'pharos-chat-starter-evidence',
		'pharos-chat-starter-limitations',
	];

	/** Where the read-only view of the account's model lives. */
	const PROVIDER_PANE = 'zotero-subpane-pharos-daily';

	/** Where signing in lives. */
	const ACCOUNT_PANE = 'zotero-prefpane-pharos';

	/**
	 * A Fluent string, with arguments when it takes them.
	 *
	 * NOT Zotero.getString(id, params) for the argument case: a params argument
	 * routes to the .properties bundle, where no pharos-* id exists, and that
	 * throws in en-US while silently returning the id in zh-CN.
	 */
	function _str(id, args) {
		if (args === undefined) {
			return Zotero.getString(id);
		}
		let value = Zotero.ftl.formatValueSync(id, args);
		// An unresolvable id comes back null rather than throwing, and an empty
		// string is a legitimate value, so this cannot be a truthiness test.
		return value === null || value === undefined ? id : value;
	}

	/**
	 * Ask questions about the open paper, in the item pane beside it.
	 *
	 * The section is deliberately inert until the user sends something. Resolving
	 * the paper means uploading it and having the backend extract its text, which
	 * costs bandwidth and a model's context window; doing that merely because a
	 * pane scrolled into view would charge the user for a question they never
	 * asked.
	 *
	 * Everything the section shows before that point is therefore a report of
	 * what is already known -- a paper resolved earlier in this session, the
	 * account's configured model -- and never a preparation performed in order
	 * to have something to report.
	 */
	class PharosChatBox extends ItemPaneSectionElementBase {
		content = MozXULElement.parseXULToFragment(`
			<collapsible-section data-l10n-id="section-pharos-chat" data-pane="pharos-chat"
					extra-buttons="pharos-chat-new,pharos-chat-more">
				<html:div class="body">
					<html:div class="pharos-chat-phase">
						<html:span class="pharos-chat-phase-label"/>
						<html:span class="pharos-chat-phase-chars"/>
					</html:div>
					<html:div class="pharos-chat-sessions" hidden="hidden">
						<html:select class="pharos-chat-session"
							data-l10n-id="pharos-chat-session-select"
							data-l10n-attrs="aria-label"/>
					</html:div>
					<html:div class="pharos-chat-menu" hidden="hidden">
						<html:button class="pharos-chat-menu-delete"
							data-l10n-id="pharos-chat-delete"/>
					</html:div>
					<html:div class="pharos-chat-notice" hidden="hidden">
						<html:strong class="pharos-chat-notice-title"/>
						<html:span class="pharos-chat-notice-detail"/>
						<html:button class="pharos-chat-notice-action"/>
					</html:div>
					<html:div class="pharos-chat-empty" hidden="hidden">
						<html:div class="pharos-chat-empty-headline"/>
						<html:div class="pharos-chat-empty-paper"/>
						<html:div class="pharos-chat-starters"/>
					</html:div>
					<html:div class="pharos-chat-messages" tabindex="0"/>
					<html:div class="pharos-chat-confirm" hidden="hidden">
						<html:span data-l10n-id="pharos-chat-delete-confirm"/>
						<html:div class="pharos-chat-confirm-actions">
							<html:button class="pharos-chat-confirm-go"
								data-l10n-id="pharos-chat-delete-go"/>
							<html:button class="pharos-chat-confirm-cancel"
								data-l10n-id="pharos-chat-delete-cancel"/>
						</html:div>
					</html:div>
					<html:div class="pharos-chat-banner" hidden="hidden" role="alert">
						<html:span class="pharos-chat-banner-text"/>
						<html:button class="pharos-chat-banner-dismiss"
							data-l10n-id="pharos-chat-dismiss"/>
					</html:div>
					<html:div class="pharos-chat-status" role="status"/>
					<html:div class="pharos-chat-composer">
						<html:textarea class="pharos-chat-input" rows="2"
							data-l10n-id="pharos-chat-placeholder" data-l10n-attrs="placeholder"/>
						<html:div class="pharos-chat-actions">
							<!-- The behaviour matched the web already; only the
							     sentence explaining it was missing, which makes
							     Shift+Enter something a user has to guess. -->
							<html:span class="pharos-chat-hint"
								data-l10n-id="pharos-chat-hint"/>
							<html:button class="pharos-chat-stop" hidden="hidden"
								data-l10n-id="pharos-chat-stop"/>
							<html:button class="pharos-chat-send" data-l10n-id="pharos-chat-send"/>
						</html:div>
					</html:div>
				</html:div>
			</collapsible-section>
		`);

		/** The conversation currently painted, or null when there is none yet. */
		_conversationID = null;

		/**
		 * Conversation summaries for this paper, newest first.
		 *
		 * Empty until the paper has an id, which is the same moment the stored
		 * thread becomes reachable: both are keyed by the paper, and asking for
		 * either costs an upload before then. So the picker and the header
		 * buttons appear exactly when there is something for them to manage.
		 */
		_conversations = [];

		/** The backend's id for this item's PDF, once something has paid for it. */
		_paperID = null;

		/** 'unknown' | 'understanding' | 'indexed' | 'ready' | 'error' */
		_phase = 'unknown';

		/** Characters the backend extracted, or 0 when that is not known. */
		_charCount = 0;

		/** The account's model provider, or null while it has not been asked for. */
		_provider = null;

		/** Whether the stored turns have already been painted for this item. */
		_historyLoaded = false;

		_busy = false;

		/** Whether the header's overflow actions are showing. */
		_menuOpen = false;

		/** Whether the delete confirmation is on screen. */
		_confirming = false;

		/**
		 * Set only by the stop button, so that the abort can be told apart from
		 * the ones an item switch and teardown raise. Those two must paint
		 * nothing; this one owes the user an explanation for the answer that
		 * just vanished.
		 */
		_stopped = false;

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
		 *
		 * Everything asynchronous added since -- the provider lookup, the
		 * context report, the conversation list, delete -- carries it for the
		 * same reason, and for the same reason each of them checks it again
		 * after every await rather than only at the end.
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
			// Asked before anything is mutated. canChat() reaches for the item's
			// child items, which throws UnloadedDataException when the pane hands
			// over an item whose data is not loaded yet. Doing it in this order
			// means such a throw leaves the section wholly on the previous item
			// rather than holding the new one with the old one's thread -- and
			// the pane's next render pass, which is what loading produces, tries
			// again.
			let canChat = Zotero.Pharos.Chat.canChat(item);
			super.item = item;
			this.hidden = !canChat;
			if (!switched) {
				return;
			}
			// Switching items while a reply is streaming would otherwise append
			// the rest of it under the new paper.
			this._cancel();
			// Invalidates any load still in flight for the old item.
			this._generation++;
			this._conversationID = null;
			this._conversations = [];
			this._paperID = null;
			this._phase = 'unknown';
			this._charCount = 0;
			this._historyLoaded = false;
			// Cleared even for an item that cannot be chatted about. Skipping it
			// while hidden left the previous paper's turns in the DOM, and an
			// item that acquires a PDF later -- which is the one case the
			// re-render above exists for -- then revealed them under the wrong
			// paper.
			this._reset();
			if (!this.hidden) {
				// Deliberately not awaited: the section renders now and fills in
				// when the backend answers.
				this._restore(item, this._generation);
			}
		}

		init() {
			this._sectionEl = this.querySelector('collapsible-section');
			this._phaseEl = this.querySelector('.pharos-chat-phase');
			this._phaseLabel = this.querySelector('.pharos-chat-phase-label');
			this._phaseChars = this.querySelector('.pharos-chat-phase-chars');
			this._sessions = this.querySelector('.pharos-chat-sessions');
			this._sessionSelect = this.querySelector('.pharos-chat-session');
			this._menu = this.querySelector('.pharos-chat-menu');
			this._menuDelete = this.querySelector('.pharos-chat-menu-delete');
			this._notice = this.querySelector('.pharos-chat-notice');
			this._noticeTitle = this.querySelector('.pharos-chat-notice-title');
			this._noticeDetail = this.querySelector('.pharos-chat-notice-detail');
			this._noticeAction = this.querySelector('.pharos-chat-notice-action');
			this._empty = this.querySelector('.pharos-chat-empty');
			this._emptyHeadline = this.querySelector('.pharos-chat-empty-headline');
			this._emptyPaper = this.querySelector('.pharos-chat-empty-paper');
			this._starters = this.querySelector('.pharos-chat-starters');
			this._messages = this.querySelector('.pharos-chat-messages');
			this._confirm = this.querySelector('.pharos-chat-confirm');
			this._banner = this.querySelector('.pharos-chat-banner');
			this._bannerText = this.querySelector('.pharos-chat-banner-text');
			this._status = this.querySelector('.pharos-chat-status');
			this._input = this.querySelector('.pharos-chat-input');
			this._send = this.querySelector('.pharos-chat-send');
			this._stop = this.querySelector('.pharos-chat-stop');
			// Built by collapsible-section's own init(), which has already run:
			// appending the template above connected it, and custom element
			// upgrades during insertion are synchronous.
			this._newButton = this.querySelector('.section-custom-button.pharos-chat-new');
			this._moreButton = this.querySelector('.section-custom-button.pharos-chat-more');

			this._buildStarters();

			this._menuDelete.addEventListener('click', () => {
				this._menuOpen = false;
				this._confirming = true;
				this._render();
			});

			this._send.addEventListener('command', () => this.send());
			this._send.addEventListener('click', () => this.send());
			this._stop.addEventListener('click', () => this.stop());
			this._input.addEventListener('keydown', (event) => {
				// Enter sends; Shift-Enter is a newline. A chat box that needed a
				// button click for every message would be exhausting, and a
				// multi-line question still has to be possible.
				if (event.key == 'Enter' && !event.shiftKey) {
					event.preventDefault();
					this.send();
				}
			});

			this._sessionSelect.addEventListener('change', () => {
				this._selectConversation(this._sessionSelect.value);
			});
			this.querySelector('.pharos-chat-banner-dismiss')
				.addEventListener('click', () => this._setError(''));
			this.querySelector('.pharos-chat-confirm-go')
				.addEventListener('click', () => this._deleteConversation());
			this.querySelector('.pharos-chat-confirm-cancel')
				.addEventListener('click', () => {
					this._confirming = false;
					this._render();
				});
			this._noticeAction.addEventListener('click', () => {
				Zotero.Utilities.Internal.openPreferences(
					this._gate() == 'signed-out' ? ACCOUNT_PANE : PROVIDER_PANE
				);
			});

			// The extra buttons report through the section, which is what
			// dispatches the event named after each button type.
			this._sectionEl.addEventListener('pharos-chat-new', () => this._newConversation());
			this._sectionEl.addEventListener('pharos-chat-more', () => {
				this._menuOpen = !this._menuOpen;
				this._confirming = false;
				this._render();
			});

			this._render();
		}

		destroy() {
			this._cancel();
			// Stops a load in flight from painting into a section that is being
			// taken down.
			this._generation++;
		}

		_buildStarters() {
			let fragment = this.ownerDocument.createDocumentFragment();
			for (let id of STARTERS) {
				let button = this.ownerDocument.createElement('button');
				button.className = 'pharos-chat-starter';
				button.setAttribute('data-l10n-id', id);
				// The label is the question. Read at click time rather than
				// captured here so that it is whatever the user can see.
				button.addEventListener('click', () => this.send(button.textContent));
				fragment.append(button);
			}
			this._starters.replaceChildren(fragment);
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
			this._setError('');
			// _provider is deliberately kept: it belongs to the account, not to
			// the item, and clearing it here would open the composer for one
			// tick on every selection before the gate came back.
			this._menuOpen = false;
			this._confirming = false;
			this._stopped = false;
			if (this._input) {
				this._input.value = '';
			}
			this._render();
		}

		_setStatus(text) {
			this._status.textContent = text || '';
			this._status.hidden = !text;
		}

		/**
		 * Show a failure until it is dismissed, or clear the one showing.
		 *
		 * Not the status line. That line is overwritten by the next thing that
		 * happens -- "思考中…" erases the reason the last answer failed -- so a
		 * failure written there is gone by the time the user has read the
		 * question they just retyped.
		 *
		 * @param {String} text - empty to dismiss
		 */
		_setError(text) {
			this._bannerText.textContent = text || '';
			this._banner.hidden = !text;
			if (text) {
				this._setStatus('');
			}
		}

		/**
		 * What, if anything, stops a question being asked at all.
		 *
		 * @return {String|null} 'signed-out', 'no-model', or null
		 */
		_gate() {
			if (!Zotero.Pharos.API.hasCredentials()) {
				return 'signed-out';
			}
			// Null means the lookup has not come back yet, which is not the same
			// as "no model": gating on it would blank the composer for the
			// length of a round trip on every selection.
			if (this._provider && !this._provider.configured) {
				return 'no-model';
			}
			return null;
		}

		/**
		 * The resting line under the section title.
		 *
		 * Reports state; never causes it. 'unknown' is the honest answer for a
		 * paper this session has not resolved, and saying so is better than the
		 * web client's "preparing", which here would describe work that is
		 * deliberately not happening.
		 *
		 * @return {String}
		 */
		_phaseText() {
			switch (this._phase) {
				case 'understanding':
					return _str('pharos-chat-phase-understanding');
				case 'ready':
					return _str('pharos-chat-phase-ready');
				case 'indexed':
					return this._gate() == 'no-model'
						? _str('pharos-chat-phase-indexed-no-model')
						: _str('pharos-chat-phase-indexed');
				case 'error':
					return _str('pharos-chat-phase-error');
				default:
					return _str('pharos-chat-phase-lazy');
			}
		}

		/**
		 * Repaint everything derived from state.
		 *
		 * One pass rather than a dozen targeted updates. Every region here
		 * depends on more than one piece of state -- the composer on three of
		 * them -- and the failure mode of updating them one at a time is a
		 * region that is stale and still looks authoritative.
		 */
		_render() {
			if (!this.initialized) {
				return;
			}
			let gate = this._gate();

			this._phaseLabel.textContent = this._phaseText();
			this._phaseChars.textContent = this._charCount
				? _str('pharos-chat-phase-chars', { count: this._charCount })
				: '';
			this._phaseChars.hidden = !this._charCount;
			this._phaseEl.classList.toggle('is-ready', this._phase == 'ready');
			this._phaseEl.classList.toggle('is-error', this._phase == 'error');

			this._renderNotice(gate);
			this._renderSessions();
			this._renderEmpty(gate);

			// The overflow actions are an inline row rather than a XUL
			// menupopup. Everything else destructive in this client confirms
			// inline too, and a popup anchored into a section whose body
			// animates its own max-height is a lot of machinery for one button
			// -- one that also cannot be driven in a test until the popup has
			// been opened by hand at least once, which is not a property a
			// delete control should have.
			this._menu.hidden = !this._menuOpen || !this._paperID;
			this._confirm.hidden = !this._confirming;
			// The backend refuses to delete a conversation that is still
			// generating (409), so the control has to be shut off rather than
			// let the user find that out from an error.
			this._menuDelete.disabled = !this._conversationID || this._busy;
			this._newButton.disabled = this._busy;

			this._input.disabled = !!gate;
			this._send.disabled = this._busy || !!gate;
			this._send.hidden = this._busy;
			this._stop.hidden = !this._busy;
		}

		_renderNotice(gate) {
			this._notice.hidden = !gate;
			if (!gate) {
				return;
			}
			let signedOut = gate == 'signed-out';
			this._noticeTitle.textContent = _str(
				signedOut ? 'pharos-chat-signed-out-title' : 'pharos-chat-no-model-title'
			);
			this._noticeDetail.textContent = _str(
				signedOut ? 'pharos-chat-signed-out-detail' : 'pharos-chat-no-model-detail'
			);
			this._noticeAction.textContent = _str(
				signedOut ? 'pharos-chat-signed-out-action' : 'pharos-chat-no-model-action'
			);
		}

		_renderSessions() {
			// Shown from the first conversation, as the web client's is. This
			// hid below two on the argument that a one-entry picker is a control
			// that cannot do anything -- true of the control, false of the label.
			// Conversations are created implicitly and outlive the window, so at
			// one entry the picker is still answering "which thread am I in?",
			// which is the question a returning reader has. It also stops the
			// row appearing out of nowhere and shifting the composer down the
			// moment a second conversation exists.
			this._sessions.hidden = !this._conversations.length;
			this._newButton.hidden = !this._paperID;
			this._moreButton.hidden = !this._paperID;
			if (this._sessions.hidden) {
				return;
			}
			let fragment = this.ownerDocument.createDocumentFragment();
			for (let conversation of this._conversations) {
				let option = this.ownerDocument.createElement('option');
				option.value = conversation.id;
				// The backend renames a conversation after its first question,
				// so an untouched one keeps the generic default and that is the
				// truthful label for it.
				option.textContent = conversation.title || _str('pharos-chat-untitled');
				fragment.append(option);
			}
			this._sessionSelect.replaceChildren(fragment);
			this._sessionSelect.value = this._conversationID || '';
			this._sessionSelect.disabled = this._busy;
		}

		_renderEmpty(gate) {
			// A restored thread stays on screen behind the notice card. It is
			// still the record of what was asked, and hiding it would make a
			// model that went missing look like a conversation that did.
			let empty = !gate && !this._messages.childElementCount && !this._busy;
			this._empty.hidden = !empty;
			if (!empty) {
				return;
			}
			this._emptyHeadline.textContent = _str(
				this._phase == 'ready'
					? 'pharos-chat-empty-ready'
					: (this._phase == 'understanding'
						? 'pharos-chat-empty-understanding'
						: 'pharos-chat-empty-idle')
			);
			let title = this._paperTitle();
			this._emptyPaper.textContent = title;
			this._emptyPaper.title = title;
			this._emptyPaper.hidden = !title;
		}

		/**
		 * The paper's title, or nothing if it cannot be had cheaply.
		 *
		 * getDisplayTitle() reads the item's data and throws
		 * UnloadedDataException when that has not been loaded, which the item
		 * pane usually but not always guarantees. Letting it out abandons the
		 * whole of _render() halfway: the regions painted after this one keep
		 * whatever they last showed, and a stale "delete" left enabled against
		 * a conversation that is no longer current is a worse outcome than a
		 * missing line of the empty state. Nothing here is worth an await --
		 * loading the item to print its name would make every repaint async.
		 *
		 * @return {String}
		 */
		_paperTitle() {
			if (!this.item) {
				return '';
			}
			try {
				return this.item.getDisplayTitle();
			}
			catch {
				return '';
			}
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
			this._render();
			return el;
		}

		/**
		 * Redraw everything about this item that is already paid for.
		 *
		 * The paper id is only known when the user has asked something about this
		 * paper in this session. Resolving it otherwise means uploading the file,
		 * and doing that because an item was selected would upload the library one
		 * arrow key at a time -- the same reason this section stays inert until a
		 * question is sent. What that leaves is a thread older than this process,
		 * and send() picks that one up at the point where the upload is being paid
		 * for anyway.
		 *
		 * The provider lookup is not subject to that rule and runs regardless: it
		 * reads the account's configuration, not the paper, and it is the one
		 * thing that has to be known before the composer is used rather than
		 * after.
		 *
		 * @param {Zotero.Item} item
		 * @param {Number} generation
		 */
		async _restore(item, generation) {
			if (!Zotero.Pharos.API.hasCredentials()) {
				return;
			}
			this._loadProvider(generation);

			let attachment = Zotero.Pharos.Chat.getAttachment(item);
			let paperID = Zotero.Pharos.Chat.getKnownPaperID(attachment);
			if (!paperID) {
				return;
			}
			this._paperID = paperID;
			try {
				// Never getOrCreate here: selecting an item is not a reason to
				// write a conversation row on the server.
				let conversations = await Zotero.Pharos.Chat.listConversations(paperID);
				if (generation != this._generation) {
					return;
				}
				this._conversations = conversations;
				// Resumed before the repaint, not after: half of what _render()
				// decides -- which option the picker shows, whether delete is
				// live at all -- is a function of which conversation is current,
				// and a repaint that ran first would settle both on "none".
				this._conversationID = conversations.length ? conversations[0].id : null;
				this._render();
				this._reportContext(paperID, generation);
				if (this._conversationID) {
					await this._renderHistory(this._conversationID, generation);
				}
			}
			catch (e) {
				// The composer is untouched on purpose. A thread that could not
				// be redrawn still takes a new question, and answering it is
				// worth more than reporting that the scrollback is missing.
				Zotero.logError(e);
			}
		}

		/**
		 * Ask which model this account has, and gate the composer on the answer.
		 *
		 * Not awaited by callers and never rethrows: a provider lookup that fails
		 * must leave the composer usable, because the send path asks again and a
		 * question that would have worked must not be blocked by a status request
		 * that did not.
		 *
		 * @param {Number} generation
		 * @param {Object} [options]
		 * @param {Boolean} [options.refresh] - ignore the session's cached answer
		 */
		async _loadProvider(generation, { refresh } = {}) {
			try {
				let provider = await Zotero.Pharos.Chat.getProvider({ refresh });
				if (generation != this._generation) {
					return;
				}
				this._provider = provider;
				this._render();
			}
			catch (e) {
				Zotero.logError(e);
			}
		}

		/**
		 * Report what the backend has already extracted for this paper.
		 *
		 * A read, not a prepare. The distinction is the whole of this section's
		 * laziness: getContext() answers with what exists, ensureContext() would
		 * start the extraction, and only send() is allowed to do the latter.
		 *
		 * @param {String} paperID
		 * @param {Number} generation
		 */
		async _reportContext(paperID, generation) {
			try {
				let context = await Zotero.Pharos.Chat.getContext(paperID);
				if (generation != this._generation || !context) {
					return;
				}
				this._applyContext(context);
				this._render();
			}
			catch (e) {
				Zotero.logError(e);
			}
		}

		_applyContext(context) {
			if (!context) {
				return;
			}
			this._charCount = context.charCount || 0;
			// The backend drops a failed profile back to "indexed" and keeps the
			// raw text, so "ready" is the only status that means the model has
			// actually read this paper.
			this._phase = context.status == 'ready' && context.hasSummary
				? 'ready'
				: (context.status || 'indexed');
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
			if (generation != this._generation) {
				return;
			}
			if (messages.length) {
				let fragment = this.ownerDocument.createDocumentFragment();
				for (let message of messages) {
					fragment.append(this._buildMessage(message.role, message.content));
				}
				this._messages.insertBefore(fragment, this._messages.firstChild);
				this._messages.scrollTop = this._messages.scrollHeight;
			}
			// Also for an empty thread: a conversation with no turns yet is
			// still a conversation, and the controls that act on it -- the
			// picker's selection, delete -- have to come alive for it.
			this._render();
		}

		/** Switch the box to another of this paper's threads. */
		async _selectConversation(conversationID) {
			if (this._busy || !conversationID || conversationID == this._conversationID) {
				return;
			}
			let generation = this._generation;
			this._conversationID = conversationID;
			this._historyLoaded = false;
			this._menuOpen = false;
			this._confirming = false;
			this._messages.replaceChildren();
			this._setError('');
			this._render();
			try {
				await this._renderHistory(conversationID, generation);
			}
			catch (e) {
				Zotero.logError(e);
				if (generation == this._generation) {
					this._setError(e.message || _str('pharos-chat-error-history'));
				}
			}
		}

		/** Start a second thread about the same paper. */
		async _newConversation() {
			if (this._busy || !this._paperID) {
				return;
			}
			let generation = this._generation;
			try {
				let conversation = await Zotero.Pharos.Chat.createConversation(this._paperID);
				if (generation != this._generation) {
					return;
				}
				this._conversations = [conversation, ...this._conversations];
				this._conversationID = conversation.id;
				// Nothing stored to fetch, and saying so is what stops send()
				// from going looking and painting the thread it just left.
				this._historyLoaded = true;
				this._messages.replaceChildren();
				this._menuOpen = false;
				this._confirming = false;
				this._setError('');
				this._render();
			}
			catch (e) {
				Zotero.logError(e);
				if (generation == this._generation) {
					this._setError(e.message || _str('pharos-chat-error-new'));
				}
			}
		}

		/** Delete the conversation on screen, after the inline confirmation. */
		async _deleteConversation() {
			let conversationID = this._conversationID;
			if (!conversationID || this._busy) {
				return;
			}
			let generation = this._generation;
			this._menuOpen = false;
			this._confirming = false;
			this._render();
			try {
				await Zotero.Pharos.Chat.deleteConversation(conversationID);
			}
			catch (e) {
				Zotero.logError(e);
				if (generation == this._generation) {
					this._setError(e.message || _str('pharos-chat-error-delete'));
				}
				return;
			}
			if (generation != this._generation) {
				return;
			}
			this._conversations = this._conversations.filter(c => c.id != conversationID);
			this._messages.replaceChildren();
			this._historyLoaded = false;
			// Falling back to the newest of what is left, which is the same rule
			// getOrCreateConversation() would apply on the next question.
			this._conversationID = this._conversations.length ? this._conversations[0].id : null;
			this._render();
			if (this._conversationID) {
				try {
					await this._renderHistory(this._conversationID, generation);
				}
				catch (e) {
					Zotero.logError(e);
				}
			}
		}

		/**
		 * Stop the answer being generated.
		 *
		 * The partial text goes with it. The backend saves no partial answer --
		 * stream_chat_events() returns on GeneratorExit without persisting one --
		 * so text left on screen would be a turn the model does not have and will
		 * not have in context for the next question. The user's own question
		 * stays, because the backend kept that.
		 */
		stop() {
			if (!this._busy) {
				return;
			}
			this._stopped = true;
			this._cancel();
		}

		/**
		 * @param {String} [text] - what to ask; defaults to the composer's contents
		 */
		async send(text) {
			if (this._busy) {
				return;
			}
			let fromComposer = text === undefined;
			let question = (fromComposer ? this._input.value : text).trim();
			if (!question) {
				return;
			}

			if (!Zotero.Pharos.API.hasCredentials()) {
				this._provider = null;
				this._render();
				return;
			}
			// Asked again rather than trusted: the cached answer can predate the
			// user configuring a model in the web app, and the cost of being
			// wrong here is a 503 delivered after the question was written.
			if (this._provider && !this._provider.configured) {
				try {
					this._provider = await Zotero.Pharos.Chat.getProvider({ refresh: true });
				}
				catch (e) {
					Zotero.logError(e);
				}
				this._render();
				if (this._gate()) {
					return;
				}
			}

			this._busy = true;
			this._stopped = false;
			this._menuOpen = false;
			this._confirming = false;
			this._setError('');
			if (fromComposer) {
				this._input.value = '';
			}
			this._addMessage('user', question);

			// Captured now: `this.item` can change under us while awaiting, and
			// the reply belongs to the paper the question was asked about.
			let item = this.item;
			let generation = this._generation;
			this._abort = new AbortController();
			let signal = this._abort.signal;
			let bubble = null;
			// What the status line should say once this run is over. Empty for
			// every ordinary ending: the progress messages written along the way
			// describe work that has finished, and leaving the last one up is
			// how "思考中…" came to sit under a completed answer.
			let ending = '';

			// Held locally and only written back while the generation still
			// matches. None of the three calls below can be cancelled -- they go
			// through Zotero.HTTP, which takes no signal -- so each of them can
			// land after the user has moved to another paper, and writing an
			// answer about paper A into the fields of paper B is worse than
			// losing the run: the id is what the *next* question would be asked
			// about.
			let paperID = this._paperID;
			let conversationID = this._conversationID;

			try {
				if (!paperID) {
					this._setStatus(_str('pharos-chat-status-connecting'));
					let attachment = Zotero.Pharos.Chat.getAttachment(item);
					paperID = await Zotero.Pharos.Chat.resolvePaperID(attachment);
					if (generation != this._generation) {
						return;
					}
					this._paperID = paperID;
				}
				if (this._phase != 'ready') {
					this._phase = 'understanding';
					this._render();
					let context = await Zotero.Pharos.Chat.ensureContext(
						paperID, status => this._setStatus(status)
					);
					if (generation != this._generation) {
						return;
					}
					this._applyContext(context);
					this._render();
				}
				if (!conversationID) {
					let conversation = await Zotero.Pharos.Chat.getOrCreateConversation(paperID);
					if (generation != this._generation) {
						return;
					}
					conversationID = conversation.id;
					this._conversationID = conversationID;
					// That conversation is usually not new -- it is whichever one
					// this paper already had, from an earlier session or another
					// client -- and the backend is about to answer with all of it
					// in context. Paint it before the answer arrives, so the reply
					// is not the first sign that the model remembers.
					try {
						await this._renderHistory(conversationID, generation);
					}
					catch (e) {
						// Scrollback is a convenience; the question is not.
						Zotero.logError(e);
					}
				}
				if (signal.aborted || generation != this._generation) {
					return;
				}
				// The picker only has something to show once the paper is
				// resolved, which has just happened for a first question.
				this._refreshConversations(generation);

				this._setStatus(_str('pharos-chat-status-thinking'));
				bubble = this._addMessage('assistant', '');
				await Zotero.Pharos.Chat.sendMessage(this._conversationID, question, {
					signal,
					onDelta: (delta) => {
						this._setStatus('');
						bubble.textContent += delta;
						this._messages.scrollTop = this._messages.scrollHeight;
					},
				});
				if (!bubble.textContent) {
					bubble.remove();
					this._setError(_str('pharos-chat-error-empty'));
				}
			}
			catch (e) {
				if (e.name == 'AbortError') {
					// An abort raised by an item switch or teardown belongs to a
					// box the user is no longer looking at; only the stop button
					// owes anyone an explanation.
					if (this._stopped && generation == this._generation) {
						if (bubble) {
							bubble.remove();
						}
						ending = _str('pharos-chat-stopped');
					}
					return;
				}
				Zotero.logError(e);
				if (generation != this._generation) {
					return;
				}
				if (bubble && !bubble.textContent) {
					bubble.remove();
				}
				if (e instanceof Zotero.Pharos.API.SignedOutError) {
					// hasCredentials() is already false by here -- request()
					// clears a dead token -- so the gate paints itself.
					this._provider = null;
				}
				else if (e.status == 503) {
					// The backend's 503 is the model being unconfigured or
					// unusable. Re-asking turns a sentence in a banner into the
					// card that says where to go -- and it has to be a real
					// re-ask, since the session's cached answer is exactly the
					// one this 503 just contradicted.
					this._provider = null;
					this._loadProvider(generation, { refresh: true });
					this._setError(e.message || _str('pharos-chat-error-failed'));
				}
				else {
					this._phase = this._phase == 'understanding' ? 'error' : this._phase;
					this._setError(e.message || _str('pharos-chat-error-failed'));
				}
			}
			finally {
				this._busy = false;
				this._stopped = false;
				this._abort = null;
				if (generation == this._generation) {
					this._setStatus(ending);
				}
				this._render();
			}
		}

		/**
		 * Re-read this paper's conversation list without disturbing the thread.
		 *
		 * Not awaited: it exists so the picker and the header buttons catch up
		 * after a first question resolved the paper, and nothing on screen is
		 * waiting on it.
		 *
		 * @param {Number} generation
		 */
		async _refreshConversations(generation) {
			if (!this._paperID) {
				return;
			}
			try {
				let conversations = await Zotero.Pharos.Chat.listConversations(this._paperID);
				if (generation != this._generation) {
					return;
				}
				this._conversations = conversations;
				this._render();
			}
			catch (e) {
				Zotero.logError(e);
			}
		}
	}

	customElements.define("pharos-chat-box", PharosChatBox);
}
