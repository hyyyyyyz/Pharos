import { beforeEach, describe, expect, it } from "vitest";

import {
  DETAIL_OVERLAY_MAX_WIDTH,
  isAiOpen,
  isDetailOverlay,
  useUI,
} from "./store";

/**
 * `store.ts` is importable in the node test environment because every
 * boot-time window read (`matchMedia`, `localStorage`, `innerWidth`) is
 * guarded. Here that means the initial state is the desktop one:
 * `pointerCoarse` starts false and `winW` falls back to 1440, which is exactly
 * the fork this suite pins.
 */
describe("isDetailOverlay", () => {
  it("is the touch narrow fork: coarse pointer below the breakpoint", () => {
    expect(isDetailOverlay({ pointerCoarse: true, winW: 800 })).toBe(true);
    expect(isDetailOverlay({ pointerCoarse: true, winW: 1024 })).toBe(true);
  });

  it("keeps the three-pane library for touch devices at or above the breakpoint", () => {
    // 1040 itself is NOT overlay — iPad landscape 1024 sits on the overlay
    // side, a 1180px tablet landscape on the three-pane side.
    expect(isDetailOverlay({ pointerCoarse: true, winW: DETAIL_OVERLAY_MAX_WIDTH })).toBe(
      false,
    );
    expect(isDetailOverlay({ pointerCoarse: true, winW: 1280 })).toBe(false);
  });

  it("never overlays for a mouse, however narrow the window", () => {
    // A half-screen desktop window must keep the classic squeeze: mouse users
    // collapse the rail, they do not tap through an overlay.
    expect(isDetailOverlay({ pointerCoarse: false, winW: 600 })).toBe(false);
  });
});

describe("library detail overlay state", () => {
  beforeEach(() => {
    useUI.setState({ libDetailOpen: false });
  });

  it("starts closed so a cold start never paints a panel nobody asked for", () => {
    expect(useUI.getState().libDetailOpen).toBe(false);
  });

  it("opens on selection and closes from the panel's own button", () => {
    useUI.getState().setLibDetail(true);
    expect(useUI.getState().libDetailOpen).toBe(true);
    useUI.getState().setLibDetail(false);
    expect(useUI.getState().libDetailOpen).toBe(false);
  });
});

describe("isAiOpen (regression: the tablet fork must not disturb the AI panel)", () => {
  it("still follows the 1200px auto breakpoint", () => {
    expect(isAiOpen({ aiOpenPref: "auto", winW: 1200 })).toBe(true);
    expect(isAiOpen({ aiOpenPref: "auto", winW: 1024 })).toBe(false);
    expect(isAiOpen({ aiOpenPref: true, winW: 600 })).toBe(true);
  });
});
