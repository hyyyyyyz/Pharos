import { beforeEach, describe, expect, it } from "vitest";

import type { InkStrokeRow } from "./api/types";
import type { InkOp } from "./store";
import {
  DETAIL_OVERLAY_MAX_WIDTH,
  MAX_INK_HISTORY,
  MAX_INK_HISTORY_SAMPLES,
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
  /** One `add` carrying `samples` points, tagged so order is checkable. */
  const add = (tag: number, samples = 0): InkOp => ({
    kind: "add",
    stroke: { id: `s${tag}`, points: new Array(samples).fill({ x: 0, y: 0, p: 0.5 }) } as never,
  });
  const tagOf = (op: InkOp): string => (op.kind === "add" ? op.stroke.id : "?");

  it("passes a short list through untouched", () => {
    const ops = [add(1), add(2), add(3)];
    expect(capHistory(ops)).toBe(ops);
  });

  it("drops the oldest entries once the cap is hit, keeping the newest", () => {
    const ops = Array.from({ length: MAX_INK_HISTORY + 10 }, (_, i) => add(i));
    const capped = capHistory(ops);
    expect(capped.length).toBe(MAX_INK_HISTORY);
    expect(tagOf(capped[0]!)).toBe("s10");
    expect(tagOf(capped[capped.length - 1]!)).toBe(`s${MAX_INK_HISTORY + 9}`);
  });

  it("caps on retained SAMPLES, not only on entry count", () => {
    // A lasso drag over forty strokes pushes ONE op holding eighty rows. Two
    // hundred of those is sixteen thousand strokes' worth of points pinned in
    // memory — the count cap never fires, and the renderer is killed for
    // memory instead.
    const heavy = Math.ceil(MAX_INK_HISTORY_SAMPLES / 4);
    const ops = Array.from({ length: 12 }, (_, i) => add(i, heavy));
    const capped = capHistory(ops);
    expect(capped.length).toBeLessThan(12);
    expect(capped.length).toBeLessThanOrEqual(5);
    // Newest kept, oldest dropped — undo reaches the newest first.
    expect(tagOf(capped[capped.length - 1]!)).toBe("s11");
  });

  it("counts a batch's members, not the batch", () => {
    const inner = Array.from({ length: 8 }, (_, i) => add(i, MAX_INK_HISTORY_SAMPLES / 4));
    const ops: InkOp[] = [{ kind: "batch", ops: inner }, add(99, 1)];
    // The batch alone is twice the budget, so only the newest op survives.
    expect(capHistory(ops).length).toBe(1);
    expect(tagOf(capHistory(ops)[0]!)).toBe("s99");
  });

  it("always keeps the newest op, however large", () => {
    // A history that cannot hold even one entry would make undo silently do
    // nothing after a big edit — worse than forgetting an old one.
    const ops = [add(1, 10), add(2, MAX_INK_HISTORY_SAMPLES * 3)];
    const capped = capHistory(ops);
    expect(capped.length).toBe(1);
    expect(tagOf(capped[0]!)).toBe("s2");
  });

  it("leaves 胶带 ops uncounted — a strip is a rectangle, not a sample list", () => {
    const ops: InkOp[] = Array.from({ length: 50 }, (_, i) => ({
      kind: "tape-add",
      tape: { id: `t${i}` } as never,
    }));
    expect(capHistory(ops).length).toBe(50);
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

describe("remapInkRow (regression: an optimistic id must be repaired wherever it has sunk to)", () => {
  const row = (id: string): InkStrokeRow => ({ id }) as never;
  const settled = row("server-1");

  beforeEach(() => {
    useUI.setState({ inkPast: [], inkFuture: [], inkOpsKey: "doc a" });
  });

  it("reaches an edit nested inside a batch", () => {
    // The case the old hand-rolled walk in `InkLayer.settle` could not see: it
    // skipped anything whose `kind` was not "edit", and a lasso drag over ink
    // AND 胶带 folds its edit inside a batch. The temp id survived on the undo
    // stack, so undoing that drag re-added a stroke the server already held
    // under its real id — one drag, two copies of the ink.
    useUI.setState({
      inkPast: [
        {
          kind: "batch",
          ops: [
            { kind: "tape-edit", id: "t1", before: {}, after: {} },
            { kind: "edit", removed: [], added: [row("temp-1")] },
          ],
        },
      ],
    });
    useUI.getState().remapInkRow("temp-1", settled);
    const batch = useUI.getState().inkPast[0]!;
    expect(batch.kind).toBe("batch");
    const inner = batch.kind === "batch" ? batch.ops[1]! : batch;
    expect(inner.kind === "edit" && inner.added[0]!.id).toBe("server-1");
  });

  it("reaches an op that a later op has buried", () => {
    // `settleStack` only ever repaired `past[past.length - 1]`. Writing fast —
    // the normal way to write — puts the next stroke's op on top before the
    // previous POST returns, and then the repair silently did nothing.
    useUI.setState({
      inkPast: [
        { kind: "add", stroke: row("temp-1") },
        { kind: "add", stroke: row("other") },
      ],
    });
    useUI.getState().remapInkRow("temp-1", settled);
    const first = useUI.getState().inkPast[0]!;
    expect(first.kind === "add" && first.stroke.id).toBe("server-1");
  });

  it("repairs the redo stack too", () => {
    // An op that has been undone is sitting in `inkFuture`, and redoing it
    // must not name an id the server never had.
    useUI.setState({ inkFuture: [{ kind: "remove", strokes: [row("temp-1")] }] });
    useUI.getState().remapInkRow("temp-1", settled);
    const op = useUI.getState().inkFuture[0]!;
    expect(op.kind === "remove" && op.strokes[0]!.id).toBe("server-1");
  });

  it("leaves ops that do not mention the id alone", () => {
    const before: InkOp[] = [{ kind: "add", stroke: row("other") }];
    useUI.setState({ inkPast: before });
    useUI.getState().remapInkRow("temp-1", settled);
    expect(useUI.getState().inkPast[0]).toBe(before[0]);
  });
});
