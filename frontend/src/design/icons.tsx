/**
 * The Pharos icon set — hand-drawn 20×20 line icons ported from the design
 * prototype. Every icon inherits `currentColor`, so colour is set by the parent.
 */
import type { SVGProps } from "react";

type Node =
  | string
  | { c: true; cx: number; cy: number; r: number; fill?: string }
  | { r0: { x: number; y: number; width: number; height: number; rx: number } };

interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  size?: number;
  sw?: number;
}

/**
 * One size, one stroke. Every icon renders at 16px with a 1.5 stroke and takes
 * its colour from the text role it sits in; a caller may pass `size` for the
 * few places that are not a control (an empty state is 24). The old per-icon
 * defaults (13, 14, 15, 22…) are gone on purpose: they were what made the
 * toolbar and the lists look like they used different sets.
 */
function make(paths: Node[], _legacy?: unknown) {
  return function Icon({ size = 16, sw = 1.5, ...rest }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth={sw}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        {...rest}
      >
        {paths.map((p, i) => {
          if (typeof p === "string") return <path key={i} d={p} />;
          if ("c" in p) return <circle key={i} cx={p.cx} cy={p.cy} r={p.r} fill={p.fill} />;
          return <rect key={i} {...p.r0} />;
        })}
      </svg>
    );
  };
}

export const Icons = {
  library: make(["M4 4.5h3.2v11H4zM8 4.5h3.2v11H8z", "M12.2 5l2.9.5-1.6 10.4-2.9-.5z"]),
  search: make(["M13 13l3.4 3.4", { c: true, cx: 9, cy: 9, r: 5 }]),
  kb: make(["M10 3.4 3.8 6.5 10 9.6l6.2-3.1z", "M3.8 10 10 13.1 16.2 10", "M3.8 13.2 10 16.3l6.2-3.1"]),
  writing: make(["M4.5 15.3 14 5.8l2.6 2.6-9.5 9.5H4.5z", "M12.6 7.2l2.6 2.6"]),
  // 手写 ink tools: a nibbed stylus, a block eraser, and the undo/redo pair.
  pen: make(["M4 16l1.2-4.2L14 3l3 3-8.8 8.8L4 16z", "M12.5 4.5l3 3"]),
  eraser: make(["M3.5 13.5 10 7l4.5 4.5-6.5 6.5H5.5z", "M8 9l4.5 4.5"]),
  undo: make(["M6 5.5h6.5a4 4 0 0 1 0 8H5.5", "M8 3 5.5 5.5 8 8"]),
  redo: make(["M14 5.5H7.5a4 4 0 0 0 0 8H14.5", "M12 3l2.5 2.5L12 8"]),
  // 套索: a loop of rope with its tail crossing — the gesture the tool performs.
  lasso: make(
    [
      "M13.9 5.1c2.9 1 4.4 3.2 3.6 5.5-.8 2.2-3.7 3.7-6.9 3.7-1.2 0-2.4-.2-3.4-.5",
      "M6.6 14.9c-2.6-1-4.3-3-2.2-5.6C6.5 6.6 10 5 10.6 4.9",
      "M6.7 14.9c-.5.2-.9.6-.9 1.1 0 .8 1 1.2 1.9 1.2",
    ],
  ),
  // 每日论文: a dated sheet with two lines of text — a digest that arrives daily.
  daily: make(
    [
      { r0: { x: 3.5, y: 4.8, width: 13, height: 11.4, rx: 1.4 } },
      "M3.5 8.4h13",
      "M7 3.4v2.8M13 3.4v2.8",
      "M6.4 11.4h7.2M6.4 13.7h4.6",
    ],
    { sw: 1.4 },
  ),
  // 模型配置: three sliders. The rail is not behind it any more, so the knobs
  // are drawn by breaking the track rather than by painting over it.
  sliders: make([
    "M3.5 6h2M8.5 6h8",
    "M3.5 10h6M12.5 10h4",
    "M3.5 14h1M7.5 14h9",
    { c: true, cx: 7, cy: 6, r: 1.5 },
    { c: true, cx: 11, cy: 10, r: 1.5 },
    { c: true, cx: 6, cy: 14, r: 1.5 },
  ]),
  // 设置: a gear. The old icon (three sliders) now belongs to 模型配置 alone.
  gear: make(["M8.9 4.4 L9.0 2.7 L11.0 2.7 L11.1 4.4 L13.1 5.2 L14.5 4.1 L15.9 5.5 L14.8 6.9 L15.6 8.9 L17.3 9.0 L17.3 11.0 L15.6 11.1 L14.8 13.1 L15.9 14.5 L14.5 15.9 L13.1 14.8 L11.1 15.6 L11.0 17.3 L9.0 17.3 L8.9 15.6 L6.9 14.8 L5.5 15.9 L4.1 14.5 L5.2 13.1 L4.4 11.1 L2.7 11.0 L2.7 9.0 L4.4 8.9 L5.2 6.9 L4.1 5.5 L5.5 4.1 L6.9 5.2z", { c: true, cx: 10, cy: 10, r: 2.3 }]),
  // 运行: one source fanning out into two steps — a run is a flow, not a list.
  runs: make([
    { c: true, cx: 4.8, cy: 10, r: 1.8 },
    { c: true, cx: 15.2, cy: 4.8, r: 1.8 },
    { c: true, cx: 15.2, cy: 15.2, r: 1.8 },
    "M6.4 9.2 13.6 5.6M6.4 10.8l7.2 3.6",
  ]),
  // 文献探索: a compass. The magnifier is reserved for the search box.
  compass: make([{ c: true, cx: 10, cy: 10, r: 6.8 }, "M12.9 7.1 11.3 11.3 7.1 12.9 8.7 8.7z"]),
  // 研究项目: a folder holding a small graph of two linked nodes.
  project: make([
    "M2.8 5.8a1 1 0 0 1 1-1h3.1l1.3 1.6h8a1 1 0 0 1 1 1v7.6a1 1 0 0 1-1 1H3.8a1 1 0 0 1-1-1z",
    { c: true, cx: 7.2, cy: 11.4, r: 1.1 },
    { c: true, cx: 12.8, cy: 11.4, r: 1.1 },
    "M8.3 11.4h3.4",
  ]),
  // 管理后台: a shield with a tick.
  shield: make(["M10 3 16 5.2v4.4c0 3.4-2.4 5.9-6 7.4-3.6-1.5-6-4-6-7.4V5.2z", "M7.4 10l1.9 1.9 3.3-3.6"]),
  chat: make(["M4 5.5A1.5 1.5 0 0 1 5.5 4h9A1.5 1.5 0 0 1 16 5.5v6a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3V13h0A1.5 1.5 0 0 1 4 11.5z"]),
  folder: make(["M2.6 6a1 1 0 0 1 1-1h3l1.2 1.5H16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H3.6a1 1 0 0 1-1-1z"]),
  star: make(["M10 3.6l1.8 3.6 4 .6-2.9 2.8.7 4-3.6-1.9-3.6 1.9.7-4L4.2 7.8l4-.6z"]),
  inbox: make(["M3.5 5.5h11v9h-11z", "M3.5 11h3.2l1 1.6h4.6l1-1.6h1.7"]),
  trash: make(["M4.5 6h10", "M8 6V4.6h3V6", "M6 6l.7 8.4h6.1L13.5 6"]),
  /* ---------------------------------------------------------------------
     The 手写 tool icons, redrawn.

     The first cut of these reached for whatever primitive was nearest: a bare
     teardrop for the watercolour brush, a dot ringed by four ticks that reads
     as a loading spinner rather than a laser, a rectangle with two rules that
     reads as a form field rather than tape. At 15px an icon is carried by its
     SILHOUETTE, and several of these had the same one as each other.

     Each now says what its own tool does, and says it differently from its
     neighbours in the same toolbar: the wash has a wash under it, the laser
     emits, the brush has bristles, the tape is a torn strip on the slant.
     Same house style as the prototype's own icons above — 20-unit box,
     1.3-1.5 stroke, rounded joins, as few strokes as will carry it.
     --------------------------------------------------------------------- */

  lock: make(
    [
      { r0: { x: 4.8, y: 9.2, width: 10.4, height: 7.6, rx: 1.8 } },
      "M7.2 9.2V6.9a2.8 2.8 0 0 1 5.6 0v2.3",
    ],
  ),
  /** 水彩笔 — a drop, plus the wash it leaves behind. The drop on its own was
   *  any liquid at all; the wash under it is what this tool puts on a page. */
  droplet: make(
    [
      "M10 3.1c2.3 3 3.9 5.3 3.9 7.2a3.9 3.9 0 0 1-7.8 0c0-1.9 1.6-4.2 3.9-7.2z",
      "M4.4 16.4c1.5-.9 3-.9 4.6 0s3.1.9 4.6 0",
    ],
  ),
  /** 激光笔 — a point that EMITS: a beam with a burst at the far end, rather
   *  than a dot with ticks all round it (which read as a spinner). */
  laser: make(
    [
      { c: true, cx: 5.9, cy: 14.1, r: 1.7, fill: "currentColor" },
      "M7.3 12.7 12.9 7.1",
      "M12.3 4.5a7.2 7.2 0 0 1 3.1 3.1",
      "M14.5 2.8a10.4 10.4 0 0 1 2.6 2.6",
    ],
  ),
  /** 改样式 — a brush: handle, ferrule, bristles. Deliberately not a wand,
   *  which the still-deferred 魔法棒 (text transform) will want for itself. */
  styleBrush: make(
    ["M15.9 4.1 11.2 8.8", "M10.4 8 12 9.6 8.6 13 7 11.4z", "M7 11.4 3.9 16.1 8.6 13"],
  ),
  /** 胶带 — a strip on the slant with torn ends. */
  tape: make(
    [
      "M2.6 11.9 3.3 15.4l13.5-3.1-.7-3.5z",
      "M2.6 11.9 5.2 9.6l.5 2 2.6-2.3.5 2 2.6-2.3.5 2 2.6-2.3.5 2",
    ],
  ),
  /** 键盘 — the text tool. A keyboard rather than an "A" or a T-bar, because
   *  what this tool inserts is TYPED text, and the keyboard is the thing that
   *  is about to appear on a tablet when you tap it. The keys are drawn as
   *  three short rules plus a spacebar: at 15px individual keycaps turn to
   *  mush, and the silhouette is what has to read. */
  keyboard: make(
    [
      "M2.8 6.2h14.4v7.6H2.8z",
      "M5 8.4h1M8 8.4h1M11 8.4h1M14 8.4h1",
      "M6.6 11.4h6.8",
    ],
  ),
  /** 便利贴 — a square with its bottom-right corner turned up. The fold IS the
   *  icon: without it this is indistinguishable from a plain text box. */
  sticky: make(
    ["M4 3.8h12v8.4l-3.8 4H4z", "M16 12.2h-3.8v4"],
  ),
  cloud: make(["M6 13.5a3 3 0 0 1 .3-6 4 4 0 0 1 7.6 1.1A2.7 2.7 0 0 1 14 13.5z"]),
  sync: make(["M14.5 6.5A5 5 0 0 0 5.3 8", "M5.5 13.5A5 5 0 0 0 14.7 12", "M14.5 4.5v2h-2", "M5.5 15.5v-2h2"]),
  link: make(
    ["M8.5 11.5 11.5 8.5", "M9 7l1.2-1.2a2.5 2.5 0 0 1 3.5 3.5L12.5 10.5", "M11 13l-1.2 1.2a2.5 2.5 0 0 1-3.5-3.5L7.5 9.5"],
  ),
  plus: make(["M10 5.5v9M5.5 10h9"]),
  file: make(["M6 3.5h5l3 3V16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z", "M11 3.5V7h3"]),
  open: make(["M11 4.5h4.5V9", "M15.5 4.5 9 11", "M8 5.5H5v9.5h9.5v-3"]),
  alert: make([{ c: true, cx: 10, cy: 10, r: 6.2 }, "M10 6.6v4", "M10 13.2v.2"]),
  sun: make(
    [
      { c: true, cx: 10, cy: 10, r: 3.2 },
      "M10 2.6v1.6M10 15.8v1.6M2.6 10h1.6M15.8 10h1.6M4.7 4.7l1.2 1.2M14.1 14.1l1.2 1.2M15.3 4.7l-1.2 1.2M4.7 15.3l1.2-1.2",
    ],
  ),
  moon: make(["M15.4 12.3A5.8 5.8 0 1 1 9 4.1a4.6 4.6 0 0 0 6.4 8.2z"]),
  // 跟随系统: a display, not a half sun/moon. The choice is "whatever this
  // machine says", and the machine is the thing worth drawing.
  display: make(
    [{ r0: { x: 3.2, y: 4.5, width: 13.6, height: 9, rx: 1.4 } }, "M7.5 16.5h5M10 13.5v3"],
  ),
  palette: make(
    [
      { c: true, cx: 7, cy: 7, r: 2 },
      { c: true, cx: 13, cy: 7, r: 2 },
      { c: true, cx: 7, cy: 13, r: 2 },
      { c: true, cx: 13, cy: 13, r: 2 },
    ],
  ),
  close: make(["M5.5 5.5l7 7M12.5 5.5l-7 7"]),
  caretR: make(["M8 6l4 4-4 4"]),
  caretD: make(["M6 8l4 4 4-4"]),
  panelL: make([{ r0: { x: 3.5, y: 4.5, width: 13, height: 11, rx: 1.4 } }, "M8 4.5v11"]),
  panelR: make([{ r0: { x: 3.5, y: 4.5, width: 13, height: 11, rx: 1.4 } }, "M12 4.5v11"]),
  send: make(["M4 10h11", "M10 5l5 5-5 5"]),
  user: make([{ c: true, cx: 10, cy: 7.5, r: 3 }, "M4.8 16.2a5.2 5.2 0 0 1 10.4 0"]),
  check: make(["M5 10.5l3.2 3.4 6.8-7.4"]),
  eye: make(
    ["M2.5 10s3-5.5 7.5-5.5S17.5 10 17.5 10s-3 5.5-7.5 5.5S2.5 10 2.5 10z", { c: true, cx: 10, cy: 10, r: 2.2 }],
  ),
  /** …and on a strip that has been tapped open.
   *
   *  The slash is SHORT on purpose. A full-corner-to-corner one turns the eye
   *  into a circle with a line through it — the universal "prohibited" glyph,
   *  which says the wrong thing entirely — because at 15px the eye's own
   *  outline reads as a circle and the slash dominates it. Crossing only the
   *  pupil leaves the eye legible as an eye. */
  eyeOff: make(
    [
      "M2.5 10s3-5.5 7.5-5.5S17.5 10 17.5 10s-3 5.5-7.5 5.5S2.5 10 2.5 10z",
      "M7.6 12.4 12.4 7.6",
    ],
  ),
};
