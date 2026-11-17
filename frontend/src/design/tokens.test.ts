import { describe, expect, it } from "vitest";

import { ACCENTS, neutralVars, resolveTheme, scaleVars, themeStyle } from "./tokens";

describe("resolveTheme", () => {
  it("follows the OS when the user asked it to", () => {
    expect(resolveTheme("auto", true)).toBe("dark");
    expect(resolveTheme("auto", false)).toBe("light");
  });

  it("lets an explicit choice beat the OS, in both directions", () => {
    // The half that regresses: someone who picked 浅色 on a machine set to dark
    // did so knowing what the machine says. Having that revert — at sunset, on
    // the next OS theme change, or on the next reload — is the failure this
    // pins. It is symmetric, so 深色 on a light machine is pinned too.
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("is pure in the OS signal, so an explicit choice cannot drift", () => {
    // Same preference, both possible values of the media query, one answer.
    expect(resolveTheme("light", true)).toBe(resolveTheme("light", false));
    expect(resolveTheme("dark", true)).toBe(resolveTheme("dark", false));
  });
});

describe("design scale", () => {
  const px = (v: string) => Number.parseFloat(v);

  it("offers exactly six font sizes, none under 11px, and four radii", () => {
    const v = scaleVars();
    const sizes = Object.keys(v).filter((k) => k.startsWith("--fs-"));
    expect(sizes.sort()).toEqual(["--fs-base", "--fs-lg", "--fs-md", "--fs-meta", "--fs-sm", "--fs-xl"]);
    for (const k of sizes) expect(px(v[k])).toBeGreaterThanOrEqual(11);
    expect(Object.keys(v).filter((k) => k.startsWith("--r-")).sort()).toEqual(["--r-lg", "--r-md", "--r-sm", "--r-xs"]);
  });

  it("keeps sizes ascending and radii on the 4px grid", () => {
    const v = scaleVars();
    const order = ["meta", "sm", "base", "md", "lg", "xl"].map((k) => px(v[`--fs-${k}`]));
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    for (const k of ["xs", "sm", "md", "lg"]) expect(px(v[`--r-${k}`]) % 4).toBe(0);
  });

  it("defines the beam in both themes, and it does not depend on the accent", () => {
    expect(neutralVars("light")["--c-beam"]).toBe("oklch(0.837 0.151 84)");
    expect(neutralVars("dark")["--c-beam"]).toMatch(/^oklch\(/);
    for (const a of ACCENTS) {
      for (const m of ["light", "dark"] as const) {
        expect((themeStyle(m, a.key) as Record<string, string>)["--c-beam"]).toBe(neutralVars(m)["--c-beam"]);
      }
    }
  });

  it("ships the scale with every theme", () => {
    for (const m of ["light", "dark"] as const) {
      const style = themeStyle(m, "pharos") as Record<string, string>;
      expect(style["--fs-base"]).toBe("13px");
      expect(style["--ease"]).toBe("cubic-bezier(0.22, 1, 0.36, 1)");
    }
  });
});
