import { Icons } from "../design/icons";
import { useUI } from "../store";
import type { OutlineMode } from "../store";
import "./OutlinePanel.css";

export interface OutlineEntry {
  title: string;
  /** 1-based page, or null when the destination could not be resolved. */
  page: number | null;
  depth: number;
}

export interface OutlinePanelProps {
  entries: OutlineEntry[];
  pageCount: number;
  /** 1-based current page. */
  currentPage: number;
  /** Data URLs indexed by page-1; null while that thumbnail is still rendering. */
  thumbs: (string | null)[];
  onJump: (page: number) => void;
}

const OUTLINE_TABS: { key: OutlineMode; label: string }[] = [
  { key: "outline", label: "大纲" },
  { key: "thumbs", label: "缩略图" },
];

/** Index of the deepest entry sitting on the current page, or -1. */
function activeEntryIndex(entries: OutlineEntry[], currentPage: number): number {
  let best = -1;
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e.page !== currentPage) continue;
    if (best === -1 || e.depth >= entries[best].depth) best = i;
  }
  return best;
}

export function OutlinePanel(props: OutlinePanelProps): JSX.Element {
  const { entries, pageCount, currentPage, thumbs, onJump } = props;
  const outlineMode = useUI((s) => s.outlineMode);
  const setOutlineMode = useUI((s) => s.setOutlineMode);
  const toggleOutline = useUI((s) => s.toggleOutline);

  const activeIdx = activeEntryIndex(entries, currentPage);

  return (
    <aside className="ph-outline">
      <div className="ph-outline-head">
        <div className="ph-outline-seg">
          {OUTLINE_TABS.map((t) => (
            <button
              key={t.key}
              className={
                "ph-outline-seg-btn" + (outlineMode === t.key ? " is-on" : "")
              }
              onClick={() => setOutlineMode(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="ph-outline-spacer" />
        <button className="ph-outline-collapse" title="折叠" onClick={toggleOutline}>
          <Icons.panelL />
        </button>
      </div>

      <div className="ph-scroll ph-outline-body">
        {outlineMode === "outline" ? (
          entries.length === 0 ? (
            <div className="ph-outline-empty">本文档没有大纲</div>
          ) : (
            entries.map((e, i) => (
              <div
                key={i}
                className={"ph-outline-row" + (i === activeIdx ? " is-on" : "")}
                style={{ paddingLeft: 8 + e.depth * 12 }}
                onClick={() => {
                  if (e.page !== null) onJump(e.page);
                }}
              >
                {e.title}
              </div>
            ))
          )
        ) : (
          <div className="ph-outline-thumbs">
            {Array.from({ length: pageCount }, (_, i) => {
              const n = i + 1;
              const src = thumbs[i] ?? null;
              return (
                <div
                  key={n}
                  className={
                    "ph-outline-thumb" + (n === currentPage ? " is-on" : "")
                  }
                  onClick={() => onJump(n)}
                >
                  {src !== null ? (
                    <img className="ph-outline-thumb-img" src={src} alt={`第 ${n} 页`} />
                  ) : (
                    <div className="ph-outline-skel">
                      <div className="ph-outline-skel-title" />
                      <div className="ph-outline-skel-line" />
                      <div className="ph-outline-skel-line is-short" />
                      <div className="ph-outline-skel-line" />
                    </div>
                  )}
                  <span className="ph-outline-thumb-n">{n}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
