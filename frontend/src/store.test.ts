import { beforeEach, describe, expect, it } from "vitest";

import {
  DETAIL_OVERLAY_MAX_WIDTH,
  MAX_INK_HISTORY,
  MAX_INK_WIDTH,
  MIN_INK_WIDTH,
  capHistory,
  clampInkWidth,
  remapOp,
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

describe("capHistory (bounded ink undo/redo)", () => {
  it("passes a short list through untouched", () => {
    const ops = [1, 2, 3];
    expect(capHistory(ops)).toEqual([1, 2, 3]);
  });

  it("drops the oldest entries once the cap is hit, keeping the newest", () => {
    const ops = Array.from({ length: MAX_INK_HISTORY + 10 }, (_, i) => i);
    const capped = capHistory(ops);
    expect(capped.length).toBe(MAX_INK_HISTORY);
    expect(capped[0]).toBe(10);
    expect(capped[capped.length - 1]).toBe(MAX_INK_HISTORY + 9);
  });
});

describe("remapOp (a recreated row's new id must reach the WHOLE history)", () => {
  const oldRow = { id: "old", points: [], color: "ink", width: 2 } as never;
  const newRow = { id: "new", points: [], color: "ink", width: 2 } as never;

  it("repoints an add op at the recreated stroke", () => {
    const out = remapOp({ kind: "add", stroke: oldRow }, "old", newRow);
    expect(out).toEqual({ kind: "add", stroke: newRow });
  });

  it("repoints only the matching stroke inside a remove op", () => {
    const other = { id: "other" } as never;
    const out = remapOp({ kind: "remove", strokes: [oldRow, other] }, "old", newRow);
    expect(out).toEqual({ kind: "remove", strokes: [newRow, other] });
  });

  it("repoints both sides of an edit op", () => {
    const out = remapOp({ kind: "edit", removed: [oldRow], added: [oldRow] }, "old", newRow);
    expect(out).toEqual({ kind: "edit", removed: [newRow], added: [newRow] });
  });

  it("repoints a tape placement — the case that duplicated a strip", () => {
    const out = remapOp({ kind: "tape-add", tape: oldRow }, "old", newRow);
    expect(out).toEqual({ kind: "tape-add", tape: newRow });
  });

  it("repoints a tape edit's bare id", () => {
    const out = remapOp(
      { kind: "tape-edit", id: "old", before: { w: 1 }, after: { w: 2 } },
      "old",
      newRow,
    );
    expect(out).toEqual({ kind: "tape-edit", id: "new", before: { w: 1 }, after: { w: 2 } });
  });

  it("leaves an op that never mentioned the old id untouched", () => {
    const op = { kind: "add", stroke: { id: "other" } } as never;
    expect(remapOp(op, "old", newRow)).toBe(op);
  });
});

describe("clampInkWidth (the 1-100 thickness range, mirroring the backend bounds)", () => {
  it("passes an in-range width through untouched", () => {
    expect(clampInkWidth(24)).toBe(24);
  });

  it("clamps below MIN_INK_WIDTH and above MAX_INK_WIDTH", () => {
    expect(clampInkWidth(0)).toBe(MIN_INK_WIDTH);
    expect(clampInkWidth(-5)).toBe(MIN_INK_WIDTH);
    expect(clampInkWidth(500)).toBe(MAX_INK_WIDTH);
  });

  it("falls back to the floor for a non-finite value rather than storing garbage", () => {
    expect(clampInkWidth(NaN)).toBe(MIN_INK_WIDTH);
    expect(clampInkWidth(Infinity)).toBe(MIN_INK_WIDTH);
  });
});

describe("setInkWidth (regression: the slider must never store a width the server would refuse)", () => {
  it("clamps on the way into the store", () => {
    useUI.getState().setInkWidth(9999);
    expect(useUI.getState().inkWidth).toBe(MAX_INK_WIDTH);
  });
});

describe("pushInkOps (regression: a long note-taking session must not grow the undo stack forever)", () => {
  beforeEach(() => {
    useUI.setState({ inkPast: [], inkFuture: [], inkOpsKey: "" });
  });

  it("caps inkPast at MAX_INK_HISTORY as ops accumulate one at a time", () => {
    const { pushInkOps } = useUI.getState();
    for (let i = 0; i < MAX_INK_HISTORY + 25; i++) {
      pushInkOps("doc a", [{ kind: "add", stroke: { id: `s${i}` } as never }]);
    }
    expect(useUI.getState().inkPast.length).toBe(MAX_INK_HISTORY);
  });
});
