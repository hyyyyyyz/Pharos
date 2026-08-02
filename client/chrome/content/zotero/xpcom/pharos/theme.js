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
 * The user-chosen accent colour, applied at runtime.
 *
 * The rest of the palette is compiled in: each theme is a Sass map fed to
 * derive-colors (scss/abstracts/_mixins.scss), and the accent is one fixed hex
 * in that map -- #182e4e light, #82a6dd dark. A colour the user picks cannot go
 * through Sass, so it is set as CSS custom properties on each document's root
 * element instead, where an inline declaration outranks the :root rule the
 * stylesheet installed.
 *
 * WHICH PROPERTIES, and why they have to move together:
 *
 *     --accent-blue      the accent itself. Read directly by ~15 rules
 *                        (sidenav pips, Windows form elements, the info section
 *                        of the item pane, banner text, split-button icons).
 *     --accent-blue10    the same colour at 10/30/50% in the light theme and at
 *     --accent-blue30    30/45/60% in the dark one. Translucent on purpose:
 *     --accent-blue50    they are overlays that have to tint whatever surface
 *                        they land on, so they cannot be pre-composited.
 *     --accent-text      the label drawn ON the accent. Paired with it per
 *                        theme, never fixed -- see below.
 *     --color-accent     what scss/base/_base.scss aliases to --accent-blue
 *     --color-accent-text  and --accent-text inside its three -moz-platform
 *                        blocks. Leaving these to the alias would work today;
 *                        they are set explicitly so that a platform without a
 *                        block, or an upstream merge that drops one, cannot
 *                        leave the single most prominent surface in the app --
 *                        the selected row -- on the compiled accent while
 *                        everything else moved.
 *
 * Setting fewer than all seven leaves the UI incoherent: an accent with no
 * matching --accent-text is the failure mode BRANDING.md records, where a cream
 * label on the dark theme's lifted accent measures 2.02:1.
 *
 * WHY HEX. The themes resolve their oklch values to hex because Sass has to
 * parse them as colours. A runtime override has no such constraint -- Gecko
 * parses oklch() fine -- but it has a worse one: a value the platform cannot
 * parse is dropped by setProperty without an error, and the UI silently keeps
 * the old accent. Hex is the one notation nothing can refuse, and computing it
 * here rather than deferring to CSS also means getVars() is directly testable.
 *
 * WHAT IS NOT HERE. There is no Pharos colour-scheme pref: Zotero already ships
 * Automatic/Light/Dark under General, bound to browser.theme.toolbar-theme
 * (0 dark, 1 light, 2 system), which is Gecko's own lever for the colour scheme
 * of chrome documents. A second pref would fight it. This file never reads that
 * pref either -- it asks each document `prefers-color-scheme` through
 * matchMedia, which is the exact signal the compiled @media blocks respond to,
 * so the accent cannot end up derived against a different theme than the one
 * being displayed. The same listener covers the OS flipping while on Automatic.
 *
 * Loaded after pharos/api, which creates the Zotero.Pharos namespace.
 */
Zotero.Pharos.Theme = new function () {
	/** Where the chosen accent lives, under Zotero's own pref branch. */
	const PREF_ACCENT = 'pharos.appearance.accent';

	/** 灯塔蓝, the brand accent, and what an unset or unknown pref means. */
	const DEFAULT_ACCENT = 'pharos';

	/**
	 * The ceiling on a light-theme accent's lightness.
	 *
	 * The web client has two accent slots: --c-ac for solid fills and
	 * --c-aclink for the accent used as a foreground, the second being the
	 * first darkened to `min(L, 0.48)` because "links must stay legible on
	 * cream". Zotero has one slot, and it is used both ways -- as a selected
	 * row's fill AND as the colour of banner text, sidenav icons and the item
	 * pane's info section. One slot serving both uses has to satisfy the
	 * stricter constraint, so every light accent is taken to the foreground
	 * value. Every accent then clears 4.5:1 against the paper ground and
	 * carries the cream label at better than 5.5:1.
	 *
	 * It also preserves an assumption the client already depends on:
	 * _virtualized-table.scss overlays #ffffff1a on buttons inside a selected
	 * row, which only reads as a hover on a dark fill. 灯塔金 at its own
	 * lightness would break that silently.
	 *
	 * This costs the two light accents their brightness in the light theme --
	 * 灯塔金 renders as a deep gold rather than the beam's gold. The picker
	 * shows the value that will actually be applied, so nothing is missold, and
	 * the brand gold is still present as --accent-gold, which this does not
	 * touch. The dark theme has no such ceiling: there the accent has to come
	 * UP off the navy ground, and --accent-text is dark to match.
	 */
	const FOREGROUND_MAX_LIGHTNESS = 0.48;

	/**
	 * The palette, carried over value for value from the web client
	 * (frontend/src/design/tokens.ts). The hues are its HUE map; the lightness
	 * and chroma are its per-accent ternaries written out per accent, which is
	 * the same table with the branching removed.
	 *
	 * The two brand accents lead because they are the identity; the rest are
	 * preference. Both are measured off the brand assets rather than chosen by
	 * eye: the P mark's navy #0C2040 is oklch(0.247 0.066 259) and its beam's
	 * gold #F8C040 is oklch(0.837 0.151 84). 灯塔蓝 sits a little above the
	 * true navy so its hover state has somewhere darker to go.
	 *
	 * In the dark theme the accent is lifted to 0.72 (0.82 for the gold, which
	 * needs no lift) so it is visible on the deep navy ground -- the brand navy
	 * at its own near-black value would disappear into it.
	 *
	 * Sanity check on the whole chain: 灯塔蓝 resolves to #182e4e in the light
	 * theme and #82a6dd in the dark one, which are byte for byte the accents
	 * compiled into scss/themes/_light.scss and _dark.scss. The default accent
	 * is therefore a visual no-op, and pharosThemeTest.js pins that.
	 */
	const ACCENTS = [
		{ key: 'pharos', hue: 259, light: { l: 0.30, c: 0.066 }, dark: { l: 0.72, c: 0.09 } },
		{ key: 'beacon', hue: 84, light: { l: 0.837, c: 0.151 }, dark: { l: 0.82, c: 0.15 } },
		{ key: 'mint', hue: 172, light: { l: 0.565, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'sky', hue: 236, light: { l: 0.565, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'pine', hue: 150, light: { l: 0.565, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'indigo', hue: 268, light: { l: 0.565, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'lilac', hue: 305, light: { l: 0.565, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'coral', hue: 25, light: { l: 0.63, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'amber', hue: 78, light: { l: 0.72, c: 0.14 }, dark: { l: 0.72, c: 0.135 } },
		{ key: 'stone', hue: 220, light: { l: 0.565, c: 0.085 }, dark: { l: 0.72, c: 0.135 } },
	];

	/**
	 * The alphas the compiled themes give --accent-blue10/30/50, kept as the
	 * exact bytes rather than as percentages so the default accent reproduces
	 * those strings character for character.
	 *
	 * The dark theme's are much heavier than its names suggest (30/45/60 rather
	 * than 10/30/50) because a light accent at a tenth of its strength over a
	 * near-black ground is not a tint, it is nothing. Deriving these from the
	 * names would quietly erase every soft accent surface in the dark theme.
	 *
	 * These deliberately stay alpha ramps of the accent rather than becoming
	 * the web client's --c-acs/--c-acsb, which are mixed out of the warm ground
	 * `in oklab` so a pale state reads as the same paper pressed deeper. That
	 * reasoning does not transfer: those two are opaque fills for a known
	 * surface, while these three land on the item pane, a note row, the search
	 * box and a bubble input, all of which sit on different grounds. An opaque
	 * sand would paint over three of them.
	 */
	const RAMP_LIGHT = [0x1a, 0x4d, 0x80];
	const RAMP_DARK = [0x4d, 0x73, 0x99];

	/**
	 * The label drawn on the accent. Cream on the light theme, near-black navy
	 * on the dark one -- the pairing is with the THEME, not with the accent,
	 * because the light theme's accents are all dark after the ceiling above
	 * and the dark theme's are all light by construction. Upstream never needed
	 * this: macOS and Linux took SelectedItem and SelectedItemText from the OS
	 * as a matched pair.
	 */
	const ACCENT_TEXT_LIGHT = '#fffcf7';
	const ACCENT_TEXT_DARK = '#08111d';

	let _inited = false;

	/**
	 * Windows already carrying a prefers-color-scheme listener, so that a
	 * window styled twice -- it is enumerated at startup and again on every
	 * accent change -- does not accumulate one listener per pass. Weak so a
	 * closed window is collectable; it also holds the MediaQueryList, which
	 * otherwise has nothing keeping it alive and stops firing when collected.
	 */
	let _watched = new WeakMap();

	/**
	 * Fires for every chrome document created in the process, which is what
	 * covers the windows and browsers that do not exist yet at startup: a
	 * preferences window, a standalone note, and the four Pharos views, which
	 * are <browser type="chrome"> elements in the main window whose src is only
	 * set the first time each is opened.
	 *
	 * The document has no root element yet at this point -- it has not been
	 * parsed -- so the work waits for DOMContentLoaded.
	 */
	let _documentObserver = {
		observe: function (subject) {
			try {
				let win = subject;
				if (!win || !win.addEventListener) {
					return;
				}
				win.addEventListener('DOMContentLoaded', function () {
					try {
						Zotero.Pharos.Theme.applyToDocument(win.document);
						_watchColorScheme(win);
					}
					catch (e) {
						Zotero.logError(e);
					}
				}, { once: true });
			}
			catch (e) {
				Zotero.logError(e);
			}
		}
	};

	/**
	 * oklch to an sRGB hex string, by way of oklab and linear sRGB.
	 *
	 * Out-of-gamut channels are clipped rather than gamut-mapped. That is the
	 * cruder of the two answers, but it is the one that reproduces the compiled
	 * themes exactly, and it only comes into play for the two light accents
	 * after the lightness ceiling has already moved them.
	 *
	 * @param {Number} l - lightness, 0-1
	 * @param {Number} c - chroma
	 * @param {Number} h - hue, degrees
	 * @return {String} lowercase '#rrggbb'
	 */
	function _oklchToHex(l, c, h) {
		let rad = h * Math.PI / 180;
		let a = c * Math.cos(rad);
		let b = c * Math.sin(rad);

		let lCube = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
		let mCube = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
		let sCube = (l - 0.0894841775 * a - 1.2914855480 * b) ** 3;

		let channels = [
			4.0767416621 * lCube - 3.3077115913 * mCube + 0.2309699292 * sCube,
			-1.2684380046 * lCube + 2.6097574011 * mCube - 0.3413193965 * sCube,
			-0.0041960863 * lCube - 0.7034186147 * mCube + 1.7076147010 * sCube,
		];

		return '#' + channels.map((v) => {
			let encoded = v <= 0.0031308
				? 12.92 * v
				: 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
			return _byte(Math.round(encoded * 255));
		}).join('');
	}

	/** A clamped 0-255 value as two lowercase hex digits. */
	function _byte(value) {
		return Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0');
	}

	/**
	 * Whether a document is currently showing the dark theme.
	 *
	 * Asked of the document rather than derived from browser.theme.toolbar-theme
	 * so that this and the compiled @media blocks can never disagree: they are
	 * answering the same question through the same mechanism. A document with
	 * no window -- one built by a test -- is treated as light.
	 */
	function _prefersDark(doc) {
		let win = doc.defaultView;
		if (!win || !win.matchMedia) {
			return false;
		}
		return win.matchMedia('(prefers-color-scheme: dark)').matches;
	}

	/**
	 * Re-derive this window's accent whenever its colour scheme changes, which
	 * happens both when the user picks Light or Dark in the preferences and
	 * when the OS flips while the setting is on Automatic.
	 */
	function _watchColorScheme(win) {
		if (!win || !win.matchMedia || _watched.has(win)) {
			return;
		}
		let mql = win.matchMedia('(prefers-color-scheme: dark)');
		mql.addEventListener('change', function () {
			try {
				Zotero.Pharos.Theme.applyToDocument(win.document);
			}
			catch (e) {
				Zotero.logError(e);
			}
		});
		_watched.set(win, mql);
	}

	/**
	 * Every chrome document a window is showing: its own, plus any it hosts in
	 * a browser or iframe.
	 *
	 * Custom properties inherit through a document, not across documents, so a
	 * hosted document needs its own copy -- the four Pharos views each load a
	 * stylesheet that pulls in the full theme, and would otherwise keep the
	 * compiled accent while the window around them changed.
	 *
	 * Restricted to chrome URIs: a content document here is a web page, a PDF
	 * in the reader or the note editor, none of which this should be reaching
	 * into. Access to a remote or cross-origin one throws, hence the catch.
	 */
	function _chromeDocuments(win) {
		let docs = [];
		if (!win || !win.document) {
			return docs;
		}
		docs.push(win.document);
		let hosts;
		try {
			hosts = win.document.querySelectorAll('browser, iframe');
		}
		catch {
			return docs;
		}
		for (let host of hosts) {
			try {
				let doc = host.contentDocument;
				if (doc && doc.documentURI && doc.documentURI.startsWith('chrome://')) {
					docs.push(doc);
				}
			}
			catch {
				// A browser we are not allowed to look into is not ours to style
			}
		}
		return docs;
	}


	/** The accent keys, in the order the picker shows them. */
	this.getAccentKeys = function () {
		return ACCENTS.map(accent => accent.key);
	};


	/**
	 * The accent in effect. An unset or unrecognised pref falls back rather
	 * than propagating: an undefined lightness would produce a string that is
	 * not a colour, and setProperty drops those without complaining, leaving
	 * the UI on whatever accent it happened to have.
	 */
	this.getAccent = function () {
		let key = Zotero.Prefs.get(PREF_ACCENT);
		return ACCENTS.some(accent => accent.key === key) ? key : DEFAULT_ACCENT;
	};


	/**
	 * Choose an accent. Writing the pref is the whole operation -- the observer
	 * registered in init() is what pushes it to every window, so a caller that
	 * sets the pref directly and a caller that comes through here behave the
	 * same.
	 */
	this.setAccent = function (key) {
		if (!ACCENTS.some(accent => accent.key === key)) {
			throw new Error(`Unknown Pharos accent '${key}'`);
		}
		Zotero.Prefs.set(PREF_ACCENT, key);
	};


	/**
	 * The custom properties for an accent under a theme.
	 *
	 * @param {String} key - an accent key
	 * @param {Boolean} dark - true for the dark theme
	 * @return {Object} property name to value
	 */
	this.getVars = function (key, dark) {
		let accent = ACCENTS.find(a => a.key === key)
			|| ACCENTS.find(a => a.key === DEFAULT_ACCENT);
		let lightness = dark ? accent.dark.l : Math.min(accent.light.l, FOREGROUND_MAX_LIGHTNESS);
		let chroma = dark ? accent.dark.c : accent.light.c;
		let ramp = dark ? RAMP_DARK : RAMP_LIGHT;
		let text = dark ? ACCENT_TEXT_DARK : ACCENT_TEXT_LIGHT;
		let hex = _oklchToHex(lightness, chroma, accent.hue);

		return {
			'--accent-blue': hex,
			'--accent-blue10': hex + _byte(ramp[0]),
			'--accent-blue30': hex + _byte(ramp[1]),
			'--accent-blue50': hex + _byte(ramp[2]),
			'--accent-text': text,
			'--color-accent': hex,
			'--color-accent-text': text,
		};
	};


	/**
	 * The colour to draw an accent's swatch with. It is the value that will
	 * actually be applied, not the palette's nominal one, so the picker cannot
	 * promise a brightness the lightness ceiling then takes away.
	 */
	this.accentSwatch = function (key, dark) {
		return this.getVars(key, dark)['--accent-blue'];
	};


	/**
	 * @param {Document} doc
	 * @param {Boolean} [dark] - which theme to derive against; asked of the
	 *     document when omitted, which is what callers other than tests want
	 */
	this.applyToDocument = function (doc, dark) {
		if (!doc || !doc.documentElement) {
			return;
		}
		if (dark === undefined) {
			dark = _prefersDark(doc);
		}
		let vars = this.getVars(this.getAccent(), dark);
		let style = doc.documentElement.style;
		for (let name in vars) {
			style.setProperty(name, vars[name]);
		}
	};


	/** Style a window and everything chrome it is hosting. */
	this.applyToWindow = function (win) {
		for (let doc of _chromeDocuments(win)) {
			this.applyToDocument(doc);
			_watchColorScheme(doc.defaultView);
		}
	};


	/**
	 * Push the current accent to every open window.
	 *
	 * getEnumerator(null) rather than Zotero.getMainWindows(): the preferences
	 * window is not a navigator:browser, and it is the one window guaranteed to
	 * be open at the moment the accent changes.
	 */
	this.apply = function () {
		for (let win of Services.wm.getEnumerator(null)) {
			try {
				this.applyToWindow(win);
			}
			catch (e) {
				// One unstyleable window must not stop the others
				Zotero.logError(e);
			}
		}
	};


	/**
	 * Idempotent, because the preferences pane calls it too: the pane is
	 * useless if startup missed this, and calling it there costs nothing if it
	 * did not.
	 *
	 * Nothing is unregistered. Both hooks live as long as the application does,
	 * and the only thing they retain is this object.
	 */
	this.init = function () {
		if (_inited) {
			return;
		}
		_inited = true;

		Zotero.Prefs.registerObserver(PREF_ACCENT, () => this.apply());
		Services.obs.addObserver(_documentObserver, 'chrome-document-global-created');

		this.apply();
	};
};
