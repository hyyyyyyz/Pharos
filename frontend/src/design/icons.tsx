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

function make(paths: Node[], def: { size?: number; sw?: number } = {}) {
  return function Icon({ size = def.size ?? 18, sw = def.sw ?? 1.5, ...rest }: IconProps) {
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
    { size: 15, sw: 1.4 },
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
  settings: make(
    [
      "M3.5 6h13",
      "M3.5 10h13",
      "M3.5 14h13",
      { c: true, cx: 7, cy: 6, r: 1.7, fill: "var(--c-rail)" },
      { c: true, cx: 13, cy: 10, r: 1.7, fill: "var(--c-rail)" },
      { c: true, cx: 6, cy: 14, r: 1.7, fill: "var(--c-rail)" },
    ],
    { size: 16 },
  ),
  folder: make(["M2.6 6a1 1 0 0 1 1-1h3l1.2 1.5H16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H3.6a1 1 0 0 1-1-1z"], { size: 15, sw: 1.4 }),
  star: make(["M10 3.6l1.8 3.6 4 .6-2.9 2.8.7 4-3.6-1.9-3.6 1.9.7-4L4.2 7.8l4-.6z"], { size: 15, sw: 1.4 }),
  inbox: make(["M3.5 5.5h11v9h-11z", "M3.5 11h3.2l1 1.6h4.6l1-1.6h1.7"], { size: 15, sw: 1.4 }),
  trash: make(["M4.5 6h10", "M8 6V4.6h3V6", "M6 6l.7 8.4h6.1L13.5 6"], { size: 15, sw: 1.4 }),
  lock: make(
    [{ r0: { x: 5, y: 9.5, width: 10, height: 7.5, rx: 1.4 } }, "M7 9.5V7a3 3 0 0 1 6 0v2.5"],
    { size: 15, sw: 1.4 },
  ),
  droplet: make(
    ["M10 3.2c2.6 3.4 4.4 6 4.4 8.2a4.4 4.4 0 1 1-8.8 0c0-2.2 1.8-4.8 4.4-8.2z"],
    { size: 15, sw: 1.4 },
  ),
  cloud: make(["M6 13.5a3 3 0 0 1 .3-6 4 4 0 0 1 7.6 1.1A2.7 2.7 0 0 1 14 13.5z"], { size: 13, sw: 1.4 }),
  sync: make(["M14.5 6.5A5 5 0 0 0 5.3 8", "M5.5 13.5A5 5 0 0 0 14.7 12", "M14.5 4.5v2h-2", "M5.5 15.5v-2h2"], { size: 13, sw: 1.4 }),
  link: make(
    ["M8.5 11.5 11.5 8.5", "M9 7l1.2-1.2a2.5 2.5 0 0 1 3.5 3.5L12.5 10.5", "M11 13l-1.2 1.2a2.5 2.5 0 0 1-3.5-3.5L7.5 9.5"],
    { size: 14, sw: 1.4 },
  ),
  plus: make(["M10 5.5v9M5.5 10h9"], { size: 13, sw: 1.7 }),
  file: make(["M6 3.5h5l3 3V16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z", "M11 3.5V7h3"], { size: 14, sw: 1.4 }),
  open: make(["M11 4.5h4.5V9", "M15.5 4.5 9 11", "M8 5.5H5v9.5h9.5v-3"], { size: 14, sw: 1.4 }),
  spark: make(["M10 3.5l1.6 4.1 4.4 1.6-4.4 1.6L10 15l-1.6-4.2L4 9.2l4.4-1.6z"], { size: 15 }),
  alert: make([{ c: true, cx: 10, cy: 10, r: 6.2 }, "M10 6.6v4", "M10 13.2v.2"], { size: 22 }),
  sun: make(
    [
      { c: true, cx: 10, cy: 10, r: 3.2 },
      "M10 2.6v1.6M10 15.8v1.6M2.6 10h1.6M15.8 10h1.6M4.7 4.7l1.2 1.2M14.1 14.1l1.2 1.2M15.3 4.7l-1.2 1.2M4.7 15.3l1.2-1.2",
    ],
    { size: 16 },
  ),
  moon: make(["M15.4 12.3A5.8 5.8 0 1 1 9 4.1a4.6 4.6 0 0 0 6.4 8.2z"], { size: 16 }),
  // 跟随系统: a display, not a half sun/moon. The choice is "whatever this
  // machine says", and the machine is the thing worth drawing.
  display: make(
    [{ r0: { x: 3.2, y: 4.5, width: 13.6, height: 9, rx: 1.4 } }, "M7.5 16.5h5M10 13.5v3"],
    { size: 16, sw: 1.4 },
  ),
  palette: make(
    [
      { c: true, cx: 7, cy: 7, r: 2 },
      { c: true, cx: 13, cy: 7, r: 2 },
      { c: true, cx: 7, cy: 13, r: 2 },
      { c: true, cx: 13, cy: 13, r: 2 },
    ],
    { size: 16, sw: 1.4 },
  ),
  close: make(["M5.5 5.5l7 7M12.5 5.5l-7 7"], { size: 12 }),
  caretR: make(["M8 6l4 4-4 4"], { size: 14 }),
  caretD: make(["M6 8l4 4 4-4"], { size: 14 }),
  panelL: make([{ r0: { x: 3.5, y: 4.5, width: 13, height: 11, rx: 1.4 } }, "M8 4.5v11"], { size: 15, sw: 1.4 }),
  panelR: make([{ r0: { x: 3.5, y: 4.5, width: 13, height: 11, rx: 1.4 } }, "M12 4.5v11"], { size: 15, sw: 1.4 }),
  send: make(["M4 10h11", "M10 5l5 5-5 5"], { size: 16 }),
  brand: make(
    [
      "M8.7 18h6.6",
      "M9.6 18l.7-6.2h3.4l.7 6.2",
      "M10 8.2h4",
      "M12 3.5l2.2 2.4H9.8z",
      "M10.3 8.2v3.6M13.7 8.2v3.6",
      "M6.2 7.3 8.6 8",
      "M17.8 7.3 15.4 8",
    ],
    { size: 20, sw: 1.4 },
  ),
  user: make([{ c: true, cx: 10, cy: 7.5, r: 3 }, "M4.8 16.2a5.2 5.2 0 0 1 10.4 0"], { size: 17 }),
  check: make(["M5 10.5l3.2 3.4 6.8-7.4"], { size: 16, sw: 1.7 }),
};
