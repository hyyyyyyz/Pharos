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
	 * The module rail: the far-left column that switches the whole main area
	 * between the library and the Pharos views.
	 *
	 * This mirrors the web client's `Rail`, deliberately, down to the widths. The
	 * three Pharos views were reachable only from the Tools menu before, which
	 * put permanently useful things behind a menu nobody opens.
	 *
	 * It does NOT touch the collection tree, the item tree, or the item pane.
	 * Selecting a Pharos module switches a deck that wraps all three; selecting
	 * 文库 switches back. That is why none of Zotero's own view code needed
	 * changing: the library panel is simply not the visible one.
	 */
	class PharosRail extends XULElementBase {
		content = MozXULElement.parseXULToFragment(`
			<html:div class="pharos-rail-inner">
				<html:div class="pharos-rail-brand">
					<html:span class="pharos-rail-mark"/>
					<html:span class="pharos-rail-wordmark">Pharos</html:span>
					<!-- An ordinary tab stop, as the web client's is
					     (Rail.tsx:168-177). It carried tabindex="-1" before,
					     which left collapsing the rail as the one thing in it a
					     keyboard could not do at all: the roving tabindex on the
					     modules reaches every module, and the account button
					     keeps its own stop, but this had no route in. -->
					<html:button class="pharos-rail-toggle"/>
				</html:div>
				<html:nav class="pharos-rail-nav" role="tablist"/>
				<html:div class="pharos-rail-spacer"/>
				<html:button class="pharos-rail-acct">
					<html:span class="pharos-rail-acct-avatar"/>
					<html:span class="pharos-rail-acct-text">
						<html:span class="pharos-rail-acct-name"/>
						<html:span class="pharos-rail-acct-sub"/>
					</html:span>
				</html:button>
			</html:div>
		`);

		/**
		 * Keep in step with the deck's panel order in zoteroPane.xhtml, and with
		 * the web client's rail. `library` is index 0 because the library panel is
		 * the deck's first child, which is also what makes it the default without
		 * any code running.
		 */
		static MODULES = [
			{ key: 'library', icon: 'library', l10n: 'pharos-rail-library' },
			{ key: 'daily', icon: 'daily', l10n: 'pharos-rail-daily' },
			{ key: 'discovery', icon: 'discovery', l10n: 'pharos-rail-discovery' },
			{ key: 'projects', icon: 'projects', l10n: 'pharos-rail-projects' },
			{ key: 'admin', icon: 'admin', l10n: 'pharos-rail-admin', adminOnly: true },
		];

		/**
		 * What the rail may be dragged or keyed to, from the web client's
		 * store.ts:15-17.
		 *
		 * MUST stay in step with the widths in scss/elements/_pharosRail.scss:
		 * the stylesheet clamps what a drag can produce and these clamp what a
		 * key press can, and a mismatch shows only as a rail that stops in a
		 * different place depending on how it was resized.
		 *
		 * MIN_WIDTH is 144, not 44. 44 is the collapsed width; between the two
		 * the labels are clipped while the rail is not actually collapsed.
		 */
		static MIN_WIDTH = 144;

		static MAX_WIDTH = 280;

		get module() {
			return this._module || 'library';
		}

		set module(key) {
			if (!PharosRail.MODULES.some(m => m.key == key)) {
				return;
			}
			this._module = key;
			this._render();
			this._apply();
		}

		/**
		 * NOT the `collapsed` attribute, which XUL reserves: setting it applies
		 * `visibility: collapse` to the whole element, so the rail vanished
		 * entirely and there was nothing left to click to bring it back.
		 */
		get railCollapsed() {
			return this.getAttribute('rail-collapsed') == 'true';
		}

		/**
		 * Note that the collapsed state is stored TWICE: here, and again by the
		 * `rail-collapsed` entry in this element's zotero-persist list. The two
		 * are written at different moments -- the pref on every toggle, the
		 * attribute only when a window closes -- so they can hold different
		 * answers. They are left as they are on purpose; the width beside them
		 * is persisted once, through zotero-persist alone.
		 */
		set railCollapsed(val) {
			this.setAttribute('rail-collapsed', val ? 'true' : 'false');
			Zotero.Prefs.set('pharos.rail.collapsed', !!val);
			this._renderToggle();
		}

		/**
		 * The rail's rendered width, rounded to whole pixels.
		 *
		 * Read from layout rather than from the inline style, because a drag,
		 * the stylesheet's default and a restored value all end up in different
		 * places and only the rendered width knows about all three.
		 */
		get railWidth() {
			return Math.round(this.getBoundingClientRect().width);
		}

		set railWidth(px) {
			let width = Math.round(px);
			if (!Number.isFinite(width)) {
				return;
			}
			width = Math.min(PharosRail.MAX_WIDTH, Math.max(PharosRail.MIN_WIDTH, width));
			// Both, because two different things read them. The inline style is
			// what sizes the element -- the stylesheet's own width would win
			// over a plain attribute -- and the `width` ATTRIBUTE is the only
			// thing ZoteroPane.serializePersist() copies into pane.persist.
			this.style.width = `${width}px`;
			this.setAttribute('width', width);
			this._updateSplitter();
		}

		/**
		 * Back to the stylesheet's width, as double-clicking the web client's
		 * handle does (Rail.tsx:267-272).
		 *
		 * Clears both rather than writing the default back: with neither set,
		 * the one width in the stylesheet is the only one there is, so the two
		 * cannot drift apart. It also takes the entry out of pane.persist, since
		 * serializePersist() records only attributes that are present.
		 */
		resetRailWidth() {
			this.style.removeProperty('width');
			this.removeAttribute('width');
			this._updateSplitter();
		}

		init() {
			this._nav = this.querySelector('.pharos-rail-nav');
			this._toggle = this.querySelector('.pharos-rail-toggle');
			this._deck = document.getElementById('pharos-deck');
			this._splitter = document.getElementById('pharos-rail-splitter');

			this._toggle.addEventListener('click', () => {
				this.railCollapsed = !this.railCollapsed;
			});

			this.setAttribute('rail-collapsed',
				Zotero.Prefs.get('pharos.rail.collapsed') ? 'true' : 'false');

			this._initSplitter();
			this._render();
			this._renderToggle();
			this._renderFooter();

			// Re-check against the server and redraw. isAdmin() reads a cached pref
			// so the first paint needs no round trip, but an operator promoted
			// since last launch should not have to restart to see the console.
			Zotero.Pharos.Admin.refresh()
				.then(() => this._render())
				.catch(e => Zotero.logError(e));
		}

		_render() {
			this._nav.replaceChildren();
			for (let mod of PharosRail.MODULES) {
				// Skipped at render time, NOT filtered out of MODULES: _apply()
				// maps a module's index in that array onto the deck's child index,
				// so removing an entry would shift every panel after it.
				if (mod.adminOnly && !Zotero.Pharos.Admin.isAdmin()) {
					continue;
				}
				let button = document.createElement('button');
				button.className = 'pharos-rail-item'
					+ (mod.key == this.module ? ' is-active' : '');
				button.setAttribute('role', 'tab');
				button.setAttribute('aria-selected', mod.key == this.module);
				// Roving tabindex: the rail is ONE tab stop and arrow keys move
				// within it, rather than four stops that would push every element
				// of Zotero's own pane four places along its focus order.
				button.setAttribute('tabindex', mod.key == this.module ? '0' : '-1');
				button.dataset.module = mod.key;

				let icon = document.createElement('span');
				icon.className = `pharos-rail-icon icon-${mod.icon}`;
				button.append(icon);

				let label = document.createElement('span');
				label.className = 'pharos-rail-label';
				document.l10n.setAttributes(label, mod.l10n);
				button.append(label);

				// The label is hidden when collapsed, so the tooltip is the only
				// thing naming the button in that state.
				document.l10n.setAttributes(button, mod.l10n + '-tooltip');

				button.addEventListener('click', () => {
					this.module = mod.key;
				});
				button.addEventListener('keydown', event => this._handleKey(event));
				this._nav.append(button);
			}
		}

		/**
		 * Arrow keys move between modules; Home and End jump to the ends.
		 *
		 * Required by the roving tabindex above: with only one button reachable
		 * by Tab, this is the only way to reach the others from the keyboard.
		 */
		_handleKey(event) {
			const KEYS = {
				ArrowDown: 1,
				ArrowRight: 1,
				ArrowUp: -1,
				ArrowLeft: -1,
			};
			// Navigates the VISIBLE entries. Arrowing onto a hidden admin module
			// would select a panel with no button to show it was selected.
			let visible = PharosRail.MODULES.filter(
				m => !m.adminOnly || Zotero.Pharos.Admin.isAdmin()
			);
			let index = visible.findIndex(m => m.key == this.module);
			let next;
			if (event.key in KEYS) {
				next = (index + KEYS[event.key] + visible.length) % visible.length;
			}
			else if (event.key == 'Home') {
				next = 0;
			}
			else if (event.key == 'End') {
				next = visible.length - 1;
			}
			else {
				return;
			}
			event.preventDefault();
			this.module = visible[next].key;
			this._nav.querySelector('.pharos-rail-item.is-active')?.focus();
		}

		/**
		 * Wire the splitter that resizes the rail.
		 *
		 * Dragging is the XUL splitter's own; this adds the three things a bare
		 * <splitter> has no answer for and the web client's handle does -- a
		 * width that survives a restart, a keyboard, and a double-click that
		 * puts the width back.
		 *
		 * The splitter is a sibling rather than a child, so it is found by id,
		 * and nothing here assumes it is there: a window that somehow lacks it
		 * should still have a working rail, just an unresizable one.
		 */
		_initSplitter() {
			if (!this._splitter) {
				return;
			}

			// Which of the two places a drag lands in -- an inline width or the
			// `width` attribute -- has moved between Gecko versions, and
			// serializePersist() copies only the attribute. Reading the rendered
			// width back at the end of every drag makes the persisted value
			// right either way. `command` fires when the splitter settles;
			// `mouseup` covers a drag that ends without one, and reaches this
			// element because the splitter captures the pointer for the whole
			// drag.
			this._splitter.addEventListener('command', () => this._recordWidth());
			this._splitter.addEventListener('mouseup', () => this._recordWidth());
			this._splitter.addEventListener('dblclick', () => this.resetRailWidth());
			this._splitter.addEventListener('keydown', event => this._handleSplitterKey(event));
			this._updateSplitter();
		}

		/**
		 * Write the width a drag just produced where persistence can find it.
		 */
		_recordWidth() {
			// A collapsed rail is a fixed 44px, and the stylesheet hides the
			// splitter in that state -- but a drag that started before the rail
			// collapsed would otherwise record 44 and come back as 144.
			if (this.railCollapsed) {
				return;
			}
			// Through the setter, which is what clamps it and puts it in both
			// of the places the two readers look.
			let width = this.railWidth;
			this.railWidth = width;
		}

		/**
		 * Arrow keys resize the rail; Home and End take it to its limits.
		 *
		 * Steps are the web client's (Rail.tsx:139-150). Without this the
		 * splitter is mouse-only: a keyboard user can select modules and collapse
		 * the rail, but cannot change its width at all.
		 */
		_handleSplitterKey(event) {
			const STEP = 8;
			const STEP_LARGE = 24;

			let step = event.shiftKey ? STEP_LARGE : STEP;
			let width;
			if (event.key == 'ArrowLeft') {
				width = this.railWidth - step;
			}
			else if (event.key == 'ArrowRight') {
				width = this.railWidth + step;
			}
			else if (event.key == 'Home') {
				width = PharosRail.MIN_WIDTH;
			}
			else if (event.key == 'End') {
				width = PharosRail.MAX_WIDTH;
			}
			else {
				return;
			}
			event.preventDefault();
			// Home and End would otherwise also reach whatever tree is behind
			// this and move its selection to the top or the bottom.
			event.stopPropagation();
			this.railWidth = width;
		}

		/**
		 * Republish the width on the splitter.
		 *
		 * A focusable role="separator" is a window splitter, and a window
		 * splitter is expected to announce its value. It has no accessible NAME:
		 * that needs a new string and the locale files belong to another
		 * workstream at the moment -- until one lands this announces as an
		 * unnamed separator carrying a number.
		 */
		_updateSplitter() {
			if (!this._splitter) {
				return;
			}
			// Falls back to the stylesheet's width rather than reporting 0 while
			// the window is still being laid out.
			let width = Math.min(PharosRail.MAX_WIDTH,
				Math.max(PharosRail.MIN_WIDTH, this.railWidth || PharosRail.MIN_WIDTH));
			this._splitter.setAttribute('aria-valuemin', PharosRail.MIN_WIDTH);
			this._splitter.setAttribute('aria-valuemax', PharosRail.MAX_WIDTH);
			this._splitter.setAttribute('aria-valuenow', width);
		}

		_renderToggle() {
			document.l10n.setAttributes(
				this._toggle,
				this.railCollapsed ? 'pharos-rail-expand' : 'pharos-rail-collapse'
			);
		}

		/**
		 * Show the selected module.
		 */
		_apply() {
			if (!this._deck) {
				this._deck = document.getElementById('pharos-deck');
			}
			if (!this._deck) {
				return;
			}

			// `hidden` on each panel rather than a deck's selectedIndex -- see the
			// comment in zoteroPane.xhtml for why a nested deck is the wrong tool
			// here. The library panel is #zotero-trees; the rest are the browsers.
			let panels = Array.from(this._deck.children);
			let index = PharosRail.MODULES.findIndex(m => m.key == this.module);
			if (index < 0) {
				index = 0;
			}
			panels.forEach((panel, i) => {
				panel.hidden = i != index;
			});

			// A Pharos module owns the whole main area, so a reader tab has to be
			// left first -- otherwise the deck switches underneath a tab that is
			// still showing a PDF.
			if (this.module != 'library' && Zotero_Tabs.selectedID != 'zotero-pane') {
				Zotero_Tabs.select('zotero-pane');
			}

			if (this.module != 'library') {
				this._ensureLoaded(this.module);
			}

			// The item pane's sidenav and the tag selector belong to the library
			// view and read as broken when a Pharos module is showing.
			document.getElementById('zotero-tab-cover')?.classList.add('hidden');
		}

		/**
		 * Point a panel's browser at its document the first time it is shown.
		 *
		 * Lazily, because each view queries the backend as it loads: giving all
		 * three a `src` up front would fire three requests on every start, for
		 * modules the user may never open.
		 */
		_ensureLoaded(key) {
			let browser = document.getElementById(`pharos-view-${key}`);
			if (!browser || browser.getAttribute('src')) {
				return;
			}
			browser.setAttribute('src', `chrome://zotero/content/pharos${
				key.charAt(0).toUpperCase() + key.slice(1)
			}.xhtml`);
		}

		/**
		 * The account footer: who is signed in, and the way to the account pane.
		 *
		 * Sits at the bottom of the column behind a spacer, as it does in the web
		 * client. Signed out it is the way in rather than a dead label: the token
		 * is obtained in the preferences pane, and without one every Pharos module
		 * is a wall of errors, so the footer has to point at the cure.
		 *
		 * It keeps its place in the tab order. Sign-in is the one thing in this
		 * rail a new user has to reach, and a control the keyboard cannot reach
		 * is not an entry point. The collapse toggle above it is a tab stop for
		 * the same reason; only the module list is roving, so the rail is three
		 * stops rather than seven.
		 */
		/**
		 * Repaint the account footer.
		 *
		 * Public because the sign-in gate closes from outside this element and the
		 * footer would otherwise keep saying "signed out" until the next restart.
		 * Reaching into _renderFooter() from zoteroPane.js would work today and
		 * break the moment this element is refactored.
		 */
		refreshAccount() {
			this._renderFooter();
		}

		_renderFooter() {
			let button = this.querySelector('.pharos-rail-acct');

			button.addEventListener('click', () => {
				// The pane id is the one preferencePanes.js registers for Pharos.
				// openPreferences() hands an unknown id to navigateToPane, which
				// simply finds nothing -- a typo here opens the window on the
				// wrong pane rather than failing, so the test pins the pair.
				Zotero.Utilities.Internal.openPreferences('zotero-prefpane-pharos');
			});

			// Painted from the cached address first -- the same pref the
			// preferences pane paints from. Waiting for the server would show
			// every signed-in user "not signed in" for the length of a round
			// trip, on every window that opens.
			this._paintAccount(Zotero.Prefs.get('pharos.accountEmail'));

			if (!Zotero.Pharos.API.hasCredentials()) {
				return;
			}
			Zotero.Pharos.API.verify()
				.then((user) => {
					// null is not a guess: verify() returns it only after a 401
					// cleared the token, so the account really is signed out.
					Zotero.Prefs.set('pharos.accountEmail', user ? user.email : '');
					this._paintAccount(user ? user.email : null);
				})
				// An unreachable server is not evidence about anyone's account.
				// Keep the cached address rather than signing the user out of the
				// UI the first time the wifi drops.
				.catch(e => Zotero.logError(e));
		}

		/**
		 * Paint the footer for an address, or for nobody.
		 *
		 * @param {String|null} email
		 */
		_paintAccount(email) {
			let button = this.querySelector('.pharos-rail-acct');
			let name = button.querySelector('.pharos-rail-acct-name');
			let sub = button.querySelector('.pharos-rail-acct-sub');
			// Both halves matter: a cached address outlives the token it was
			// cached beside, since a 401 clears the token from under it, and an
			// address with no token is not a signed-in account.
			let signedIn = !!email && Zotero.Pharos.API.hasCredentials();

			if (signedIn) {
				// The id has to come off before the text goes in: DOM
				// localization retranslates anything still carrying one, which
				// would drop the placeholder back over the address.
				name.removeAttribute('data-l10n-id');
				name.textContent = email;
			}
			else {
				// Emptied rather than merely relabelled: DOM localization fills
				// the element on its own schedule, and an address must not sit
				// there naming an account that has just been signed out of.
				name.textContent = '';
				document.l10n.setAttributes(name, 'pharos-rail-account-none');
			}
			document.l10n.setAttributes(sub, signedIn
				? 'pharos-rail-account-settings'
				: 'pharos-rail-account-sign-in');
			// The label is hidden when the rail is collapsed, so the tooltip is
			// the only thing naming this button in that state.
			document.l10n.setAttributes(button, signedIn
				? 'pharos-rail-account-tooltip'
				: 'pharos-rail-account-sign-in-tooltip');
		}
	}

	customElements.define("pharos-rail", PharosRail);
}
