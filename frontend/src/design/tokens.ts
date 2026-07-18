/**
 * Design tokens, ported 1:1 from the Claude Design prototype (Pharos.dc.html).
 *
 * Everything is expressed as CSS custom properties set on the app root, so the
 * whole UI restyles when the theme or accent changes. Neutrals are fixed;
 * accents are generated in oklch from a single hue.
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
  | "stone";

export const ACCENTS: { key: AccentKey; name: string }[] = [
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
};

type Vars = Record<string, string>;

export function neutralVars(t: ThemeMode): Vars {
  if (t === "dark")
    return {
      "--c-bg": "#141519",
      "--c-sf": "#1c1e24",
      "--c-rail": "#101115",
      "--c-read": "#0f1013",
      "--c-sheet": "#22252c",
      "--c-hdr": "#1a1c21",
      "--c-bd": "#2a2e35",
      "--c-bds": "#3a3f48",
      "--c-hv": "#23272e",
      "--c-tx": "#e6e8ec",
      "--c-tx2": "#9aa1ad",
      "--c-tx3": "#666d78",
      "--c-err": "#f0736f",
      "--c-err-soft": "#3a2321",
      "--c-ok": "oklch(0.72 0.13 150)",
      "--c-ok-soft": "color-mix(in oklch, oklch(0.72 0.13 150) 18%, transparent)",
      "--sh-sm": "0 1px 2px rgba(0,0,0,.4)",
      "--sh-md": "0 2px 12px rgba(0,0,0,.5)",
      "--sh-pop": "0 12px 34px rgba(0,0,0,.6)",
      "--c-overlay": "rgba(0,0,0,.55)",
    };
  return {
    "--c-bg": "#fafbfc",
    "--c-sf": "#ffffff",
    "--c-rail": "#f2f3f5",
    "--c-read": "#f1f2f4",
    "--c-sheet": "#ffffff",
    "--c-hdr": "#f7f8fa",
    "--c-bd": "#e6e8ec",
    "--c-bds": "#d4d8de",
    "--c-hv": "#f0f2f5",
    "--c-tx": "#1e222a",
    "--c-tx2": "#5a616e",
    "--c-tx3": "#8b93a1",
    "--c-err": "#d93a3a",
    "--c-err-soft": "#fdeceb",
    "--c-ok": "oklch(0.5 0.13 150)",
    "--c-ok-soft": "color-mix(in oklch, oklch(0.6 0.13 150) 15%, transparent)",
    "--sh-sm": "0 1px 2px rgba(20,23,33,.05)",
    "--sh-md": "0 2px 10px rgba(20,23,33,.08)",
    "--sh-pop": "0 12px 34px rgba(20,23,33,.15)",
    "--c-overlay": "rgba(20,23,33,.4)",
  };
}

export function accentVars(key: AccentKey, t: ThemeMode): Vars {
  const H = HUE[key] ?? HUE.indigo;
  if (t === "dark")
    return {
      "--c-ac": `oklch(0.74 0.135 ${H})`,
      "--c-ach": `oklch(0.80 0.135 ${H})`,
      "--c-acs": `oklch(0.30 0.05 ${H})`,
      "--c-acsb": `oklch(0.42 0.07 ${H})`,
      "--c-acc": "#14161b",
      "--c-ring": `oklch(0.74 0.135 ${H} / 0.5)`,
      "--c-aclink": `oklch(0.80 0.11 ${H})`,
    };
  const L = key === "amber" ? 0.72 : key === "coral" ? 0.63 : 0.565;
  const C = key === "stone" ? 0.085 : 0.14;
  return {
    "--c-ac": `oklch(${L} ${C} ${H})`,
    "--c-ach": `oklch(${L - 0.06} ${C} ${H})`,
    "--c-acs": `oklch(0.966 0.03 ${H})`,
    "--c-acsb": `oklch(0.90 0.055 ${H})`,
    "--c-acc": key === "amber" ? "#3a2c00" : "#ffffff",
    "--c-ring": `oklch(${L} ${C} ${H} / 0.32)`,
    "--c-aclink": `oklch(${L - 0.03} ${C} ${H})`,
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
