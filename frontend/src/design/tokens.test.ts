import { describe, expect, it } from "vitest";

import { resolveTheme } from "./tokens";

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
