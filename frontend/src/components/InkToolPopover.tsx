/**
 * The popover under a drawing tool — colour, thickness, and whatever else that
 * one tool needs.
 *
 * One component for 手写笔, 水彩笔 and 改样式, because they were three copies
 * of the same rows and had already drifted apart: the pen hid half its colours
 * behind a 更多颜色 toggle while the watercolour showed all five inline, and
 * each carried its own width tray. Drift like that is what "调色盘设计不对"
 * was pointing at.
 *
 * The shape now:
 *
 * - **A quick row** — 墨黑 pinned first, then the three colours this reader
 *   actually reaches for (recency-weighted; see `rankInkColors`). This is the
 *   part that changes with use, and it is deliberately small.
 * - **调色盘** — the WHOLE palette, in a fixed grid, every colour always in
 *   the same place. The previous version showed only the colours the quick row
 *   had NOT taken, so the palette's contents shuffled as habits changed and it
 *   never showed the full set anywhere. A palette you cannot learn the shape of
 *   is not a palette.
 * - **粗细** — one thickness control for every tool ("统一拉出来设定大小"),
 *   with the value remembered per tool by the store, so the pen stays thin and
 *   the wash stays broad without either one resetting the other.
 *
 * At most one tray is open at a time, and starting a stroke closes it — both
 * enforced outside this component (`store.inkTray`), since the gesture that
 * should close it happens on the canvas.
 */
import { Icons } from "../design/icons";
import { MAX_INK_WIDTH, MIN_INK_WIDTH } from "../store";

/** Thickness presets in the 粗细 tray — a handful of common tiers across the
 *  1-100 range, not a value for every integer. Geometric rather than linear:
 *  the difference between 1 and 2 is obvious, between 60 and 61 is not. */
export const INK_WIDTH_PRESETS = [1, 2, 4, 8, 16, 32, 64];

export interface InkSwatch {
  key: string;
  label: string;
}

/**
 * A dot previewing a stroke width, on a SQUARE-ROOT scale.
 *
 * Linear-with-a-ceiling was the obvious thing and it was wrong: clamping at
 * 20 made 16, 32 and 64 render as the same dot, so half the preset row looked
 * like duplicates of each other. Square root keeps every preset visibly
 * distinct across the whole 1-100 range while still fitting a 24px button —
 * which is what a size preview is for.
 */
const dotSize = (w: number, k: number): number => 3 + Math.sqrt(Math.max(0, w)) * k;

export function InkToolPopover({
  label,
  palette,
  quick,
  color,
  onColor,
  width,
  onWidth,
  tray,
  onTray,
  note,
  children,
}: {
  /** Accessible name for the popover — "手写工具", "水彩笔工具", … */
  label: string;
  /** Every colour this tool can use: the 调色盘 grid shows all of them. */
  palette: readonly InkSwatch[];
  /** The handful on the quick row. Already ordered; usually black + top 3. */
  quick: readonly InkSwatch[];
  color: string;
  onColor: (key: string) => void;
  width: number;
  onWidth: (w: number) => void;
  tray: "color" | "width" | null;
  onTray: (tray: "color" | "width") => void;
  /** A one-line hint shown on the main row, if the tool has something to say. */
  note?: string;
  /** Tool-specific controls appended to the main row (手指书写, …). */
  children?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="ph-rv-inkbar" role="toolbar" aria-label={label}>
      <div className="ph-rv-ink-row">
        {quick.map((c) => (
          <button
            key={c.key}
            className={`ph-rv-ink-color${color === c.key ? " is-on" : ""}`}
            style={{ background: `var(--c-ink-${c.key}, var(--c-tx))` }}
            title={c.label}
            aria-label={c.label}
            aria-pressed={color === c.key}
            onClick={() => onColor(c.key)}
          />
        ))}
        <button
          className={`ph-rv-ink-more-btn${tray === "color" ? " is-on" : ""}`}
          title="调色盘"
          aria-label="调色盘"
          aria-pressed={tray === "color"}
          onClick={() => onTray("color")}
        >
          <Icons.palette size={14} />
        </button>
        <span className="ph-rv-ink-sep" />
        <button
          className={`ph-rv-ink-more-btn${tray === "width" ? " is-on" : ""}`}
          title={`粗细 ${Math.round(width)}`}
          aria-label="笔画粗细"
          aria-pressed={tray === "width"}
          onClick={() => onTray("width")}
        >
          <span
            className="ph-rv-ink-width-dot"
            style={{ width: dotSize(width, 0.9), height: dotSize(width, 0.9) }}
          />
        </button>
        {children && <span className="ph-rv-ink-sep" />}
        {children}
        {note && <span className="ph-rv-ink-note">{note}</span>}
      </div>

      {tray === "color" && (
        <div className="ph-rv-ink-tray" role="group" aria-label="调色盘">
          <div className="ph-rv-ink-grid">
            {palette.map((c) => (
              <button
                key={c.key}
                className={`ph-rv-ink-chip${color === c.key ? " is-on" : ""}`}
                style={{ background: `var(--c-ink-${c.key}, var(--c-tx))` }}
                title={c.label}
                aria-label={c.label}
                aria-pressed={color === c.key}
                onClick={() => onColor(c.key)}
              />
            ))}
          </div>
        </div>
      )}

      {tray === "width" && (
        <div className="ph-rv-ink-tray" role="group" aria-label="笔画粗细">
          <div className="ph-rv-ink-row">
            <input
              type="range"
              className="ph-rv-ink-width-slider"
              min={MIN_INK_WIDTH}
              max={MAX_INK_WIDTH}
              step={1}
              value={Math.round(width)}
              aria-label="笔画粗细"
              onChange={(e) => onWidth(Number(e.target.value))}
            />
            <span className="ph-rv-ink-width-val">{Math.round(width)}</span>
          </div>
          <div className="ph-rv-ink-row">
            {INK_WIDTH_PRESETS.map((w) => (
              <button
                key={w}
                className={`ph-rv-ink-width${Math.round(width) === w ? " is-on" : ""}`}
                title={`粗细 ${w}`}
                aria-label={`粗细 ${w}`}
                aria-pressed={Math.round(width) === w}
                onClick={() => onWidth(w)}
              >
                <span style={{ width: dotSize(w, 1.9), height: dotSize(w, 1.9) }} />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
