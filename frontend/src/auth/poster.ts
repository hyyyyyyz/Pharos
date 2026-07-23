/**
 * The sign-in poster — the large left panel of the gate.
 *
 * TO INSTALL THE ARTWORK: drop the file at `src/assets/login-poster.png`
 * (or .jpg / .webp / .svg), then replace the two lines below with:
 *
 *     import poster from "../assets/login-poster.png";
 *     export const POSTER_SRC: string | null = poster;
 *
 * It is wired this way rather than importing a file that does not exist yet
 * because Vite resolves imports at build time — a missing asset would fail the
 * build outright. With `null` the gate renders a branded fallback panel
 * instead, so the page is always shippable.
 *
 * SIZING. The panel is `object-fit: cover` and takes the viewport minus the
 * sign-in column, so its aspect ratio is roughly 1.1 (a 1512×982 laptop) to
 * 1.5 (a 2560×1440 desktop) — mildly LANDSCAPE, never portrait. 5:4 sits in
 * the middle of that range and crops least at both ends: ~6% off the sides at
 * 1.1, ~8% off the top and bottom at 1.5. Cover always crops from the centre,
 * so keep anything that must survive within the middle ~80%.
 */
import poster from "../assets/login-poster.webp";

export const POSTER_SRC: string | null = poster;

/** Shown by screen readers in place of the poster. Empty = purely decorative. */
export const POSTER_ALT = "";
