import { useState } from "react";
import { Icons } from "../design/icons";
import { useUI, type ModuleKey } from "../store";
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

export function Rail(): JSX.Element {
  const railExpanded = useUI((s) => s.railExpanded);
  const toggleRail = useUI((s) => s.toggleRail);
  const activeModule = useUI((s) => s.activeModule);
  const setModule = useUI((s) => s.setModule);
  const openSettings = useUI((s) => s.openSettings);

  // The brand button swaps its glyph on hover, so hover has to be observable
  // in JS — CSS alone cannot change which icon is rendered.
  const [brandHover, setBrandHover] = useState(false);

  const exp = railExpanded;

  return (
    <nav className={cx("ph-rail", exp ? "ph-rail--exp" : "ph-rail--col")}>
      <div className={cx("ph-rail-brand", exp ? "ph-rail-brand--exp" : "ph-rail-brand--col")}>
        <button
          type="button"
          onClick={toggleRail}
          onMouseEnter={() => setBrandHover(true)}
          onMouseLeave={() => setBrandHover(false)}
          title={exp ? "收起侧栏" : "展开侧栏"}
          className={cx("ph-rail-brand-btn", exp ? "ph-rail-brand-btn--exp" : "ph-rail-brand-btn--col")}
        >
          {brandHover ? <Icons.panelL /> : <Icons.brand />}
        </button>
        {exp && <span className="ph-rail-wordmark">Pharos</span>}
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
    </nav>
  );
}
