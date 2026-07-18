import { useEffect } from "react";
import { Icons } from "../design/icons";
import { ACCENTS, accentSwatch } from "../design/tokens";
import type { ThemeMode } from "../design/tokens";
import { useUI, type SettingsTab } from "../store";
import "./SettingsModal.css";

type IconComponent = (typeof Icons)["user"];

const TABS: { key: SettingsTab; label: string; Icon: IconComponent }[] = [
  { key: "account", label: "账户", Icon: Icons.user },
  { key: "appearance", label: "外观", Icon: Icons.palette },
];

const THEMES: { key: ThemeMode; label: string; Icon: IconComponent }[] = [
  { key: "light", label: "浅色", Icon: Icons.sun },
  { key: "dark", label: "深色", Icon: Icons.moon },
];

type ZoteroState = "off" | "connecting" | "connected";

/**
 * Wiring point for the Zotero integration, which is not implemented yet.
 * The connecting/connected branches below are ported and ready; when the
 * backend lands, drive this from real connection state instead of the
 * constant and delete the disabled/note treatment in the "off" branch.
 * (`as ZoteroState` keeps the union type so the other branches type-check.)
 */
const zotero = "off" as ZoteroState;

const cx = (...parts: (string | false)[]): string => parts.filter(Boolean).join(" ");

export function SettingsModal(): JSX.Element | null {
  const settingsOpen = useUI((s) => s.settingsOpen);
  const settingsTab = useUI((s) => s.settingsTab);
  const setSettingsTab = useUI((s) => s.setSettingsTab);
  const closeSettings = useUI((s) => s.closeSettings);
  const theme = useUI((s) => s.theme);
  const setTheme = useUI((s) => s.setTheme);
  const accent = useUI((s) => s.accent);
  const setAccent = useUI((s) => s.setAccent);

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSettings();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, closeSettings]);

  if (!settingsOpen) return null;

  return (
    <>
      <div className="ph-set-overlay" onClick={closeSettings} />
      <div className="ph-set-dialog" role="dialog" aria-modal="true" aria-label="设置">
        <div className="ph-set-nav">
          <div className="ph-set-nav-title">设置</div>
          <div className="ph-set-nav-list">
            {TABS.map(({ key, label, Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => setSettingsTab(key)}
                className={cx("ph-set-tab", settingsTab === key && "ph-set-tab--on")}
              >
                <span className="ph-set-ic">
                  <Icon />
                </span>
                {label}
              </button>
            ))}
          </div>
          <div className="ph-set-nav-spacer" />
          <div className="ph-set-nav-foot">
            Pharos v0.1
            <br />
            开源 · AGPL-3.0
          </div>
        </div>

        <div className="ph-scroll ph-set-pane">
          <button type="button" onClick={closeSettings} title="关闭" className="ph-set-close">
            <Icons.close />
          </button>

          {settingsTab === "account" && (
            <>
              <div className="ph-set-h">账户</div>
              <div className="ph-set-acct">
                <span className="ph-set-avatar">
                  <Icons.user size={22} sw={1.4} />
                </span>
                <div>
                  <div className="ph-set-acct-name">科研用户</div>
                  <div className="ph-set-acct-sub">本地账户 · 未登录云端</div>
                </div>
              </div>
              <div className="ph-set-zot">
                <div className="ph-set-zot-head">
                  <span className="ph-set-ic ph-set-ic--tx2">
                    <Icons.cloud />
                  </span>
                  <div className="ph-set-zot-title">Zotero 文献库</div>
                </div>

                {zotero === "off" && (
                  <>
                    <div className="ph-set-zot-desc">
                      登录并连接你的 Zotero 账户，本地文献库的分类与论文会同步进文库，翻译状态双向保留。
                    </div>
                    <input
                      className="ph-set-input"
                      placeholder="Zotero 用户 ID 或 API 密钥（自托管服务器可选）"
                    />
                    <button type="button" className="ph-set-connect" disabled>
                      <span className="ph-set-ic">
                        <Icons.link />
                      </span>
                      登录并连接 Zotero
                    </button>
                    <div className="ph-set-zot-note">
                      Zotero 互通开发中 · 文库将直接同步你的本地 Zotero
                    </div>
                  </>
                )}

                {zotero === "connecting" && (
                  <div className="ph-set-zot-connecting">
                    <span className="ph-set-ic ph-set-ic--spin">
                      <Icons.sync />
                    </span>
                    正在连接并同步…
                  </div>
                )}

                {zotero === "connected" && (
                  <div className="ph-set-zot-card">
                    <span className="ph-set-zot-check">
                      <Icons.check />
                    </span>
                    <div className="ph-set-zot-card-text">
                      <div className="ph-set-zot-card-title">已连接 · 同步完成</div>
                      {/* Real account/collection counts come from the backend once
                          Zotero sync exists; never fabricate them. */}
                      <div className="ph-set-zot-card-sub">—</div>
                    </div>
                    <button type="button" className="ph-set-zot-disconnect">
                      断开
                    </button>
                  </div>
                )}
              </div>
            </>
          )}

          {settingsTab === "appearance" && (
            <>
              <div className="ph-set-h">外观</div>
              <div className="ph-set-label">主题</div>
              <div className="ph-set-themes">
                {THEMES.map(({ key, label, Icon }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTheme(key)}
                    className={cx("ph-set-theme", theme === key && "ph-set-theme--on")}
                  >
                    <span className="ph-set-ic">
                      <Icon />
                    </span>
                    {label}
                  </button>
                ))}
              </div>
              <div className="ph-set-label">
                强调色 <span className="ph-set-label-sub">· 仅用于关键处</span>
              </div>
              <div className="ph-set-accents">
                {ACCENTS.map(({ key, name }) => {
                  const on = accent === key;
                  const colour = accentSwatch(key, theme);
                  return (
                    <button
                      key={key}
                      type="button"
                      title={name}
                      onClick={() => setAccent(key)}
                      className={cx("ph-set-acc", on && "ph-set-acc--on")}
                    >
                      <span
                        className="ph-set-acc-dot"
                        style={{
                          background: colour,
                          boxShadow: on
                            ? `0 0 0 2px var(--c-sf),0 0 0 3.5px ${colour}`
                            : "inset 0 0 0 1px rgba(0,0,0,.06)",
                        }}
                      />
                      <span className="ph-set-acc-name">{name}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
