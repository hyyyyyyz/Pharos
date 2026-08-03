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
	 * Where a paper's translation stands, in the item pane beside it.
	 *
	 * Before this section existed, the only place a translation reported
	 * anything was the ProgressQueues dialog, and that dialog is modal-ish,
	 * transient and per-run: close it and every trace of the job is gone. A user
	 * looking at an item could not tell whether it had been translated, was
	 * being translated, or had failed an hour ago. The web client has answered
	 * that from the start with a status pill in the list and a progress block in
	 * the detail panel (frontend/src/components/{ItemList,DetailPanel}.tsx); this
	 * is the same information, at the place the desktop client puts per-item
	 * facts.
	 *
	 * Everything shown here is derived synchronously and locally --
	 * Zotero.Pharos.Translate.getState() does no I/O, deliberately, and its
	 * comment explains what that costs. Rendering must stay that cheap: the item
	 * pane rebuilds this on every selection change, which for a held-down arrow
	 * key is a render per row.
	 */
	class PharosTranslateBox extends ItemPaneSectionElementBase {
		content = MozXULElement.parseXULToFragment(`
			<collapsible-section data-l10n-id="section-pharos-translate" data-pane="pharos-translate">
				<html:div class="body">
					<html:div class="pharos-translate-headline">
						<html:span class="pharos-translate-pill"/>
					</html:div>
					<html:div class="pharos-translate-track" hidden="hidden">
						<html:span class="pharos-translate-bar"/>
					</html:div>
					<html:ol class="pharos-translate-steps" hidden="hidden"/>
					<html:div class="pharos-translate-status" role="status"/>
					<html:div class="pharos-translate-error"/>
					<html:div class="pharos-translate-note"/>
					<html:div class="pharos-translate-actions"/>
				</html:div>
			</collapsible-section>
		`);

		/**
		 * Fluent, with the id as its own placeholder.
		 *
		 * Zotero.getString() cannot stand in here: for an id that is not in the
		 * bundle yet it falls through to the .properties bundle and *throws* in
		 * en-US (see xpcom/intl.js), which would take the whole item pane down
		 * rather than leave one label blank. This resolves what exists and shows
		 * the id for what does not, so a missing string is a visible gap and
		 * never a broken pane.
		 *
		 * @param {String} id
		 * @param {Object} [args]
		 * @return {String}
		 */
		_string(id, args) {
			let value = null;
			try {
				value = Zotero.ftl.formatValueSync(id, args);
			}
			catch {
				Zotero.debug(`Pharos: no string for ${id}`);
			}
			return value || id;
		}

		get item() {
			return this._item;
		}

		set item(item) {
			super.item = item;
			// Re-decided on every pass, not only on a switch, because the things
			// it depends on are exactly the things an edit can change: an item
			// acquires its first PDF, or a translation is imported onto it.
			this.hidden = !this._applies(item);
			if (!this.hidden) {
				this.render();
			}
		}

		/**
		 * Whether this section has anything true to say about an item.
		 *
		 * Two ways to have nothing: translation does not apply to the item at
		 * all (no stored PDF anywhere on it), or the user is not signed in, in
		 * which case every control here would be a button that cannot work and
		 * every state a claim that cannot be checked. Both hide the section
		 * outright rather than showing it greyed out -- an inert panel on every
		 * note and web page in the library is worse than no panel.
		 *
		 * hasCredentials() is a synchronous read of the already-loaded login
		 * store, so it is safe on the selection path; getToken(), which
		 * decrypts, is not, and is not called here.
		 *
		 * @param {Zotero.Item} item
		 * @return {Boolean}
		 */
		_applies(item) {
			return !!item
				&& Zotero.Pharos.API.hasCredentials()
				// The account switch. Without it this section still appeared for
				// an account with translation turned off -- a header over a body
				// that render() then declined to fill, because getState() returns
				// null in that case. An empty section is not "the apparatus is
				// gone"; it is the apparatus, broken.
				&& Zotero.Pharos.Translate.isEnabled()
				&& !!Zotero.Pharos.Translate.getTranslatableAttachment(item);
		}

		init() {
			this.initCollapsibleSection();

			this._pill = this.querySelector('.pharos-translate-pill');
			this._track = this.querySelector('.pharos-translate-track');
			this._bar = this.querySelector('.pharos-translate-bar');
			this._steps = this.querySelector('.pharos-translate-steps');
			this._status = this.querySelector('.pharos-translate-status');
			this._error = this.querySelector('.pharos-translate-error');
			this._note = this.querySelector('.pharos-translate-note');
			this._actions = this.querySelector('.pharos-translate-actions');

			// Progress comes out of a poll loop, not out of the data layer, so
			// the item notifier below never hears about it.
			Zotero.Pharos.Translate.addStateListener(this._handleJobState);
			// ...and the data layer is what reports the end of the job: the
			// translation is imported and related, which modifies the source
			// attachment. Without this the section would go from "95%" straight
			// to nothing until the user clicked elsewhere and back.
			this._notifierID = Zotero.Notifier.registerObserver(
				this, ['item'], 'pharosTranslateBox'
			);

			this.render();
		}

		destroy() {
			Zotero.Pharos.Translate.removeStateListener(this._handleJobState);
			Zotero.Notifier.unregisterObserver(this._notifierID);
		}

		notify(action, type, ids) {
			if (action != 'add' && action != 'modify' && action != 'delete') {
				return;
			}
			if (!this._item || this.hidden) {
				return;
			}
			// The source attachment is what changes when a translation lands
			// (the relation is written onto it), and the selected item is what
			// changes when a PDF is added to a regular item. Deleting a
			// translation is a 'delete' on an id this item no longer relates to,
			// so that one is caught by the selected item's own modification.
			let watched = [this._item.id];
			let state = Zotero.Pharos.Translate.getState(this._item);
			if (state) {
				watched.push(state.attachment.id);
				for (let translation of state.translations) {
					watched.push(translation.id);
				}
			}
			if (ids.some(id => watched.includes(id))) {
				this.hidden = !this._applies(this._item);
				if (!this.hidden) {
					this.render();
				}
			}
		}

		_handleJobState = (itemID) => {
			if (this.hidden || !this._item) {
				return;
			}
			let state = Zotero.Pharos.Translate.getState(this._item);
			if (state && state.attachment.id == itemID) {
				this.render();
			}
		};

		/**
		 * Deliberately not gated on _isAlreadyRendered(): its dependency list is
		 * (tabID, item id), and the whole point of this section is that what it
		 * shows changes while both stay the same.
		 */
		render() {
			if (!this._item || !this._pill) {
				return;
			}
			let state = Zotero.Pharos.Translate.getState(this._item);
			if (!state) {
				return;
			}

			let T = Zotero.Pharos.Translate;
			let running = state.state == T.STATE_RUNNING;

			this._pill.className = `pharos-translate-pill is-${state.state}`;
			this._pill.textContent = this._stateLabel(state);

			this._track.hidden = !running;
			this._bar.style.width = `${running ? state.progress : 0}%`;

			this._renderSteps(state, running);

			// The engine's own words for the phases that are not the engine:
			// uploading, queueing and downloading are outside the three steps
			// and would otherwise leave the section looking stalled at 0%.
			let showStatus = running && state.phase && state.phase != 'running';
			this._status.textContent = showStatus ? state.message : '';
			this._status.hidden = !showStatus;

			this._error.textContent = state.error || '';
			this._error.hidden = !state.error;

			// Said once, next to the answer it qualifies: this section reports a
			// library, not an account. Only where the absence of evidence is the
			// whole finding -- saying it under "已译" would be noise.
			let unknown = state.state == T.STATE_UNKNOWN && !state.isTranslation;
			this._note.textContent = unknown
				? this._string('pharos-translate-state-unknown-detail')
				: '';
			this._note.hidden = !unknown;

			this._renderActions(state);
		}

		_stateLabel(state) {
			let T = Zotero.Pharos.Translate;
			switch (state.state) {
				case T.STATE_RUNNING:
					// Percentages only exist once the engine has the file. Until
					// then the pill says what is true without a number, rather
					// than a confident "0%".
					return state.phase == 'running'
						? this._string('pharos-translate-state-translating-percent',
							{ percent: state.progress })
						: this._string('pharos-translate-state-translating');
				case T.STATE_FAILED:
					return this._string('pharos-translate-state-failed');
				case T.STATE_TRANSLATED:
					return this._string('pharos-translate-state-translated');
				default:
					// A translation is not "no translation in this library" --
					// it is the translation. Saying so is the one thing that
					// stays true for it whatever the engine ever did.
					return this._string(state.isTranslation
						? 'pharos-translate-state-is-translation'
						: 'pharos-translate-state-unknown');
			}
		}

		/**
		 * The three-step stepper, done / active / todo.
		 *
		 * The raw engine stage goes on as the tooltip rather than into the
		 * label: it is the more precise of the two and the less legible, and
		 * discarding it to gain legibility would trade one problem for another.
		 */
		_renderSteps(state, running) {
			this._steps.hidden = !running || state.phase != 'running';
			if (this._steps.hidden) {
				this._steps.replaceChildren();
				return;
			}

			let ids = [
				'pharos-translate-stage-parse',
				'pharos-translate-stage-translate',
				'pharos-translate-stage-typeset',
			];
			let tooltip = state.stage
				? this._string('pharos-translate-stage-tooltip', { stage: state.stage })
				: '';

			let fragment = this.ownerDocument.createDocumentFragment();
			for (let i = 0; i < ids.length; i++) {
				let li = this.ownerDocument.createElement('li');
				let status = 'todo';
				if (i < state.stageIndex) {
					status = 'done';
				}
				else if (i == state.stageIndex) {
					status = 'active';
				}
				li.className = `pharos-translate-step is-${status}`;
				li.textContent = this._string(ids[i]);
				if (tooltip) {
					li.title = tooltip;
				}
				fragment.append(li);
			}
			this._steps.replaceChildren(fragment);
		}

		/**
		 * Only the actions that make sense in the state shown.
		 *
		 * Rebuilt rather than shown/hidden because the set genuinely changes
		 * shape -- there is one "open" button per translation the paper has, and
		 * that is 0, 1 or 2.
		 */
		_renderActions(state) {
			let T = Zotero.Pharos.Translate;
			let buttons = [];

			if (state.state == T.STATE_RUNNING) {
				// The only place a run can be cancelled is the queue dialog, and
				// it is closed by now or this section would not be needed. Not
				// offering the way back to it would make "翻译中" a state with
				// no exit.
				buttons.push(this._button('pharos-translate-action-queue', null, () => {
					Zotero.ProgressQueues.get('pharos-translate').getDialog().open();
				}));
			}
			else if (state.state == T.STATE_FAILED) {
				buttons.push(this._button('pharos-translate-action-retry', null, () => {
					this._start(() => T.retry(state.attachment));
				}));
			}
			else if (!state.isTranslation) {
				// Same two modes as the context menu, and the same words for
				// them: this is a second route to that command, not a second
				// command. Only the modes the paper does not already have --
				// running mono again on a paper that has a mono translation
				// spends minutes of engine time to arrive back where it started,
				// and would leave two identically-named attachments behind.
				let have = state.translations.map(t => T.getTranslationMode(t));
				if (!have.includes(T.MODE_MONO)) {
					buttons.push(this._button('pharos-translate-menu-mono', null, () => {
						this._start(() => T.translateItems([state.attachment], T.MODE_MONO));
					}));
				}
				if (!have.includes(T.MODE_DUAL)) {
					buttons.push(this._button('pharos-translate-menu-dual', null, () => {
						this._start(() => T.translateItems([state.attachment], T.MODE_DUAL));
					}));
				}
			}

			for (let translation of state.translations) {
				// Named when there is more than one, because "Open Translation"
				// twice over is a coin toss between 译文 and 对照.
				let label = state.translations.length > 1
					? this._string('pharos-translate-action-open-named',
						{ name: this._suffixOf(translation) })
					: this._string('pharos-translate-action-open');
				buttons.push(this._button(
					null,
					label,
					() => this._open(translation),
					translation.getField('title') || translation.attachmentFilename
				));
			}

			if (state.original) {
				buttons.push(this._button(
					'pharos-translate-action-open-original',
					null,
					() => this._open(state.original),
					state.original.getField('title') || state.original.attachmentFilename
				));
			}

			this._actions.replaceChildren(...buttons);
			this._actions.hidden = !buttons.length;
		}

		/** The localized word inside the parentheses, for a button label. */
		_suffixOf(translation) {
			let name = translation.getField('title') || translation.attachmentFilename || '';
			let match = /\(([^()]+)\)(?:\.pdf)?$/i.exec(name.trim());
			return match ? match[1] : name;
		}

		_button(id, text, onClick, tooltip) {
			let button = this.ownerDocument.createElement('button');
			button.className = 'pharos-translate-action';
			button.textContent = id ? this._string(id) : text;
			if (tooltip) {
				button.title = tooltip;
			}
			// Both, as the chat box does: these are HTML buttons inside a XUL
			// document, where 'command' is what XUL keyboard activation sends
			// and 'click' is what the mouse sends.
			button.addEventListener('command', onClick);
			button.addEventListener('click', onClick);
			return button;
		}

		/**
		 * Kick a run off without waiting for it.
		 *
		 * translateItems() resolves when the whole queue is done, which is
		 * minutes away; awaiting it here would leave the click handler pending
		 * for the length of the translation. The state listener is what draws
		 * the result, and a rejection is logged rather than thrown into a
		 * handler nobody is watching -- _processQueue() has already recorded it
		 * as a failed job, which is what this section will show.
		 */
		_start(run) {
			this.render();
			Promise.resolve()
				.then(run)
				.catch(e => Zotero.logError(e));
		}

		_open(attachment) {
			ZoteroPane.viewAttachment(attachment.id);
		}
	}

	customElements.define("pharos-translate-box", PharosTranslateBox);
}
