import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import "./shell.css";

/**
 * The frame every module sits in: [SidePanel] | main column | [detail].
 *
 *   ┌───────────┬────────────────────────────┬──────────────┐
 *   │ SidePanel │ PageHeader (48)            │              │
 *   │ 264       ├────────────────────────────┤ detail 360   │
 *   │           │ content (the module's own) │              │
 *   └───────────┴────────────────────────────┴──────────────┘
 *
 * The shell owns only geometry. Each module still owns its data and renders its
 * own `PageHeader` at the top of its content, so nothing about how a module
 * fetches or mutates changed when it moved in here — what changed is that the
 * header height, the side width and the detail width stopped being decided
 * eight different times.
 *
 * `detail` is shown in place on a wide screen and as a slide-over when
 * `detailOverlay` says so (touch, narrow). The collapse toggle lives in the
 * PageHeader, which finds out about it through context.
 */

interface ShellState {
  /** A detail column exists and can be collapsed from the header. */
  collapsible: boolean;
  collapsed: boolean;
  toggleDetail: () => void;
}

const ShellContext = createContext<ShellState>({
  collapsible: false,
  collapsed: false,
  toggleDetail: () => {},
});

export const useShell = (): ShellState => useContext(ShellContext);

const read = (key: string): boolean => {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
};

/** Below this window width the detail column no longer fits beside the list. */
export const DETAIL_NARROW_MAX = 1200;

const isNarrow = (): boolean => typeof window !== "undefined" && window.innerWidth < DETAIL_NARROW_MAX;

interface ModuleShellProps {
  /** A `SidePanel`. Omit for a module with no in-module list. */
  side?: ReactNode;
  /** The right-hand column. Omit when the module has none. */
  detail?: ReactNode;
  /** Persist the collapsed state of `detail` under this key. */
  detailKey?: string;
  /** Show `detail` as a slide-over instead of a column. */
  detailOverlay?: boolean;
  /** Whether the slide-over is currently open (overlay mode only). */
  detailOpen?: boolean;
  onDetailClose?: () => void;
  children: ReactNode;
}

export function ModuleShell({
  side,
  detail,
  detailKey,
  detailOverlay = false,
  detailOpen = false,
  onDetailClose,
  children,
}: ModuleShellProps): JSX.Element {
  const [collapsed, setCollapsed] = useState(() => (detailKey ? read(`ph-detail-${detailKey}`) : false));

  // A desktop window narrower than the three columns need: the detail becomes a
  // slide-over too, closed until asked for, instead of starving the list.
  const [narrow, setNarrow] = useState(isNarrow);
  const [narrowOpen, setNarrowOpen] = useState(false);
  useEffect(() => {
    const on = (): void => setNarrow(isNarrow());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  const slideOver = detailOverlay || narrow;
  const open = detailOverlay ? detailOpen : narrowOpen;
  const close = detailOverlay ? onDetailClose : () => setNarrowOpen(false);

  const toggleDetail = useCallback(() => {
    if (slideOver) {
      setNarrowOpen((o) => !o);
      return;
    }
    setCollapsed((c) => {
      const next = !c;
      if (detailKey) {
        try {
          localStorage.setItem(`ph-detail-${detailKey}`, next ? "1" : "0");
        } catch {
          /* private mode — the panel just reopens next time */
        }
      }
      return next;
    });
  }, [detailKey, slideOver]);

  const ctx = useMemo<ShellState>(
    () => ({
      // On a touch device the parent opens the slide-over from a row tap, so
      // the header toggle is only for the in-place and narrow-desktop cases.
      collapsible: detail !== undefined && !detailOverlay,
      collapsed: slideOver ? !narrowOpen : collapsed,
      toggleDetail,
    }),
    [detail, detailOverlay, slideOver, narrowOpen, collapsed, toggleDetail],
  );

  const showDetail = detail !== undefined && (slideOver ? open : !collapsed);

  return (
    <ShellContext.Provider value={ctx}>
      <div className="ph-ms">
        {side}
        <div className="ph-ms-main">{children}</div>
        {showDetail && slideOver && <div className="ph-ms-backdrop" onClick={close} />}
        {showDetail && (
          <aside className={slideOver ? "ph-ms-detail is-overlay" : "ph-ms-detail"}>{detail}</aside>
        )}
      </div>
    </ShellContext.Provider>
  );
}
