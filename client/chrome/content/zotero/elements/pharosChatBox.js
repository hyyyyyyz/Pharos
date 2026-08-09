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
	// ItemPaneSectionElementBase is a GLOBAL here, not an ES module.
	// customElements.js loads elements/itemPaneSection.js through
	// loadSubScript before registering any section (line 31), which is how
	// every upstream box -- abstractBox, attachmentsBox -- reaches it too.
	//
	// It was an importESModule of itemPaneSectionElementBase.mjs while this
	// client was built on a later Zotero branch, where the class had been moved
	// into a module. On the release it is built on now that file does not exist,
	// and the failed import took the whole section down: no header, no body, and
	// `hidden` stuck false so it showed on notes and on items with no PDF.

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
	 * Stop waiting for shared work without cancelling the work itself.
	 *
	 * PDF upload and profile construction are shared by the reader and send(),
	 * and Zotero.HTTP cannot take an AbortSignal. The stop button still has to
	 * release the composer immediately, so the send run abandons its wait while
	 * the reader-owned preparation is allowed to finish and fill the cache.
	 */
	function _abortable(promise, signal) {
		if (!signal) {
			return promise;
		}
		if (signal.aborted) {
			let error = new Error('aborted');
			error.name = 'AbortError';
			return Promise.reject(error);
		}
		return new Promise((resolve, reject) => {
			let settled = false;
			let finish = (callback, value) => {
				if (settled) {
					return;
				}
				settled = true;
				signal.removeEventListener('abort', onAbort);
				callback(value);
			};
			let onAbort = () => {
				let error = new Error('aborted');
				error.name = 'AbortError';
				finish(reject, error);
			};
			signal.addEventListener('abort', onAbort, { once: true });
			promise.then(
				value => finish(resolve, value),
				error => finish(reject, error)
			);
		});
	}

	/**
	 * Ask questions about the open paper, in the item pane beside it.
	 *
	 * Merely selecting rows in the library keeps this section inert. Resolving a
	 * paper means uploading it and having the backend extract and understand its
	 * text, so doing that while arrowing through the library would upload papers
	 * the user never opened. The PDF reader explicitly calls prepare(), because
	 * opening a paper is the point at which Pharos promises to understand it
	 * before the first question.
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

		/** The exact PDF this pane is about; readers set it explicitly. */
		_attachment = null;

		/** 'unknown' | 'understanding' | 'indexed' | 'ready' | 'error' */
		_phase = 'unknown';

		/** Characters the backend extracted, or 0 when that is not known. */
		_charCount = 0;

		/** The account's model provider, or null while it has not been asked for. */
		_provider = null;

		/** Whether the stored turns have already been painted for this item. */
		_historyLoaded = false;

		/** Invalidates a history response whenever its destination changes. */
		_historyEpoch = 0;

		/** The history request currently allowed to paint, if any. */
		_historyLoad = null;

		_busy = false;

		/** The reader-triggered preparation for the current item, if any. */
		_preparePromise = null;

		/** Generation owned by _preparePromise. */
		_prepareGeneration = -1;

		/** Account epoch owned by _preparePromise. */
		_prepareAccountEpoch = -1;

		/** Attachment id owned by _preparePromise. */
		_prepareAttachmentID = null;

		/** Whether the header's overflow actions are showing. */
		_menuOpen = false;

		/** Whether the delete confirmation is on screen. */
		_confirming = false;

		/** The send run that alone owns _busy and the stop button. */
		_sendRun = null;

		/** Observer registered after init() so account replacement resets this box. */
		_accountObserver = null;

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
			this._attachment = null;
			this._phase = 'unknown';
			this._charCount = 0;
			this._invalidateHistory(false);
			// The old preparation cannot be cancelled (ordinary API requests do not
			// take an AbortSignal), but it must not be joined by the new paper.
			this._preparePromise = null;
			this._prepareGeneration = -1;
			this._prepareAccountEpoch = -1;
			this._prepareAttachmentID = null;
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
			// ItemPaneSectionElementBase owns the public `open` state and the
			// force-render-on-expand behaviour. Without this registration the chat
			// body can exist in the DOM, but reader integrations cannot actually
			// reveal it (`pane.open = true` is otherwise a no-op).
			this.initCollapsibleSection();
			this._sectionEl = this._section;
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

			this._accountObserver = {
				observe: () => this._accountChanged(),
			};
			Services.obs.addObserver(
				this._accountObserver,
				Zotero.Pharos.API.ACCOUNT_CHANGED_TOPIC
			);

			this._render();
		}

		destroy() {
			if (this._accountObserver) {
				Services.obs.removeObserver(
					this._accountObserver,
					Zotero.Pharos.API.ACCOUNT_CHANGED_TOPIC
				);
				this._accountObserver = null;
			}
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
			let run = this._sendRun;
			if (run) {
				run.controller.abort();
				if (this._sendRun === run) {
					this._sendRun = null;
				}
			}
			this._busy = false;
		}

		/**
		 * Make every earlier history response stale and set the canonical state.
		 *
		 * Ordinary API requests cannot be aborted, so invalidation happens at the
		 * painting boundary. The old promise may still settle, but it no longer
		 * owns `_historyLoad` and therefore cannot touch this conversation's DOM or
		 * loaded state.
		 *
		 * @param {Boolean} loaded - whether the messages already on screen are the
		 *     complete canonical history for the current conversation
		 */
		_invalidateHistory(loaded = false) {
			this._historyEpoch++;
			this._historyLoad = null;
			this._historyLoaded = loaded;
		}

		/** Invalidate every account-owned id and async painter in this mounted box. */
		_accountChanged() {
			this._cancel();
			this._generation++;
			this._provider = null;
			this._conversationID = null;
			this._conversations = [];
			this._paperID = null;
			this._phase = 'unknown';
			this._charCount = 0;
			this._invalidateHistory(false);
			this._preparePromise = null;
			this._prepareGeneration = -1;
			this._prepareAccountEpoch = -1;
			this._prepareAttachmentID = null;
			this._reset();
			if (this.item && !this.hidden) {
				this._restore(this.item, this._generation);
			}
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

			let attachment = this._attachment || Zotero.Pharos.Chat.getAttachment(item);
			this._attachment = attachment;
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
				this._invalidateHistory(false);
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
		 * Prepare the paper opened in the PDF reader before its first question.
		 *
		 * This is intentionally a public method on the pane: ReaderChat is the
		 * only automatic caller, while send() remains the explicit fallback for a
		 * library item. Concurrent toolbar/context-pane events and a question sent
		 * during preparation all join the same promise.
		 *
		 * @param {Zotero.Item|null} [attachment] - the exact PDF in a reader tab;
		 *     omitted by the library pane, which resolves from its bibliographic item
		 * @return {Promise<Boolean>} true when the paper is ready for questions
		 */
		prepare(attachment = null) {
			// A parent item may own several PDFs. The reader knows which one is open,
			// and that identity must win over getAttachment()'s source-first default.
			if (attachment && this._attachment && attachment.id != this._attachment.id) {
				this._cancel();
				this._generation++;
				this._conversationID = null;
				this._conversations = [];
				this._paperID = null;
				this._phase = 'unknown';
				this._charCount = 0;
				this._invalidateHistory(false);
				this._preparePromise = null;
				this._prepareGeneration = -1;
				this._prepareAccountEpoch = -1;
				this._prepareAttachmentID = null;
				this._reset();
			}
			if (attachment) {
				this._attachment = attachment;
			}
			let target = this._attachment || Zotero.Pharos.Chat.getAttachment(this.item);
			let generation = this._generation;
			let accountEpoch = Zotero.Pharos.API.getTokenEpoch();
			if (!target || !Zotero.Pharos.API.hasCredentials()) {
				return Promise.resolve(false);
			}
			if (this._preparePromise
					&& this._prepareGeneration == generation
					&& this._prepareAccountEpoch == accountEpoch
					&& this._prepareAttachmentID == target.id) {
				return this._preparePromise;
			}

			this._attachment = target;
			let promise = this._preparePaper(target, generation, accountEpoch);
			this._preparePromise = promise;
			this._prepareGeneration = generation;
			this._prepareAccountEpoch = accountEpoch;
			this._prepareAttachmentID = target.id;
			let clear = () => {
				if (this._preparePromise === promise) {
					this._preparePromise = null;
					this._prepareGeneration = -1;
					this._prepareAccountEpoch = -1;
					this._prepareAttachmentID = null;
				}
			};
			promise.then(clear, clear);
			return promise;
		}

		async _preparePaper(attachment, generation, accountEpoch) {
			let isCurrent = () => (
				generation == this._generation
				&& accountEpoch == Zotero.Pharos.API.getTokenEpoch()
			);
			try {
				let provider = this._provider || await Zotero.Pharos.Chat.getProvider();
				if (!isCurrent()) {
					return false;
				}
				this._provider = provider;
				this._render();
				// Do not upload a byte when there is no model to understand it. In
				// particular, ensureContext() would otherwise poll for five minutes.
				if (!provider?.configured) {
					return false;
				}

				let paperID = this._paperID;
				if (!paperID) {
					this._phase = 'understanding';
					this._setStatus(_str('pharos-chat-status-connecting'));
					this._render();
					paperID = await Zotero.Pharos.Chat.resolvePaperID(attachment);
					if (!isCurrent()) {
						return false;
					}
					this._paperID = paperID;
				}

				if (this._phase != 'ready') {
					this._phase = 'understanding';
					this._render();
					let context = await Zotero.Pharos.Chat.ensureContext(paperID, (status) => {
						if (isCurrent()) {
							this._setStatus(status);
						}
					});
					if (!isCurrent()) {
						return false;
					}
					this._applyContext(context);
					this._render();
				}

				// Once the context is ready, the paper is answerable. Restoring the
				// conversation picker and scrollback is useful, but neither network
				// request is a prerequisite for the first question.
				this._restorePreparedConversation(paperID, generation, accountEpoch)
					.catch(e => Zotero.logError(e));
				return isCurrent() && this._phase == 'ready';
			}
			catch (e) {
				Zotero.logError(e);
				if (isCurrent()) {
					if (e instanceof Zotero.Pharos.API.SignedOutError) {
						this._provider = null;
					}
					else {
						this._phase = this._phase == 'understanding' ? 'error' : this._phase;
						this._setError(e.message || _str('pharos-chat-error-prepare'));
					}
					this._render();
				}
				throw e;
			}
			finally {
				if (isCurrent()) {
					this._setStatus('');
				}
			}
		}

		/**
		 * Restore the newest thread after reader preparation, without delaying it.
		 *
		 * send() may start while the list request is still in flight. In that case
		 * getOrCreateConversation() owns the selection and this older snapshot must
		 * not clear or replace it; send() refreshes the picker after resolving it.
		 *
		 * @param {String} paperID
		 * @param {Number} generation
		 * @param {Number} accountEpoch
		 */
		async _restorePreparedConversation(paperID, generation, accountEpoch) {
			let conversationID = this._conversationID;
			let conversations = await Zotero.Pharos.Chat.listConversations(paperID);
			if (generation != this._generation
					|| accountEpoch != Zotero.Pharos.API.getTokenEpoch()
					|| paperID != this._paperID
					|| this._busy
					|| conversationID != this._conversationID) {
				return;
			}
			this._conversations = conversations;
			if (!this._conversationID
					|| !conversations.some(c => c.id == this._conversationID)) {
				this._conversationID = conversations.length ? conversations[0].id : null;
				this._invalidateHistory(false);
			}
			this._render();
			if (this._conversationID) {
				// Deliberately detached: scrollback failure costs scrollback only.
				this._renderHistory(this._conversationID, generation)
					.catch(e => Zotero.logError(e));
			}
		}

		/**
		 * Report what the backend has already extracted for this paper.
		 *
		 * A read, not a prepare. The distinction preserves library navigation:
		 * getContext() answers with what exists, while ensureContext() is reached
		 * only from an explicitly opened PDF reader or from send().
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
		 * @param {Object} [options]
		 * @param {Boolean} [options.replace] - replace local optimistic turns with
		 *     the server's canonical history after a successful send
		 */
		_renderHistory(conversationID, generation, { replace = false } = {}) {
			if (generation != this._generation || conversationID != this._conversationID) {
				return Promise.resolve(false);
			}
			if (this._historyLoaded && !replace) {
				return Promise.resolve(true);
			}
			let current = this._historyLoad;
			if (current
					&& current.conversationID == conversationID
					&& current.generation == generation
					&& current.epoch == this._historyEpoch) {
				return current.promise;
			}

			let load = {
				conversationID,
				generation,
				epoch: this._historyEpoch,
				promise: null,
			};
			let isCurrent = () => (
				this._historyLoad === load
				&& generation == this._generation
				&& conversationID == this._conversationID
				&& load.epoch == this._historyEpoch
			);
			this._historyLoad = load;
			load.promise = (async () => {
				let messages;
				try {
					messages = await Zotero.Pharos.Chat.getMessages(conversationID);
				}
				catch (e) {
					if (!isCurrent()) {
						return false;
					}
					this._historyLoad = null;
					this._historyLoaded = false;
					throw e;
				}
				if (!isCurrent()) {
					return false;
				}

				let fragment = this.ownerDocument.createDocumentFragment();
				for (let message of messages) {
					fragment.append(this._buildMessage(message.role, message.content));
				}
				if (replace) {
					this._messages.replaceChildren(fragment);
				}
				else if (messages.length) {
					this._messages.insertBefore(fragment, this._messages.firstChild);
				}
				this._messages.scrollTop = this._messages.scrollHeight;
				this._historyLoad = null;
				this._historyLoaded = true;
				// Also for an empty thread: a conversation with no turns yet is
				// still a conversation, and the controls that act on it -- the
				// picker's selection, delete -- have to come alive for it.
				this._render();
				return true;
			})();
			return load.promise;
		}

		/** Switch the box to another of this paper's threads. */
		async _selectConversation(conversationID) {
			if (this._busy || !conversationID || conversationID == this._conversationID) {
				return;
			}
			let generation = this._generation;
			this._conversationID = conversationID;
			this._invalidateHistory(false);
			let historyEpoch = this._historyEpoch;
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
				if (generation == this._generation
						&& conversationID == this._conversationID
						&& historyEpoch == this._historyEpoch) {
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
				this._invalidateHistory(true);
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
			// Falling back to the newest of what is left, which is the same rule
			// getOrCreateConversation() would apply on the next question.
			this._conversationID = this._conversations.length ? this._conversations[0].id : null;
			this._invalidateHistory(false);
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
			let run = this._sendRun;
			if (!this._busy || !run) {
				return;
			}
			run.stopped = true;
			run.controller.abort();
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
			// Capture identity before the provider lookup. It is an ordinary HTTP
			// request and cannot be cancelled; a click followed by an immediate tab
			// switch must not send this question against the paper switched to.
			let generation = this._generation;

			if (!Zotero.Pharos.API.hasCredentials()) {
				this._provider = null;
				this._render();
				return;
			}
			// Wait for the selection-time lookup when it is still in flight. This
			// keeps a question in the composer until Pharos knows it can answer,
			// rather than echoing it and only then discovering that no model exists.
			if (!this._provider) {
				try {
					this._provider = await Zotero.Pharos.Chat.getProvider();
				}
				catch (e) {
					Zotero.logError(e);
					if (generation == this._generation) {
						this._setError(e.message || _str('pharos-chat-error-failed'));
					}
					return;
				}
				if (generation != this._generation) {
					return;
				}
				this._render();
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
				if (generation != this._generation) {
					return;
				}
				this._render();
				if (this._gate()) {
					return;
				}
			}
			// Another click may have completed the same provider wait first.
			if (this._busy || generation != this._generation) {
				return;
			}

			// A history GET that began before this turn may settle after the backend
			// has stored it and return the new question and answer as well. Letting
			// that old painter prepend its response would duplicate both optimistic
			// bubbles. Invalidate it now; if history was not already complete, a
			// canonical replacement is fetched after the stream succeeds.
			let historyWasLoaded = this._historyLoaded;
			this._invalidateHistory(historyWasLoaded);
			this._busy = true;
			this._menuOpen = false;
			this._confirming = false;
			this._setError('');
			if (fromComposer) {
				this._input.value = '';
			}
			let questionBubble = this._addMessage('user', question);

			let run = {
				controller: new AbortController(),
				stopped: false,
			};
			this._sendRun = run;
			let signal = run.controller.signal;
			let bubble = null;
			let messageStarted = false;
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
				// Reader auto-preparation and an immediate first question share this
				// exact task. Library-item chat also comes through here, but only after
				// the user explicitly sends, so row navigation remains upload-free.
				await _abortable(this.prepare(), signal);
				if (generation != this._generation) {
					return;
				}
				if (signal.aborted) {
					if (run.stopped) {
						questionBubble.remove();
						ending = _str('pharos-chat-stopped');
					}
					return;
				}
				paperID = this._paperID;
				conversationID = this._conversationID;
				if (!paperID || this._phase != 'ready') {
					throw new Error(_str('pharos-chat-error-prepare'));
				}
				if (!conversationID) {
					let conversation = await _abortable(
						Zotero.Pharos.Chat.getOrCreateConversation(paperID), signal
					);
					if (generation != this._generation) {
						return;
					}
					conversationID = conversation.id;
					this._conversationID = conversationID;
					this._invalidateHistory(false);
				}
				if (generation != this._generation) {
					return;
				}
				if (signal.aborted) {
					if (run.stopped) {
						questionBubble.remove();
						ending = _str('pharos-chat-stopped');
					}
					return;
				}
				// The picker only has something to show once the paper is
				// resolved, which has just happened for a first question.
				this._refreshConversations(generation);

				this._setStatus(_str('pharos-chat-status-thinking'));
				bubble = this._addMessage('assistant', '');
				messageStarted = true;
				await Zotero.Pharos.Chat.sendMessage(conversationID, question, {
					signal,
					onDelta: (delta) => {
						if (this._sendRun !== run || generation != this._generation) {
							return;
						}
						this._setStatus('');
						bubble.textContent += delta;
						this._messages.scrollTop = this._messages.scrollHeight;
					},
				});
				if (!bubble.textContent) {
					bubble.remove();
					this._setError(_str('pharos-chat-error-empty'));
				}
				if (!historyWasLoaded
						&& this._sendRun === run
						&& generation == this._generation
						&& conversationID == this._conversationID) {
					// Do not await: the composer is released as soon as streaming is
					// done. Replacing with the server's history removes any optimistic
					// duplicates and recovers the older turns the invalidated GET held.
					this._renderHistory(conversationID, generation, { replace: true })
						.catch(e => Zotero.logError(e));
				}
			}
			catch (e) {
				if (e.name == 'AbortError') {
					// An abort raised by an item switch or teardown belongs to a
					// box the user is no longer looking at; only the stop button
					// owes anyone an explanation.
					if (run.stopped && generation == this._generation) {
						if (bubble) {
							bubble.remove();
						}
						if (!messageStarted) {
							questionBubble.remove();
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
				// Item/account switches may already have installed a new run. Only the
				// run that still owns the box may release its controller and _busy.
				if (this._sendRun === run) {
					this._sendRun = null;
					this._busy = false;
					if (generation == this._generation) {
						this._setStatus(ending);
					}
					this._render();
				}
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
