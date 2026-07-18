/**
 * CollectionTree — the 196px Zotero-style category rail of the 文库 view.
 *
 * Ported from the design prototype (Pharos.dc.html markup 77-94, styles from
 * renderVals() 589-598). Two deliberate real-data deviations, both documented
 * inline: the backend has no collections and no tags yet.
 */
import { Fragment, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Icons } from "../design/icons";
import { toVM } from "../lib/model";
import { useUI } from "../store";
import "./CollectionTree.css";

interface TreeRow {
  id: string;
  label: string;
  depth: number;
  icon: JSX.Element;
  /** Collapsible group row: renders a caret and toggles instead of selecting. */
  groupKey?: "favOpen" | "zoteroOpen";
  expanded?: boolean;
  count?: number;
  /** Zotero only: spinning sync glyph while the library syncs. */
  sync?: boolean;
}

export function CollectionTree(): JSX.Element {
  const selectedCol = useUI((s) => s.selectedCol);
  const selectCol = useUI((s) => s.selectCol);
  const favOpen = useUI((s) => s.favOpen);
  const toggleGroup = useUI((s) => s.toggleGroup);
  const selectedTags = useUI((s) => s.selectedTags);
  const toggleTag = useUI((s) => s.toggleTag);

  const papers = useQuery({ queryKey: ["papers"], queryFn: api.listPapers });

  // No collections exist server-side yet, so every paper counts as both
  // "我的文库" and "未分类".
  const total = papers.data?.length ?? 0;

  const allTags = useMemo(() => {
    const seen = new Set<string>();
    for (const p of papers.data ?? []) for (const t of toVM(p).tags) seen.add(t);
    return [...seen];
  }, [papers.data]);

  const rows: TreeRow[] = [
    { id: "lib", label: "我的文库", depth: 0, icon: <Icons.folder />, count: total },
    {
      id: "__fav",
      label: "收藏夹",
      depth: 0,
      icon: <Icons.star />,
      groupKey: "favOpen",
      expanded: favOpen,
    },
    { id: "uncat", label: "未分类", depth: 0, icon: <Icons.inbox />, count: total },
    { id: "trash", label: "回收站", depth: 0, icon: <Icons.trash />, count: 0 },
  ];

  // Where the Zotero group goes once the integration is connected: a group row
  // {id:"__zot", label:"Zotero", icon:<Icons.cloud/>, groupKey:"zoteroOpen",
  // expanded: zoteroOpen, sync: true} followed, when zoteroOpen, by its
  // collections at depth 1. The sync glyph is <Icons.sync/> inside
  // .ph-tree-sync (animation: ph-spin 1.1s linear infinite). Never rendered
  // yet — there is no Zotero connection.

  const renderRow = (r: TreeRow) => {
    const active = selectedCol === r.id;
    const onActivate = () => (r.groupKey ? toggleGroup(r.groupKey) : selectCol(r.id));
    return (
      <div
        key={r.id}
        className={active ? "ph-tree-row is-active" : "ph-tree-row"}
        style={{ paddingLeft: 8 + r.depth * 15 }}
        onClick={onActivate}
      >
        {r.groupKey !== undefined && (
          <span
            className="ph-tree-caret"
            onClick={(e) => {
              e.stopPropagation();
              if (r.groupKey) toggleGroup(r.groupKey);
            }}
          >
            {r.expanded ? <Icons.caretD /> : <Icons.caretR />}
          </span>
        )}
        <span className="ph-tree-icon">{r.icon}</span>
        <span className="ph-tree-text">{r.label}</span>
        {r.sync && (
          <span className="ph-tree-sync">
            <Icons.sync />
          </span>
        )}
        {r.count !== undefined && <span className="ph-tree-count">{r.count}</span>}
      </div>
    );
  };

  return (
    <aside className="ph-tree ph-scroll">
      <div className="ph-tree-head">分类</div>
      {rows.map((r) => (
        <Fragment key={r.id}>
          {renderRow(r)}
          {/* Real-data deviation: the prototype listed three mock collections
              under 收藏夹. There are no collections in the backend yet. */}
          {r.id === "__fav" && favOpen && (
            <div className="ph-tree-empty" style={{ paddingLeft: 8 + 1 * 15 }}>
              暂无分类
            </div>
          )}
        </Fragment>
      ))}

      {/* Real-data deviation: no tags exist yet, so the whole 标签 section
          (divider included) is omitted until one appears. */}
      {allTags.length > 0 && (
        <>
          <div className="ph-tree-div" />
          <div className="ph-tree-head">标签</div>
          <div className="ph-tree-tags">
            {allTags.map((t) => (
              <button
                key={t}
                className={selectedTags.includes(t) ? "ph-tree-tag is-on" : "ph-tree-tag"}
                onClick={() => toggleTag(t)}
              >
                {t}
              </button>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}
