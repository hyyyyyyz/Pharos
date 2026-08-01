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
				<html:nav class="pharos-rail-nav" role="tablist"/>
				<html:div class="pharos-rail-spacer"/>
				<html:button class="pharos-rail-toggle" tabindex="-1"/>
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
		];

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

		get collapsed() {
			return this.getAttribute('collapsed') == 'true';
		}

		set collapsed(val) {
			this.setAttribute('collapsed', val ? 'true' : 'false');
			Zotero.Prefs.set('pharos.rail.collapsed', !!val);
			this._renderToggle();
		}

		init() {
			this._nav = this.querySelector('.pharos-rail-nav');
			this._toggle = this.querySelector('.pharos-rail-toggle');
			this._deck = document.getElementById('pharos-deck');

			this._toggle.addEventListener('click', () => {
				this.collapsed = !this.collapsed;
			});

			this.setAttribute('collapsed',
				Zotero.Prefs.get('pharos.rail.collapsed') ? 'true' : 'false');

			this._render();
			this._renderToggle();
		}

		_render() {
			this._nav.replaceChildren();
			for (let mod of PharosRail.MODULES) {
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
			let index = PharosRail.MODULES.findIndex(m => m.key == this.module);
			let next;
			if (event.key in KEYS) {
				next = (index + KEYS[event.key] + PharosRail.MODULES.length)
					% PharosRail.MODULES.length;
			}
			else if (event.key == 'Home') {
				next = 0;
			}
			else if (event.key == 'End') {
				next = PharosRail.MODULES.length - 1;
			}
			else {
				return;
			}
			event.preventDefault();
			this.module = PharosRail.MODULES[next].key;
			this._nav.querySelector('.pharos-rail-item.is-active')?.focus();
		}

		_renderToggle() {
			document.l10n.setAttributes(
				this._toggle,
				this.collapsed ? 'pharos-rail-expand' : 'pharos-rail-collapse'
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
	}

	customElements.define("pharos-rail", PharosRail);
}
