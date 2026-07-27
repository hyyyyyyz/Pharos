import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Icons } from "../design/icons";
import {
  RAIL_DEFAULT_WIDTH,
  RAIL_MAX_WIDTH,
  RAIL_MIN_WIDTH,
  useUI,
  type ModuleKey,
} from "../store";
import "./Rail.css";

type IconComponent = (typeof Icons)["library"];

interface NavDef {
  key: ModuleKey;
  label: string;
  title: string;
  Icon: IconComponent;
  /** Not built yet: dimmed, with a 即将 pill (expanded) or a dot (collapsed). */
  comingSoon: boolean;
}

const NAV: NavDef[] = [
  { key: "library", label: "文库", title: "文库", Icon: Icons.library, comingSoon: false },
  { key: "daily", label: "每日论文", title: "每日论文", Icon: Icons.daily, comingSoon: false },
  { key: "search", label: "文献探索", title: "文献探索", Icon: Icons.search, comingSoon: false },
  { key: "kb", label: "研究项目", title: "研究项目", Icon: Icons.kb, comingSoon: false },
];

const cx = (...parts: (string | false)[]): string => parts.filter(Boolean).join(" ");

const SMALL_VIEWPORT = 760;
const SMALL_RAIL_MAX = 220;

function maxRailWidthForViewport(viewportWidth: number): number {
  if (viewportWidth >= SMALL_VIEWPORT) return RAIL_MAX_WIDTH;
  return Math.max(
    RAIL_MIN_WIDTH,
    Math.min(SMALL_RAIL_MAX, Math.floor(viewportWidth * 0.42)),
  );
}

interface RailDrag {
  pointerId: number;
  startX: number;
  startWidth: number;
  currentWidth: number;
}

export function Rail(): JSX.Element {
  const railExpanded = useUI((s) => s.railExpanded);
  const toggleRail = useUI((s) => s.toggleRail);
  const railWidth = useUI((s) => s.railWidth);
  const setRailWidth = useUI((s) => s.setRailWidth);
  const resetRailWidth = useUI((s) => s.resetRailWidth);
  const winW = useUI((s) => s.winW);
  const activeModule = useUI((s) => s.activeModule);
  const setModule = useUI((s) => s.setModule);
  const openSettings = useUI((s) => s.openSettings);

  const [resizing, setResizing] = useState(false);
  const [draftWidth, setDraftWidth] = useState<number | null>(null);
  const drag = useRef<RailDrag | null>(null);

  const exp = railExpanded;
  const resizeMax = maxRailWidthForViewport(winW);
  const visibleWidth = Math.min(draftWidth ?? railWidth, resizeMax);

  useEffect(() => {
    if (!resizing) return;
    document.documentElement.classList.add("ph-rail-resizing");
    return () => document.documentElement.classList.remove("ph-rail-resizing");
  }, [resizing]);

  useEffect(() => {
    if (exp) return;
    drag.current = null;
    setDraftWidth(null);
    setResizing(false);
  }, [exp]);

  const beginResize = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: visibleWidth,
      currentWidth: visibleWidth,
    };
    setResizing(true);
  };

  const moveResize = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const current = drag.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    const next = Math.min(
      resizeMax,
      Math.max(RAIL_MIN_WIDTH, current.startWidth + event.clientX - current.startX),
    );
    current.currentWidth = next;
    setDraftWidth(next);
  };

  const finishResize = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const current = drag.current;
    if (current === null || current.pointerId !== event.pointerId) return;
    drag.current = null;
    setRailWidth(current.currentWidth);
    setDraftWidth(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setResizing(false);
  };

  const resizeByKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    const step = event.shiftKey ? 24 : 8;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = visibleWidth - step;
    else if (event.key === "ArrowRight") next = visibleWidth + step;
    else if (event.key === "Home") next = RAIL_MIN_WIDTH;
    else if (event.key === "End") next = resizeMax;
    if (next === null) return;
    event.preventDefault();
    event.stopPropagation();
    setRailWidth(Math.min(resizeMax, Math.max(RAIL_MIN_WIDTH, next)));
  };

  return (
    <nav
      className={cx(
        "ph-rail",
        exp ? "ph-rail--exp" : "ph-rail--col",
        resizing && "ph-rail--resizing",
      )}
      style={exp ? { width: visibleWidth } : undefined}
    >
      <div className={cx("ph-rail-brand", exp ? "ph-rail-brand--exp" : "ph-rail-brand--col")}>
        {exp ? (
          <>
            <span className="ph-rail-brand-mark" aria-hidden>
              <Icons.brand />
            </span>
            <span className="ph-rail-wordmark">Pharos</span>
            <button
              type="button"
              onClick={toggleRail}
              title="收起侧栏"
              aria-label="收起侧栏"
              aria-expanded="true"
              className="ph-rail-toggle ph-rail-toggle--exp"
            >
              <Icons.panelL />
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={toggleRail}
            title="展开侧栏"
            aria-label="展开侧栏"
            aria-expanded="false"
            className="ph-rail-toggle ph-rail-toggle--col"
          >
            <Icons.panelR />
          </button>
        )}
      </div>

      <div className={cx("ph-rail-nav", exp ? "ph-rail-nav--exp" : "ph-rail-nav--col")}>
        {NAV.map(({ key, label, title, Icon, comingSoon }) => {
          const active = activeModule === key;
          return (
            <button
              key={key}
              type="button"
              title={title}
              onClick={() => setModule(key)}
              className={cx(
                "ph-rail-item",
                exp ? "ph-rail-item--exp" : "ph-rail-item--col",
                active && "ph-rail-item--active",
                comingSoon && !active && "ph-rail-item--cs",
              )}
            >
              <span className="ph-rail-item-ic">
                <Icon />
              </span>
              {exp && <span className="ph-rail-item-label">{label}</span>}
              {comingSoon && exp && <span className="ph-rail-item-pill">即将</span>}
              {comingSoon && !exp && <span className="ph-rail-item-dot" />}
              {active && !exp && <span className="ph-rail-item-bar" />}
            </button>
          );
        })}
      </div>

      <div className="ph-rail-spacer" />

      {exp ? (
        <button
          type="button"
          title="账户与设置"
          onClick={() => openSettings("account")}
          className="ph-rail-acct"
        >
          <span className="ph-rail-acct-avatar">
            <Icons.user />
          </span>
          <span className="ph-rail-acct-text">
            <span className="ph-rail-acct-name">科研用户</span>
            <span className="ph-rail-acct-sub">设置与账户</span>
          </span>
        </button>
      ) : (
        <button
          type="button"
          title="账户与设置"
          onClick={() => openSettings("account")}
          className="ph-rail-acct-mini"
        >
          <Icons.user />
        </button>
      )}

      {exp && (
        <div
          className="ph-rail-resize-handle"
          role="separator"
          aria-label="调整侧栏宽度"
          aria-orientation="vertical"
          aria-valuemin={RAIL_MIN_WIDTH}
          aria-valuemax={resizeMax}
          aria-valuenow={visibleWidth}
          aria-valuetext={`${visibleWidth} 像素`}
          tabIndex={0}
          title={`拖动调整宽度；双击恢复 ${RAIL_DEFAULT_WIDTH}px`}
          onPointerDown={beginResize}
          onPointerMove={moveResize}
          onPointerUp={finishResize}
          onPointerCancel={finishResize}
          onLostPointerCapture={finishResize}
          onKeyDown={resizeByKeyboard}
          onDoubleClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setDraftWidth(null);
            resetRailWidth();
          }}
        >
          <span aria-hidden />
        </div>
      )}
    </nav>
  );
}
