/**
 * Design tokens.
 *
 * The structure came from the Claude Design prototype (Pharos.dc.html); the
 * palette is now the brand's, measured off the logo and login poster rather
 * than chosen by eye:
 *
 *     navy  #0C2040  oklch(0.247 0.066 259)   the P mark, and the text colour
 *     gold  #F8C040  oklch(0.837 0.151  84)   the beam
 *     teal  #189090  oklch(0.594 0.097 195)   the secondary wedge, success
 *     paper #F7F2E9  oklch(0.963 0.013  82)   the ground
 *
 * Note the ground and the gold share a hue (82 vs 84) — they are one warm
 * family, with the navy as the single cool anchor opposite them. That is the
 * relationship the whole palette maintains, and it is why soft accent states
 * are mixed into the ground rather than tinted independently.
 *
 * Everything is emitted as CSS custom properties on the app root, so the whole
 * UI restyles when the theme or accent changes — and because components only
 * ever reference var(--c-*), a palette change touches this file alone.
 */
import type { CSSProperties } from "react";

export type ThemeMode = "light" | "dark";
export type AccentKey =
  | "mint"
  | "sky"
  | "pine"
  | "indigo"
  | "lilac"
  | "coral"
  | "amber"
  | "stone"
  | "pharos"
  | "beacon";

export const ACCENTS: { key: AccentKey; name: string }[] = [
  // The two brand accents lead: they are the identity, the rest are preference.
  { key: "pharos", name: "灯塔蓝" },
  { key: "beacon", name: "灯塔金" },
  { key: "mint", name: "薄荷" },
  { key: "sky", name: "天蓝" },
  { key: "pine", name: "松绿" },
  { key: "indigo", name: "靛蓝" },
  { key: "lilac", name: "丁香" },
  { key: "coral", name: "珊瑚" },
  { key: "amber", name: "琥珀" },
  { key: "stone", name: "石青" },
];

const HUE: Record<AccentKey, number> = {
  mint: 172,
  sky: 236,
  pine: 150,
  indigo: 268,
  lilac: 305,
  coral: 25,
  amber: 78,
  stone: 220,
  // Sampled from the brand assets: the navy of the P mark (#0C2040) and the
  // gold of its beam (#F8C040), converted to oklch hue.
  pharos: 259,
  beacon: 84,
};

type Vars = Record<string, string>;

/**
 * Neutrals, drawn from the brand assets rather than from a generic grey ramp.
 *
 * Sampled from the logo and poster: navy #0C2040, paper #F8F0E8 / #F0E8D0,
 * gold #F8C040, teal #189090. The greys are therefore *warm* — a faint cream
 * cast rather than the cold blue-grey of the original palette — which is both
 * what the artwork does and easier on the eyes over a long reading session.
 *
 * The one deliberate exception is `--c-sheet`, the paper a PDF is painted on:
 * it stays pure white. Tinting it would tint every scanned page and every
 * figure with it, and a paper's own whites are content, not chrome.
 */
export function neutralVars(t: ThemeMode): Vars {
  if (t === "dark")
    return {
      // Dark mode is the brand navy taken down, not neutral charcoal, so the
      // gold and teal keep the same relationship to the ground they have in
      // the logo.
      "--c-bg": "#0B1524",
      "--c-sf": "#122033",
      "--c-rail": "#08111D",
      "--c-read": "#070E18",
      "--c-sheet": "#1A2A3E",
      "--c-hdr": "#0E1B2C",
      "--c-bd": "#1E3049",
      "--c-bds": "#2B4260",
      "--c-hv": "#18293F",
      "--c-tx": "#EDE7DC",
      "--c-tx2": "#A2B0C2",
      "--c-tx3": "#6B7C93",
      "--c-err": "#F0736F",
      "--c-err-soft": "#3A2321",
      "--c-ok": "oklch(0.74 0.10 195)",
      "--c-ok-soft": "color-mix(in oklab, oklch(0.74 0.10 195) 24%, var(--c-bg))",
      "--sh-sm": "0 1px 2px rgba(0,0,0,.45)",
      "--sh-md": "0 2px 12px rgba(0,0,0,.55)",
      "--sh-pop": "0 12px 34px rgba(0,0,0,.65)",
      "--c-overlay": "rgba(4,10,18,.6)",
    };
  return {
    "--c-bg": "#F7F2E9",
    "--c-sf": "#FFFCF7",
    "--c-rail": "#F1EADD",
    "--c-read": "#EDE6D9",
    "--c-sheet": "#FFFFFF",
    "--c-hdr": "#F4EEE3",
    "--c-bd": "#E3DACA",
    "--c-bds": "#CFC3AE",
    "--c-hv": "#EFE7D9",
    "--c-tx": "#0C2040",
    "--c-tx2": "#4A5B72",
    "--c-tx3": "#8A93A0",
    "--c-err": "#C2412F",
    "--c-err-soft": "color-mix(in oklab, #C2412F 12%, var(--c-bg))",
    // Teal from the logo beam, used for the 已译 badge and success states.
    "--c-ok": "oklch(0.594 0.097 195)",
    "--c-ok-soft": "color-mix(in oklab, oklch(0.594 0.097 195) 16%, var(--c-bg))",
    // Shadows tinted navy rather than black, so they read as depth on cream
    // instead of as dirt.
    "--sh-sm": "0 1px 2px rgba(12,32,64,.06)",
    "--sh-md": "0 2px 10px rgba(12,32,64,.09)",
    "--sh-pop": "0 12px 34px rgba(12,32,64,.16)",
    "--c-overlay": "rgba(12,32,64,.42)",
  };
}

export function accentVars(key: AccentKey, t: ThemeMode): Vars {
  const H = HUE[key] ?? HUE.indigo;
  if (t === "dark") {
    // On the deep-navy ground the accent has to come UP to be visible, so the
    // brand navy is lightened into the same blue rather than used at its own
    // near-black value; the gold needs no lift.
    const dl = key === "beacon" ? 0.82 : 0.72;
    const dc = key === "pharos" ? 0.09 : key === "beacon" ? 0.15 : 0.135;
    const dark = `oklch(${dl} ${dc} ${H})`;
    return {
      "--c-ac": dark,
      "--c-ach": `oklch(${dl + 0.06} ${dc} ${H})`,
      // Same reasoning as light mode, anchored on the navy surface instead of
      // sand, and mixed in oklab so the hue path cannot detour.
      "--c-acs": `color-mix(in oklab, ${dark} 20%, #122033)`,
      "--c-acsb": `color-mix(in oklab, ${dark} 38%, #122033)`,
      "--c-acc": "#08111D",
      "--c-ring": `oklch(${dl} ${dc} ${H} / 0.5)`,
      "--c-aclink": `oklch(${dl + 0.06} ${dc - 0.02} ${H})`,
    };
  }
  // Lightness/chroma per accent. The two brand values are measured from the
  // logo, not eyeballed: #0C2040 is oklch(0.247 0.066 259) and #F8C040 is
  // oklch(0.837 0.151 84). 灯塔蓝 is lifted a touch from the true navy so its
  // hover state has somewhere darker to go.
  const L =
    key === "pharos" ? 0.30 : key === "beacon" ? 0.837 : key === "amber" ? 0.72 : key === "coral" ? 0.63 : 0.565;
  const C =
    key === "pharos" ? 0.066 : key === "beacon" ? 0.151 : key === "stone" ? 0.085 : 0.14;
  // A light accent cannot carry light text at any readable contrast, so those
  // get the brand navy instead. Guessing wrong here is an accessibility bug,
  // not a taste one.
  const darkTextOnAccent = key === "beacon" || key === "amber";
  const accent = `oklch(${L} ${C} ${H})`;
  return {
    "--c-ac": accent,
    "--c-ach": `oklch(${L > 0.6 ? L - 0.08 : L - 0.055} ${C} ${H})`,
    // The pale states — a selected row, an active nav item, a badge — are built
    // from a warm SAND rather than from a light tint of the accent, and only
    // then nudged toward the accent's hue.
    //
    // A pale tint of the navy accent renders #d3dbe7: a cold blue-grey patch
    // on warm paper, which is exactly what "some buttons are still blue" was
    // describing. Anchoring on sand keeps every soft state in the ground's
    // family, so selection reads as the same paper pressed a little deeper —
    // the accent still identifies itself in the solid elements (buttons,
    // active icons, focus rings, links) where it has the contrast to do so.
    //
    // Mixed `in oklab`, deliberately, NOT `in oklch`: oklch interpolates hue
    // polarly, and blending 259° with the ground's 82° travels through 177° of
    // arc, so the midpoint lands in green. oklab interpolates rectangularly and
    // has no such path.
    "--c-acs": `color-mix(in oklab, ${accent} 9%, oklch(0.915 0.026 82))`,
    "--c-acsb": `color-mix(in oklab, ${accent} 18%, oklch(0.845 0.042 82))`,
    "--c-acc": darkTextOnAccent ? "#2B2000" : "#FFFCF7",
    "--c-ring": `oklch(${L} ${C} ${H} / 0.32)`,
    // Links must stay legible on cream, so a pale accent is darkened rather
    // than used at face value.
    "--c-aclink": `oklch(${Math.min(L, 0.48)} ${C} ${H})`,
  };
}

/** The swatch colour for an accent, used by the settings picker. */
export function accentSwatch(key: AccentKey, t: ThemeMode): string {
  return accentVars(key, t)["--c-ac"];
}

/** All theme variables, ready to spread onto the root element's `style`. */
export function themeStyle(mode: ThemeMode, accent: AccentKey): CSSProperties {
  return { ...neutralVars(mode), ...accentVars(accent, mode) } as CSSProperties;
}
