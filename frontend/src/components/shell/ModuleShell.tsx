import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
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
  const toggleDetail = useCallback(() => {
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
  }, [detailKey]);

  const ctx = useMemo<ShellState>(
    () => ({ collapsible: detail !== undefined && !detailOverlay, collapsed, toggleDetail }),
    [detail, detailOverlay, collapsed, toggleDetail],
  );

  const showDetail = detail !== undefined && (detailOverlay ? detailOpen : !collapsed);

  return (
    <ShellContext.Provider value={ctx}>
      <div className="ph-ms">
        {side}
        <div className="ph-ms-main">{children}</div>
        {showDetail && detailOverlay && <div className="ph-ms-backdrop" onClick={onDetailClose} />}
        {showDetail && (
          <aside className={detailOverlay ? "ph-ms-detail is-overlay" : "ph-ms-detail"}>{detail}</aside>
        )}
      </div>
    </ShellContext.Provider>
  );
}
