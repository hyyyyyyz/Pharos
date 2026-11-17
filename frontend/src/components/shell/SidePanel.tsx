import {
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

export const SIDE_DEFAULT = 264;
const SIDE_MIN = 208;
const SIDE_MAX = 400;

const clamp = (w: number): number => Math.min(SIDE_MAX, Math.max(SIDE_MIN, Math.round(w)));

function readWidth(key: string): number {
  try {
    const raw = localStorage.getItem(`ph-side-${key}`);
    const n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) ? clamp(n) : SIDE_DEFAULT;
  } catch {
    return SIDE_DEFAULT;
  }
}

interface SidePanelProps {
  /** Width is remembered per module under this key. */
  storageKey: string;
  title: ReactNode;
  /** Icon buttons on the title row's right edge. */
  actions?: ReactNode;
  children: ReactNode;
}

/**
 * The in-module list column: a quiet title row, then the list. 264 wide and
 * draggable (arrow keys too), so the one place a module needs more room — long
 * folder or project names — can take it without every module widening.
 */
export function SidePanel({ storageKey, title, actions, children }: SidePanelProps): JSX.Element {
  const [width, setWidth] = useState(() => readWidth(storageKey));
  const drag = useRef<{ id: number; x0: number; w0: number; w: number } | null>(null);

  const commit = (w: number): void => {
    setWidth(w);
    try {
      localStorage.setItem(`ph-side-${storageKey}`, String(w));
    } catch {
      /* private mode */
    }
  };

  const onDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { id: e.pointerId, x0: e.clientX, w0: width, w: width };
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = drag.current;
    if (d === null || d.id !== e.pointerId) return;
    d.w = clamp(d.w0 + e.clientX - d.x0);
    setWidth(d.w);
  };
  const onUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = drag.current;
    if (d === null || d.id !== e.pointerId) return;
    drag.current = null;
    commit(d.w);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    const step = e.shiftKey ? 32 : 8;
    if (e.key === "ArrowLeft") commit(clamp(width - step));
    else if (e.key === "ArrowRight") commit(clamp(width + step));
    else return;
    e.preventDefault();
  };

  return (
    <aside className="ph-sp" style={{ width }}>
      <div className="ph-sp-head">
        <span className="ph-sp-title">{title}</span>
        {actions !== undefined && <span className="ph-sp-actions">{actions}</span>}
      </div>
      <div className="ph-sp-body ph-scroll">{children}</div>
      <div
        className="ph-sp-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="调整面板宽度"
        aria-valuemin={SIDE_MIN}
        aria-valuemax={SIDE_MAX}
        aria-valuenow={width}
        tabIndex={0}
        title="拖动调整宽度；双击恢复默认"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onKeyDown={onKey}
        onDoubleClick={() => commit(SIDE_DEFAULT)}
      />
    </aside>
  );
}
