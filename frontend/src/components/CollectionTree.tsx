/**
 * CollectionTree — the 196px Zotero-style category rail of the 文库 view.
 *
 * Ported from the design prototype (Pharos.dc.html markup 77-94, styles from
 * renderVals() 589-598). The folders, the tags and every count are now real:
 * `GET /api/collections` returns the nested tree plus the 我的文库 / 未分类
 * badges, `GET /api/tags` the tag list, and `GET /api/papers?trash=true` the
 * recycle bin.
 *
 * The prototype's muted "暂无分类" line survives as the genuine empty state for
 * a user who has not made a folder yet.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type { CollectionNode } from "../api/types";
import { Icons } from "../design/icons";
import {
  zotero,
  zoteroAvailable,
  zoteroCollectionNodeId,
  zoteroLibraryNodeId,
  zoteroSavedSearchNodeId,
} from "../lib/zotero";
import type {
  ZoteroCollection,
  ZoteroLibrary,
  ZoteroSavedSearch,
} from "../types/zotero";
import { useUI } from "../store";
import "./CollectionTree.css";

/** MIME type for a paper drag. A custom type (rather than "text/plain") means
 *  the tree can tell a paper drag from a file drag or a text selection and only
 *  light up as a drop target for the one it can actually handle. */
export const PAPER_DRAG_MIME = "application/x-pharos-paper-ids";

/** Nesting the backend accepts is capped at 16; the rail is 196px wide and runs
 *  out of room long before that, so the "新建子分类" action stops offering to go
 *  deeper rather than letting the user build a level that renders as a sliver. */
const MAX_VISUAL_DEPTH = 5;

/** Which row, if any, is being edited inline, and what kind of edit it is.
 *  `parentId` is only meaningful while creating. */
type Editing =
  | { mode: "create"; parentId: string | null }
  | { mode: "rename"; id: string; initial: string }
  | null;

/** The open context menu: which folder, and where to put the popup. */
interface MenuState {
  id: string;
  name: string;
  depth: number;
  x: number;
  y: number;
  /** Second stage of the delete: the menu asks before it destroys a folder. */
  confirmingDelete: boolean;
}

export function CollectionTree(): JSX.Element {
  const qc = useQueryClient();

  const selectedCol = useUI((s) => s.selectedCol);
  const selectCol = useUI((s) => s.selectCol);
  const favOpen = useUI((s) => s.favOpen);
  const zoteroOpen = useUI((s) => s.zoteroOpen);
  const toggleGroup = useUI((s) => s.toggleGroup);

  const [editing, setEditing] = useState<Editing>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Folders the user has collapsed. Absent = expanded, so a folder created
   *  under a parent the user never touched shows up without a click. */
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const inputRef = useRef<HTMLInputElement>(null);
  /** Set the moment an edit is committed or abandoned, so the blur that follows
   *  an Enter (or an Escape) cannot commit the same name a second time and
   *  create two folders. Reset when a fresh editor opens. */
  const submitGuard = useRef(false);

  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections.list });
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags.list });
  const trash = useQuery({ queryKey: ["papers", "trash"], queryFn: api.listTrash });
  const localStatus = useQuery({
    queryKey: ["zotero-desktop", "status"],
    queryFn: zotero.status,
    enabled: zoteroAvailable(),
    staleTime: 5_000,
  });
  const zoteroLibraries = useQuery({
    queryKey: ["zotero-mirror", "libraries"],
    queryFn: zotero.libraries,
    enabled: zoteroAvailable(),
  });
  const zoteroCollectionQueries = useQueries({
    queries: (zoteroLibraries.data ?? []).map((library) => ({
      queryKey: ["zotero-mirror", "collections", library.sourceId, library.libraryId],
      queryFn: () => zotero.collections(library),
      enabled: zoteroAvailable(),
    })),
  });
  const zoteroSearchQueries = useQueries({
    queries: (zoteroLibraries.data ?? []).map((library) => ({
      queryKey: ["zotero-mirror", "saved-searches", library.sourceId, library.libraryId],
      queryFn: () => zotero.savedSearches(library),
      enabled: zoteroAvailable(),
    })),
  });
  const zoteroCountQueries = useQueries({
    queries: (zoteroLibraries.data ?? []).map((library) => ({
      queryKey: ["zotero-mirror", "items", library.sourceId, library.libraryId, "count"],
      queryFn: () => zotero.queryItems({ library, limit: 1 }),
      enabled: zoteroAvailable(),
    })),
  });

  const tree = useMemo(() => collections.data?.collections ?? [], [collections.data]);

  /** Refetch the sidebar counts and the two list views a folder edit can move
   *  rows between. Deliberately explicit rather than a blanket cache reset. */
  const refreshAfterFiling = () => {
    void qc.invalidateQueries({ queryKey: ["collections"] });
    void qc.invalidateQueries({ queryKey: ["papers"] });
  };

  const onMutationError = (e: unknown) => {
    setError(e instanceof ApiError ? e.message : "操作失败");
  };

  const create = useMutation({
    mutationFn: (v: { name: string; parentId: string | null }) =>
      api.collections.create({ name: v.name, parent_id: v.parentId }),
    onSuccess: (col) => {
      setError(null);
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["collections"] });
      selectCol(col.id);
    },
    onError: onMutationError,
  });

  const rename = useMutation({
    mutationFn: (v: { id: string; name: string }) =>
      api.collections.update(v.id, { name: v.name }),
    onSuccess: () => {
      setError(null);
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["collections"] });
    },
    onError: onMutationError,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.collections.remove(id),
    onSuccess: (res) => {
      setError(null);
      setMenu(null);
      void qc.invalidateQueries({ queryKey: ["collections"] });
      // Papers are not deleted with the folder, but they stop being filed, so
      // 未分类 and the folder view both change.
      void qc.invalidateQueries({ queryKey: ["papers"] });
      // The selection cannot be left pointing at a folder that no longer
      // exists — the item list would query a 404 forever. Reset it here, on the
      // event, rather than from an effect watching the tree.
      if (selectedCol === res.id) selectCol("lib");
      if (res.promoted_children > 0) {
        setError(`已删除，${res.promoted_children} 个子分类已上移一层`);
      }
    },
    onError: onMutationError,
  });

  const file = useMutation({
    mutationFn: (v: { id: string; paperIds: string[] }) =>
      api.collections.addPapers(v.id, v.paperIds),
    onSuccess: (res, v) => {
      setError(null);
      refreshAfterFiling();
      void qc.invalidateQueries({ queryKey: ["collection", v.id, "papers"] });
      if (res.added === 0) setError("已在该分类中");
    },
    onError: onMutationError,
  });

  // Focus the inline editor the moment it appears. This is a DOM side effect on
  // a state transition the user just caused — it touches no query cache, so it
  // cannot start a refetch loop.
  useEffect(() => {
    if (editing === null) return;
    submitGuard.current = false;
    inputRef.current?.focus();
  }, [editing]);

  // Dismiss the context menu on an outside click or Escape.
  useEffect(() => {
    if (menu === null) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const submitEdit = (raw: string) => {
    if (editing === null || submitGuard.current) return;
    submitGuard.current = true;
    const name = raw.trim();
    if (name === "") {
      setEditing(null);
      return;
    }
    if (editing.mode === "create") create.mutate({ name, parentId: editing.parentId });
    else if (name !== editing.initial) rename.mutate({ id: editing.id, name });
    else setEditing(null);
  };

  /** Read the dragged paper ids, or null when this drag is not ours (a PDF
   *  being dropped for upload, a text selection, anything else). */
  const draggedPaperIds = (dt: DataTransfer): string[] | null => {
    if (!dt.types.includes(PAPER_DRAG_MIME)) return null;
    // During dragover the payload is not readable, only the type list — which
    // is exactly why the type is custom: it is enough to decide on its own.
    const raw = dt.getData(PAPER_DRAG_MIME);
    if (raw === "") return [];
    try {
      const ids: unknown = JSON.parse(raw);
      return Array.isArray(ids) ? ids.filter((i): i is string => typeof i === "string") : null;
    } catch {
      return null;
    }
  };

  const inlineEditor = (depth: number) => (
    <div className="ph-tree-edit" style={{ paddingLeft: 8 + depth * 15 }}>
      <input
        ref={inputRef}
        className="ph-tree-input"
        defaultValue={editing?.mode === "rename" ? editing.initial : ""}
        maxLength={120}
        placeholder="分类名称"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Enter") submitEdit(e.currentTarget.value);
          else if (e.key === "Escape") {
            // Guard first: abandoning must also disarm the blur that follows.
            submitGuard.current = true;
            setEditing(null);
          }
        }}
        // Committing on blur rather than discarding: the common accident is
        // clicking away after typing a name, and losing it is the worse outcome.
        onBlur={(e) => submitEdit(e.currentTarget.value)}
      />
    </div>
  );

  const renderFolder = (node: CollectionNode, depth: number) => {
    if (editing?.mode === "rename" && editing.id === node.id) {
      return <Fragment key={node.id}>{inlineEditor(depth)}</Fragment>;
    }
    const active = selectedCol === node.id;
    const hasChildren = node.children.length > 0;
    const isCollapsed = collapsed.has(node.id);
    return (
      <div
        key={node.id}
        className={
          "ph-tree-row" +
          (active ? " is-active" : "") +
          (dropTarget === node.id ? " is-drop" : "")
        }
        style={{ paddingLeft: 8 + depth * 15 }}
        onClick={() => selectCol(node.id)}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenu({
            id: node.id,
            name: node.name,
            depth,
            x: e.clientX,
            y: e.clientY,
            confirmingDelete: false,
          });
        }}
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes(PAPER_DRAG_MIME)) return;
          // preventDefault is what marks this element as a valid drop target.
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          setDropTarget(node.id);
        }}
        onDragLeave={() => setDropTarget((t) => (t === node.id ? null : t))}
        onDrop={(e) => {
          const ids = draggedPaperIds(e.dataTransfer);
          setDropTarget(null);
          if (ids === null || ids.length === 0) return;
          e.preventDefault();
          file.mutate({ id: node.id, paperIds: ids });
        }}
      >
        <span
          className="ph-tree-caret"
          onClick={(e) => {
            e.stopPropagation();
            if (!hasChildren) return;
            setCollapsed((prev) => {
              const next = new Set(prev);
              if (next.has(node.id)) next.delete(node.id);
              else next.add(node.id);
              return next;
            });
          }}
        >
          {hasChildren && (isCollapsed ? <Icons.caretR /> : <Icons.caretD />)}
        </span>
        <span className="ph-tree-icon">
          <Icons.folder />
        </span>
        <span className="ph-tree-text" title={node.name}>
          {node.name}
        </span>
        <span className="ph-tree-count">{node.paper_count}</span>
      </div>
    );
  };

  /** Render a subtree, honouring the user's collapsed set. */
  const renderSubtree = (nodes: CollectionNode[], depth: number): JSX.Element[] =>
    nodes.flatMap((node) => {
      const rows = [renderFolder(node, depth)];
      if (!collapsed.has(node.id)) rows.push(...renderSubtree(node.children, depth + 1));
      // A newly-created child belongs directly under its parent, before the
      // parent's siblings.
      if (editing?.mode === "create" && editing.parentId === node.id) {
        rows.push(<Fragment key={`new-${node.id}`}>{inlineEditor(depth + 1)}</Fragment>);
      }
      return rows;
    });

  const renderZoteroCollections = (
    library: ZoteroLibrary,
    all: ZoteroCollection[],
    parentKey: string | null,
    depth: number,
  ): JSX.Element[] => {
    const knownKeys = new Set(all.map((collection) => collection.key));
    return all
      .filter((collection) => {
        if (parentKey !== null) return collection.parentKey === parentKey;
        return collection.parentKey === null || !knownKeys.has(collection.parentKey);
      })
      .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
      .flatMap((collection) => {
        const nodeId = zoteroCollectionNodeId(library, collection.key);
        const children = all.filter((candidate) => candidate.parentKey === collection.key);
        const isCollapsed = collapsed.has(nodeId);
        const row = (
          <div
            key={nodeId}
            className={selectedCol === nodeId ? "ph-tree-row is-active" : "ph-tree-row"}
            style={{ paddingLeft: 8 + depth * 15 }}
            onClick={() => selectCol(nodeId)}
            title={`${library.name} / ${collection.name}`}
          >
            <span
              className="ph-tree-caret"
              onClick={(event) => {
                event.stopPropagation();
                if (children.length === 0) return;
                setCollapsed((previous) => {
                  const next = new Set(previous);
                  if (next.has(nodeId)) next.delete(nodeId);
                  else next.add(nodeId);
                  return next;
                });
              }}
            >
              {children.length > 0 && (isCollapsed ? <Icons.caretR /> : <Icons.caretD />)}
            </span>
            <span className="ph-tree-icon">
              <Icons.folder />
            </span>
            <span className="ph-tree-text">{collection.name}</span>
            <span className="ph-tree-count">{collection.itemCount}</span>
          </div>
        );
        return isCollapsed
          ? [row]
          : [row, ...renderZoteroCollections(library, all, collection.key, depth + 1)];
      });
  };

  const renderSavedSearches = (
    library: ZoteroLibrary,
    searches: ZoteroSavedSearch[],
    depth: number,
  ): JSX.Element[] =>
    searches.map((search) => {
      const nodeId = zoteroSavedSearchNodeId(library, search.key);
      return (
        <div
          key={nodeId}
          className={selectedCol === nodeId ? "ph-tree-row is-active" : "ph-tree-row"}
          style={{ paddingLeft: 8 + depth * 15 }}
          title={`Zotero 保存的搜索 · ${search.name}`}
          onClick={() => selectCol(nodeId)}
        >
          <span className="ph-tree-caret" />
          <span className="ph-tree-icon">
            <Icons.search />
          </span>
          <span className="ph-tree-text">{search.name}</span>
        </div>
      );
    });

  const renderZoteroLibrary = (library: ZoteroLibrary, index: number): JSX.Element => {
    const nodeId = zoteroLibraryNodeId(library);
    const libraryCollections = zoteroCollectionQueries[index]?.data ?? [];
    const savedSearches = zoteroSearchQueries[index]?.data ?? [];
    const count = zoteroCountQueries[index]?.data?.total;
    const hasChildren = libraryCollections.length > 0 || savedSearches.length > 0;
    const isCollapsed = collapsed.has(nodeId);
    return (
      <Fragment key={nodeId}>
        <div
          className={selectedCol === nodeId ? "ph-tree-row is-active" : "ph-tree-row"}
          style={{ paddingLeft: 8 + 15 }}
          onClick={() => selectCol(nodeId)}
          title={library.kind === "group" ? `Zotero 群组文库 · ${library.name}` : "Zotero 个人文库"}
        >
          <span
            className="ph-tree-caret"
            onClick={(event) => {
              event.stopPropagation();
              if (!hasChildren) return;
              setCollapsed((previous) => {
                const next = new Set(previous);
                if (next.has(nodeId)) next.delete(nodeId);
                else next.add(nodeId);
                return next;
              });
            }}
          >
            {hasChildren && (isCollapsed ? <Icons.caretR /> : <Icons.caretD />)}
          </span>
          <span className="ph-tree-icon">
            {library.kind === "group" ? <Icons.cloud /> : <Icons.library />}
          </span>
          <span className="ph-tree-text" title={library.name}>
            {library.name}
          </span>
          {count !== undefined && <span className="ph-tree-count">{count}</span>}
        </div>
        {!isCollapsed && renderZoteroCollections(library, libraryCollections, null, 2)}
        {!isCollapsed && renderSavedSearches(library, savedSearches, 2)}
      </Fragment>
    );
  };

  const builtins = [
    {
      id: "lib",
      label: "我的文库",
      icon: <Icons.folder />,
      count: collections.data?.all_count,
    },
    {
      id: "uncat",
      label: "未分类",
      icon: <Icons.inbox />,
      count: collections.data?.uncategorised_count,
    },
    { id: "trash", label: "回收站", icon: <Icons.trash />, count: trash.data?.length },
  ];

  const renderBuiltin = (b: (typeof builtins)[number]) => (
    <div
      key={b.id}
      className={selectedCol === b.id ? "ph-tree-row is-active" : "ph-tree-row"}
      style={{ paddingLeft: 8 }}
      onClick={() => selectCol(b.id)}
    >
      <span className="ph-tree-icon">{b.icon}</span>
      <span className="ph-tree-text">{b.label}</span>
      {/* Absent until the request lands: a count of 0 would be a claim, and
          "we don't know yet" is not the same claim as "there are none". */}
      {b.count !== undefined && <span className="ph-tree-count">{b.count}</span>}
    </div>
  );

  const tagList = tags.data ?? [];

  return (
    <aside className="ph-tree ph-scroll">
      <div className="ph-tree-head">
        <span className="ph-tree-head-text">分类</span>
        <button
          className="ph-tree-add"
          title="新建分类"
          onClick={() => {
            setEditing({ mode: "create", parentId: null });
            if (!favOpen) toggleGroup("favOpen");
          }}
        >
          <Icons.plus />
        </button>
      </div>

      {renderBuiltin(builtins[0])}

      {zoteroAvailable() && (
        <>
          <div
            className="ph-tree-row"
            style={{ paddingLeft: 8 }}
            onClick={() => toggleGroup("zoteroOpen")}
            title={
              localStatus.data?.available
                ? "本机 Zotero 在线，条目与 PDF 保留在本地"
                : localStatus.data?.itemCount
                  ? "Zotero 未运行，正在使用上次同步的离线镜像"
                  : "启动 Zotero 后即可同步完整文库"
            }
          >
            <span className="ph-tree-caret">
              {zoteroOpen ? <Icons.caretD /> : <Icons.caretR />}
            </span>
            <span className="ph-tree-icon">
              <Icons.library />
            </span>
            <span className="ph-tree-text">
              {localStatus.data?.available ? "本机 Zotero" : "Zotero 离线镜像"}
            </span>
            {localStatus.data && <span className="ph-tree-count">{localStatus.data.itemCount}</span>}
          </div>
          {zoteroOpen &&
            (zoteroLibraries.data ?? []).map((library, index) =>
              renderZoteroLibrary(library, index),
            )}
          {zoteroOpen && zoteroLibraries.isPending && (
            <div className="ph-tree-empty" style={{ paddingLeft: 8 + 15 }}>
              正在读取 Zotero…
            </div>
          )}
          {zoteroOpen && zoteroLibraries.isError && (
            <div className="ph-tree-empty" style={{ paddingLeft: 8 + 15 }}>
              无法读取 Zotero 镜像
            </div>
          )}
        </>
      )}

      <div className="ph-tree-row" style={{ paddingLeft: 8 }} onClick={() => toggleGroup("favOpen")}>
        <span className="ph-tree-caret">{favOpen ? <Icons.caretD /> : <Icons.caretR />}</span>
        <span className="ph-tree-icon">
          <Icons.star />
        </span>
        <span className="ph-tree-text">收藏夹</span>
      </div>

      {favOpen && (
        <>
          {renderSubtree(tree, 1)}
          {editing?.mode === "create" && editing.parentId === null && inlineEditor(1)}
          {/* The prototype's muted placeholder, now a real empty state. It is
              suppressed while the first folder is being named, and while the
              tree is still loading — "暂无分类" during a pending request would
              be asserting something not yet known. */}
          {tree.length === 0 && editing === null && !collections.isPending && (
            <div className="ph-tree-empty" style={{ paddingLeft: 8 + 15 }}>
              {collections.isError ? "无法载入分类" : "暂无分类"}
            </div>
          )}
        </>
      )}

      {builtins.slice(1).map(renderBuiltin)}

      {error !== null && (
        <div className="ph-tree-msg" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      {/* Tags: real names, colours and counts. The chips are deliberately not
          filters — see the note in ItemList's header comment and the handover
          report: nothing in the current API maps a tag to its papers, and
          faking it with one request per row is not a trade worth making. */}
      {tagList.length > 0 && (
        <>
          <div className="ph-tree-div" />
          <div className="ph-tree-head">
            <span className="ph-tree-head-text">标签</span>
          </div>
          <div className="ph-tree-tags">
            {tagList.map((t) => (
              <span
                key={t.id}
                className="ph-tree-tag"
                data-color={t.color ?? undefined}
                title={`${t.name} · ${t.paper_count} 篇`}
              >
                {t.name}
              </span>
            ))}
          </div>
        </>
      )}

      {menu !== null && (
        <div
          className="ph-tree-menu"
          style={{ left: menu.x, top: menu.y }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {menu.confirmingDelete ? (
            <>
              <div className="ph-tree-menu-note">删除“{menu.name}”？子分类会上移一层。</div>
              <button className="ph-tree-menu-item" onClick={() => setMenu(null)}>
                取消
              </button>
              <button
                className="ph-tree-menu-item is-danger"
                onClick={() => remove.mutate(menu.id)}
              >
                删除
              </button>
            </>
          ) : (
            <>
              <button
                className="ph-tree-menu-item"
                onClick={() => {
                  setEditing({ mode: "rename", id: menu.id, initial: menu.name });
                  setMenu(null);
                }}
              >
                重命名
              </button>
              {menu.depth < MAX_VISUAL_DEPTH && (
                <button
                  className="ph-tree-menu-item"
                  onClick={() => {
                    setCollapsed((prev) => {
                      const next = new Set(prev);
                      next.delete(menu.id);
                      return next;
                    });
                    setEditing({ mode: "create", parentId: menu.id });
                    setMenu(null);
                  }}
                >
                  新建子分类
                </button>
              )}
              <button
                className="ph-tree-menu-item is-danger"
                onClick={() => setMenu({ ...menu, confirmingDelete: true })}
              >
                删除
              </button>
            </>
          )}
        </div>
      )}
    </aside>
  );
}
