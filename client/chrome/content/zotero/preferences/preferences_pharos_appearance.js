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
 * The 外观 subpane: colour scheme and accent.
 *
 * The colour scheme needs no code -- its radiogroup carries a `preference`
 * attribute and preferences.js does the rest, in both directions.
 *
 * The accent needs three things, and all three exist because the swatch has to
 * show the truth rather than a fixed palette:
 *
 * 1. Each swatch is painted with the value that would actually be applied,
 *    which depends on the theme in effect (the light theme takes every accent
 *    down to a value that stays legible as a foreground). Painting the nominal
 *    palette instead would offer a bright gold that arrives deep.
 * 2. They are repainted when the colour scheme changes, including while this
 *    pane is open -- switching Light to Dark two controls above is the most
 *    likely moment for it to happen.
 * 3. The selection follows the pref rather than the click, so a change made in
 *    another window is reflected here.
 */
Zotero_Preferences.PharosAppearance = {
	_buttons: [],
	_mediaQuery: null,
	_onColorScheme: null,
	_prefSymbol: null,

	init: function () {
		// Idempotent. Called here as well as at startup because this pane is
		// the one place a user can change the accent, and it would be a strange
		// failure for the picker to work while nothing applied it.
		Zotero.Pharos.Theme.init();

		this._buttons = Array.from(
			document.querySelectorAll('#pharos-appearance-accents [data-accent]')
		);

		// The markup and the palette are two lists of the same thing, and a
		// drifted one shows a wrong swatch instead of throwing. Say so once,
		// loudly, rather than leaving it to be noticed by eye.
		let declared = this._buttons.map(button => button.dataset.accent).join(',');
		let known = Zotero.Pharos.Theme.getAccentKeys().join(',');
		if (declared !== known) {
			Zotero.warn('Pharos appearance pane lists accents [' + declared + '] '
				+ 'but Zotero.Pharos.Theme has [' + known + ']');
		}

		// Wired here rather than with an inline onclick because each handler
		// needs its button's key. An `oncommand` would go nowhere either way:
		// preferences.js turns those into listeners only for elements that fire
		// a command event, which an html:button does not.
		for (let button of this._buttons) {
			button.addEventListener('click', () => {
				Zotero.Pharos.Theme.setAccent(button.dataset.accent);
			});
		}

		this._mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
		this._onColorScheme = () => this.render();
		this._mediaQuery.addEventListener('change', this._onColorScheme);

		this._prefSymbol = Zotero.Prefs.registerObserver(
			'pharos.appearance.accent', () => this.render()
		);

		// Registered rather than declared as an onunload attribute -- see the
		// comment at the top of the pane's markup for why that attribute would
		// not become a listener. This has to run: the pane's sandbox is nuked
		// immediately after the event, and the observer above closes over it.
		document.getElementById('zotero-subpane-pharos-appearance')
			.addEventListener('unload', () => this.uninit(), { once: true });

		this.render();
	},

	uninit: function () {
		if (this._prefSymbol) {
			Zotero.Prefs.unregisterObserver(this._prefSymbol);
			this._prefSymbol = null;
		}
		if (this._mediaQuery) {
			this._mediaQuery.removeEventListener('change', this._onColorScheme);
			this._mediaQuery = null;
		}
	},

	render: function () {
		let dark = this._mediaQuery ? this._mediaQuery.matches : false;
		let current = Zotero.Pharos.Theme.getAccent();

		for (let button of this._buttons) {
			let key = button.dataset.accent;
			let selected = key === current;
			let colour = Zotero.Pharos.Theme.accentSwatch(key, dark);

			button.classList.toggle('selected', selected);
			button.setAttribute('aria-pressed', selected ? 'true' : 'false');
			// The one value on this pane that cannot come from the stylesheet:
			// ten swatches, each depending on the accent and on the theme, so
			// only this file knows what they resolve to. Set on the button
			// rather than on the dot so the button's own selected border can
			// inherit it -- custom properties travel down, not up.
			button.style.setProperty('--pharos-accent-swatch', colour);
		}
	},
};
