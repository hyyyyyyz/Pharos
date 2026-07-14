import { useState } from "react";
import { useUI, type AccentKey } from "../store";
import "./Settings.css";

const ACCENTS: { key: AccentKey; name: string; color: string }[] = [
  { key: "mint", name: "薄荷", color: "#12b5a6" },
  { key: "sky", name: "天蓝", color: "#2b9bf0" },
  { key: "emerald", name: "松绿", color: "#22b07a" },
  { key: "indigo", name: "靛蓝", color: "#6366f1" },
  { key: "violet", name: "丁香", color: "#8b5cf6" },
  { key: "rose", name: "珊瑚", color: "#f2607d" },
  { key: "amber", name: "琥珀", color: "#eaa64b" },
  { key: "slate", name: "石青", color: "#5b7291" },
];

export function Settings() {
  const [open, setOpen] = useState(false);
  const mode = useUI((s) => s.mode);
  const setMode = useUI((s) => s.setMode);
  const accent = useUI((s) => s.accent);
  const setAccent = useUI((s) => s.setAccent);

  return (
    <div className="settings">
      <button
        className="icon-btn"
        onClick={() => setOpen((o) => !o)}
        title="外观设置"
        aria-label="外观设置"
      >
        ⚙
      </button>
      {open && (
        <>
          <div className="settings-scrim" onClick={() => setOpen(false)} />
          <div className="settings-pop" role="dialog" aria-label="外观设置">
            <div className="settings-row">
              <span className="settings-label">主题</span>
              <div className="seg small">
                <button
                  className={`seg-btn${mode === "light" ? " is-on" : ""}`}
                  onClick={() => setMode("light")}
                >
                  浅色
                </button>
                <button
                  className={`seg-btn${mode === "dark" ? " is-on" : ""}`}
                  onClick={() => setMode("dark")}
                >
                  深色
                </button>
              </div>
            </div>
            <div className="settings-row col">
              <span className="settings-label">强调色</span>
              <div className="swatches">
                {ACCENTS.map((a) => (
                  <button
                    key={a.key}
                    className={`swatch${accent === a.key ? " is-on" : ""}`}
                    style={{ background: a.color }}
                    onClick={() => setAccent(a.key)}
                    title={a.name}
                    aria-label={a.name}
                  />
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
