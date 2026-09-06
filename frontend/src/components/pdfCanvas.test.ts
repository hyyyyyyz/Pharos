import { describe, expect, it } from "vitest";
import { pageDpr } from "./PdfCanvas";

/** A Letter page in CSS pixels at some zoom. */
const letter = (zoom: number): [number, number] => [612 * zoom, 792 * zoom];
/** A0 — a conference poster, and a real thing to open in a paper reader. */
const a0 = (zoom: number): [number, number] => [2384 * zoom, 3370 * zoom];

const px = (dpr: number, [w, h]: [number, number]): number =>
  Math.floor(w * dpr) * Math.floor(h * dpr);

describe("pageDpr (regression: 显存 — one page must not exhaust the GPU on its own)", () => {
  it("leaves an ordinary page alone", () => {
    expect(pageDpr(2, ...letter(1))).toBe(2);
    expect(pageDpr(2, ...letter(2))).toBeCloseTo(2, 5);
  });

  it("holds an A0 poster under the area ceiling", () => {
    // Unclamped this is 128.6 megapixels — 514 MB in a single backing store.
    const dpr = pageDpr(2, ...a0(4));
    expect(dpr).toBeLessThan(2);
    expect(px(dpr, a0(4))).toBeLessThanOrEqual(16_000_000);
  });

  it("holds every page under the GPU's texture side limit", () => {
    // Past this a canvas does not fail loudly, it just never paints — a blank
    // page with nothing in the console.
    for (const size of [a0(4), a0(1), letter(4), [400, 20000] as [number, number]]) {
      const dpr = pageDpr(2, ...size);
      expect(Math.max(size[0], size[1]) * dpr).toBeLessThanOrEqual(8192 + 1e-9);
    }
  });

  it("never magnifies past the dpr it was given", () => {
    expect(pageDpr(1, ...letter(1))).toBe(1);
    expect(pageDpr(0.5, ...letter(1))).toBe(0.5);
  });

  it("still renders something for a degenerate page", () => {
    expect(pageDpr(2, 0, 0)).toBe(2);
    expect(pageDpr(2, ...a0(40))).toBeGreaterThan(0);
  });
});
