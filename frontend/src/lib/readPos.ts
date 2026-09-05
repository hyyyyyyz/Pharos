/**
 * Reading position — the per-paper, per-rendition "where was I" record.
 *
 * Stored as *fractions of the scrolled content*, not pixels, so a position
 * survives zoom changes (content height scales, fractions do not) and
 * window resizes without any conversion table. The consumer (`PdfCanvas`)
 * re-applies them against the live scroll box on restore and on back.
 *
 * The record also carries the zoom to reopen at, but only when the reader
 * was deliberately zoomed — fit-width is recomputed from the window every
 * load, so persisting it would fight the ResizeObserver.
 */

export interface ReadPos {
  /** scrollTop as a fraction of scrollHeight, 0..1. */
  fy: number;
  /** scrollLeft as a fraction of scrollWidth. */
  fx: number;
  /** The zoom to come back at, or null when the reader was in fit mode. */
  zoom: number | null;
}

const KEY = "ph-read-pos-v1";
const MAX_ENTRIES = 200;

interface Store {
  [posKey: string]: ReadPos;
}

function readAll(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return {};
    return parsed as Store;
  } catch {
    return {}; // private mode or a corrupted write — a missing position is fine
  }
}

export function loadReadPos(posKey: string): ReadPos | null {
  const pos = readAll()[posKey];
  if (!pos) return null;
  const fy = Number(pos.fy);
  const fx = Number(pos.fx);
  if (!Number.isFinite(fy) || !Number.isFinite(fx)) return null;
  return {
    fy: Math.min(1, Math.max(0, fy)),
    fx: Math.min(1, Math.max(0, fx)),
    zoom: Number.isFinite(Number(pos.zoom)) && pos.zoom !== null ? Number(pos.zoom) : null,
  };
}

export function saveReadPos(posKey: string, pos: ReadPos): void {
  try {
    const all = readAll();
    all[posKey] = pos;
    // Long-lived browsers accumulate papers; the oldest entries fall off.
    const keys = Object.keys(all);
    if (keys.length > MAX_ENTRIES) {
      for (const k of keys.slice(0, keys.length - MAX_ENTRIES)) delete all[k];
    }
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* private mode — the position just won't persist */
  }
}

/**
 * Turn fractions into a concrete scroll target, clamped to what the
 * viewport can actually scroll (a stale entry from a much larger zoom must
 * not scroll past the end — the browser clamps, but keep the arithmetic
 * honest here so the caller sees the real numbers).
 */
export function scrollTarget(
  pos: ReadPos,
  viewport: { scrollWidth: number; scrollHeight: number; clientWidth: number; clientHeight: number },
): { top: number; left: number } {
  const maxTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
  const maxLeft = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
  return {
    top: Math.round(Math.min(maxTop, Math.max(0, pos.fy * maxTop))),
    left: Math.round(Math.min(maxLeft, Math.max(0, pos.fx * maxLeft))),
  };
}

/** Fractions of the current scroll state — the exact inverse of `scrollTarget`. */
export function fractionsOf(
  viewport: { scrollLeft: number; scrollTop: number; scrollWidth: number; scrollHeight: number; clientWidth: number; clientHeight: number },
): { fy: number; fx: number } {
  const maxTop = Math.max(1, viewport.scrollHeight - viewport.clientHeight);
  const maxLeft = Math.max(1, viewport.scrollWidth - viewport.clientWidth);
  return {
    fy: Math.min(1, Math.max(0, viewport.scrollTop / maxTop)),
    fx: Math.min(1, Math.max(0, viewport.scrollLeft / maxLeft)),
  };
}
