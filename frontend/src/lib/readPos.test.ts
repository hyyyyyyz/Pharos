import { afterEach, describe, expect, it, vi } from "vitest";

import { fractionsOf, loadReadPos, saveReadPos, scrollTarget } from "./readPos";

/**
 * The node test environment has no localStorage — the module guards for that
 * at runtime, but the storage half of the round trip is worth pinning, so a
 * minimal in-memory stand-in is stubbed in here.
 */
function stubStorage(): void {
  const mem = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
}

describe("readPos", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("round-trips a position through storage with validation", () => {
    stubStorage();
    saveReadPos("p orig", { fy: 0.42, fx: 0.25, zoom: 2 });
    expect(loadReadPos("p orig")).toEqual({ fy: 0.42, fx: 0.25, zoom: 2 });
    expect(loadReadPos("missing")).toBeNull();
  });

  it("treats a zoom of null as fit mode, not zero", () => {
    stubStorage();
    saveReadPos("p orig", { fy: 0.1, fx: 0, zoom: null });
    expect(loadReadPos("p orig")?.zoom).toBeNull();
  });

  it("rejects a corrupted record instead of trusting it", () => {
    stubStorage();
    localStorage.setItem("ph-read-pos-v1", "{not json");
    expect(loadReadPos("p orig")).toBeNull();
  });

  it("clamps stale fractions into the live scroll box", () => {
    // scrollHeight barely taller than the viewport: fy=1 is legal but fx
    // beyond it must clamp to the real maxima.
    const target = scrollTarget(
      { fy: 1, fx: 1, zoom: null },
      { scrollWidth: 800, scrollHeight: 4000, clientWidth: 800, clientHeight: 1000 },
    );
    expect(target).toEqual({ top: 3000, left: 0 });
  });

  it("never scrolls a box with nothing to scroll", () => {
    const target = scrollTarget(
      { fy: 0.8, fx: 0.8, zoom: null },
      { scrollWidth: 500, scrollHeight: 500, clientWidth: 800, clientHeight: 900 },
    );
    expect(target).toEqual({ top: 0, left: 0 });
  });

  it("fractionsOf inverts scrollTarget", () => {
    const vp = {
      scrollLeft: 0,
      scrollTop: 750,
      scrollWidth: 800,
      scrollHeight: 4000,
      clientWidth: 800,
      clientHeight: 1000,
    };
    const f = fractionsOf(vp);
    expect(f.fy).toBeCloseTo(0.25, 6);
    expect(f.fx).toBe(0);
  });
});
