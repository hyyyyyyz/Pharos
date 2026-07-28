import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icons } from "../design/icons";
import { ACCENTS, accentSwatch } from "../design/tokens";
import type { ThemeMode } from "../design/tokens";
import { api } from "../api/client";
import type { AuthUser, ZoteroStatus } from "../api/types";
import { zotero, zoteroAvailable } from "../lib/zotero";
import type { ZoteroConnectionStatus, ZoteroSyncReport } from "../types/zotero";
import {
  desktopZoteroOAuth,
  type ZoteroOAuthResult,
} from "../lib/zoteroOAuth";
import { pdfTranslationEnabled, useSession, useUI, type SettingsTab } from "../store";
import { DirectionsSettings } from "./DirectionsSettings";
import "./SettingsModal.css";

type IconComponent = (typeof Icons)["user"];

const TABS: { key: SettingsTab; label: string; Icon: IconComponent }[] = [
  { key: "account", label: "账户", Icon: Icons.user },
  { key: "appearance", label: "外观", Icon: Icons.palette },
  // Last, and using the rail's own 每日论文 icon so the tab is recognisable as
  // the settings for that module rather than a third appearance-like section.
  { key: "daily", label: "每日论文", Icon: Icons.daily },
];

const THEMES: { key: ThemeMode; label: string; Icon: IconComponent }[] = [
  { key: "light", label: "浅色", Icon: Icons.sun },
  { key: "dark", label: "深色", Icon: Icons.moon },
];

/** The 整篇 PDF 翻译 control, shaped exactly like the 主题 buttons above it. */
const PDF_TX: { on: boolean; label: string; Icon: IconComponent }[] = [
  { on: true, label: "开启", Icon: Icons.check },
  { on: false, label: "关闭", Icon: Icons.close },
];

const cx = (...parts: (string | false)[]): string => parts.filter(Boolean).join(" ");

/**
 * PATCH the whole-PDF-translation preference onto the signed-in account.
 *
 * `updateMe` sets the session user from its own response, so the toggle's new
 * value reaches `pdfTranslationEnabled` — and therefore the reader and the item
 * list — without a second fetch.
 */
const updatePdfTranslation = (on: boolean): Promise<AuthUser> =>
  api.auth.updateMe({ pdf_translation: on });

/**
 * The name to show for a user who never set a display name. Falling back to the
 * email's local part keeps the identity block from rendering an empty line, and
 * is honest — it is derived from the account, not invented.
 */
const nameOf = (u: AuthUser): string => u.display_name?.trim() || u.email.split("@")[0];

/** Absolute wall-clock, not "3 minutes ago": a sync time the user can verify. */
function fmtTime(iso: string | null): string {
  if (!iso) return "尚未同步";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "尚未同步";
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtEpoch(ms: number | null): string {
  if (ms === null) return "尚未同步";
  return fmtTime(new Date(ms).toISOString());
}

/** Error text from a rejected mutation, without leaking a stack trace into the UI. */
const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

const ZOTERO_RESULT_COPY: Record<
  ZoteroOAuthResult,
  { tone: "ok" | "neutral" | "error"; text: string }
> = {
  connected: { tone: "ok", text: "Zotero 已授权，首次同步已启动。" },
  cancelled: { tone: "neutral", text: "你取消了 Zotero 授权，现有连接没有改变。" },
  expired: { tone: "error", text: "授权已过期，请重新发起。" },
  invalid: { tone: "error", text: "授权状态无效或已使用，请重新连接。" },
  busy: { tone: "error", text: "当前有同步任务运行，请完成后重试。" },
  error: { tone: "error", text: "Zotero 授权未完成，请稍后重试；原有连接未被修改。" },
};

const isZoteroOAuthResult = (value: string): value is ZoteroOAuthResult =>
  Object.prototype.hasOwnProperty.call(ZOTERO_RESULT_COPY, value);

export function SettingsModal(): JSX.Element | null {
  const settingsOpen = useUI((s) => s.settingsOpen);
  const settingsTab = useUI((s) => s.settingsTab);
  const openSettings = useUI((s) => s.openSettings);
  const setSettingsTab = useUI((s) => s.setSettingsTab);
  const closeSettings = useUI((s) => s.closeSettings);
  const zoteroOAuthResult = useUI((s) => s.zoteroOAuthResult);
  const setZoteroOAuthResult = useUI((s) => s.setZoteroOAuthResult);
  const theme = useUI((s) => s.theme);
  const setTheme = useUI((s) => s.setTheme);
  const accent = useUI((s) => s.accent);
  const setAccent = useUI((s) => s.setAccent);
  /* The server's answer, not a local toggle: read from the session user, which
     `api.auth.me()` below refreshes on every open of this tab. */
  const pdfTx = useSession(pdfTranslationEnabled);

  const qc = useQueryClient();

  /* Only the 账户 tab needs the network, and only while the dialog is mounted
     open — a closed modal must not hold a poll open against /zotero/status. */
  const onAccount = settingsOpen && settingsTab === "account";

  const meQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.auth.me(),
    enabled: onAccount,
  });

  const zoteroQuery = useQuery({
    queryKey: ["zotero", "status"],
    queryFn: (): Promise<ZoteroStatus> => api.zotero.status(),
    enabled: onAccount,
    // A sync started here (or on another device) finishes server-side; poll
    // only while it is actually running, then stop.
    refetchInterval: (q) => (q.state.data?.status === "syncing" ? 1500 : false),
  });

  const localZoteroQuery = useQuery({
    queryKey: ["zotero-desktop", "status"],
    queryFn: (): Promise<ZoteroConnectionStatus> => zotero.status(),
    enabled: onAccount && zoteroAvailable(),
    staleTime: 2_000,
  });

  /* ------------------------------------------------------------ 显示名称 */

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  const rename = useMutation({
    // `api.auth.updateMe` already refreshes the cached session user, so anything
    // else rendering the name picks it up; only this query needs seeding.
    mutationFn: (display_name: string) => api.auth.updateMe({ display_name }),
    onSuccess: (user) => {
      qc.setQueryData(["auth", "me"], user);
      setEditingName(false);
    },
  });

  /* ------------------------------------------------------ 整篇 PDF 翻译 */

  /* Like `rename`: `api.auth.updateMe` pushes the returned user into the
     session, so every consumer of `pdfTranslationEnabled` — the reader's mode
     group, the detail panel's actions, the list's 状态 column — re-renders off
     that one write. Only this query needs seeding by hand. */
  const setPdfTx = useMutation({
    mutationFn: (on: boolean) => updatePdfTranslation(on),
    onSuccess: (user) => {
      qc.setQueryData(["auth", "me"], user);
    },
  });

  /* -------------------------------------------------------------- Zotero */

  const [zUserId, setZUserId] = useState("");
  const [zApiKey, setZApiKey] = useState("");
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [manualZoteroOpen, setManualZoteroOpen] = useState(false);
  const oauthStart = useMutation({
    mutationFn: async (): Promise<void> => {
      if (desktopZoteroOAuth.available()) {
        const start = await api.zotero.oauthDesktopStart();
        await desktopZoteroOAuth.start(start);
        return;
      }
      const start = await api.zotero.oauthStart();
      const authorize = new URL(start.authorize_url);
      if (
        authorize.protocol !== "https:" ||
        authorize.hostname !== "www.zotero.org" ||
        authorize.pathname !== "/oauth/authorize"
      ) {
        throw new Error("服务器返回了无效的 Zotero 授权地址。");
      }
      window.location.assign(start.authorize_url);
    },
    onMutate: () => setZoteroOAuthResult(null),
  });

  const link = useMutation({
    mutationFn: (): Promise<ZoteroStatus> =>
      api.zotero.link({ zotero_user_id: zUserId.trim(), api_key: zApiKey.trim() }),
    onSuccess: (status) => {
      // The key is never echoed back by the backend and must not linger in
      // memory once it has been handed over.
      setZUserId("");
      setZApiKey("");
      setManualZoteroOpen(false);
      qc.setQueryData(["zotero", "status"], status);
    },
  });

  const sync = useMutation({
    mutationFn: (): Promise<ZoteroStatus> => api.zotero.sync(),
    // POST /sync answers 202 with the *status* — the run itself is still going.
    // So seed the status query with what it returned (which reads "syncing",
    // starting the poll immediately) rather than treating this as a finished
    // sync. Invalidating ["papers"] here would be premature: nothing has been
    // imported yet. The poll's own completion does that, below.
    //
    // Invalidating in a success callback and never in an effect is deliberate:
    // an unconditional effect that invalidates ["papers"] refetches, re-renders,
    // and invalidates again forever. This codebase has had that loop before.
    onSuccess: (status) => {
      qc.setQueryData(["zotero", "status"], status);
    },
  });

  const syncLocal = useMutation({
    mutationFn: (): Promise<ZoteroSyncReport> => zotero.refresh(false),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["zotero-desktop"] });
      void qc.invalidateQueries({ queryKey: ["zotero-mirror"] });
    },
  });

  /* A sync finishing is the moment 文库 actually changed, and it can finish
     without this tab having started it (another device, or a run that outlived
     a reload). Watch the polled status for the syncing → not-syncing edge and
     refresh the library then. Edge-triggered, so a steady "linked" state does
     not re-invalidate on every poll — that is the loop described above. */
  const zotSyncing = zoteroQuery.data?.status === "syncing";
  const wasSyncing = useRef(false);
  useEffect(() => {
    if (wasSyncing.current && !zotSyncing) {
      void qc.invalidateQueries({ queryKey: ["papers"] });
    }
    wasSyncing.current = zotSyncing;
  }, [zotSyncing, qc]);

  const unlink = useMutation({
    mutationFn: (): Promise<void> => api.zotero.unlink(),
    onSuccess: () => {
      setConfirmUnlink(false);
      sync.reset();
      void qc.invalidateQueries({ queryKey: ["zotero", "status"] });
      void qc.invalidateQueries({ queryKey: ["papers"] });
    },
  });

  /* Zotero returns to the product root because this app deliberately has no
     client-side router. Consume its fixed result once, remove it from the URL
     before the user copies or refreshes the page, and take them straight to
     the connection they just acted on. React StrictMode runs this effect twice
     in development; the first pass removes the parameter, so the second is a
     no-op rather than a duplicate notification. */
  useEffect(() => {
    const url = new URL(window.location.href);
    const rawResult = url.searchParams.get("zotero");
    if (rawResult === null) return;

    url.searchParams.delete("zotero");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
    if (!isZoteroOAuthResult(rawResult)) return;

    setZoteroOAuthResult(rawResult);
    openSettings("account");
    void qc.invalidateQueries({ queryKey: ["zotero", "status"] });
    void qc.invalidateQueries({ queryKey: ["papers"] });
  }, [openSettings, qc, setZoteroOAuthResult]);

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSettings();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, closeSettings]);

  /**
   * Closing the dialog discards everything the user was part-way through.
   *
   * The dialog renders null when closed but never unmounts, so without this the
   * state survives: reopening 设置 would show 断开 still armed as 确认断开, one
   * stray click from unlinking, and would keep a typed-but-unsubmitted API key
   * live in React state and in the DOM. Both are things "I closed it" should
   * have undone. Guarded on `settingsOpen` so it runs on the close edge only —
   * it sets local state, and never invalidates a query.
   */
  useEffect(() => {
    if (settingsOpen) return;
    setConfirmUnlink(false);
    setEditingName(false);
    setZUserId("");
    setZApiKey("");
    setManualZoteroOpen(false);
  }, [settingsOpen]);

  /* Do not clear the callback result on the component's initial closed render:
     that render is exactly when the callback effect above opens the dialog.
     Clear it only on a real open → closed edge. */
  const settingsWasOpen = useRef(settingsOpen);
  useEffect(() => {
    const wasOpen = settingsWasOpen.current;
    settingsWasOpen.current = settingsOpen;
    if (wasOpen && !settingsOpen) setZoteroOAuthResult(null);
  }, [settingsOpen, setZoteroOAuthResult]);

  if (!settingsOpen) return null;

  const me = meQuery.data;
  const zot = zoteroQuery.data;
  const localZot = localZoteroQuery.data;

  /* The prototype's three branches, now driven by the server. */
  const connecting = oauthStart.isPending || link.isPending || zot?.status === "syncing";
  const connected = !!zot?.linked;
  const oauthConfigured = zot?.oauth_available === true;
  const showManualZotero = zot !== undefined && (!oauthConfigured || manualZoteroOpen);

  const signOut = () => {
    // Drop every cached answer as well as the token: a react-query cache that
    // survives sign-out would paint the previous user's library to whoever
    // signs in next on this browser, before any refetch could correct it.
    api.auth.logout();
    qc.clear();
    closeSettings();
  };

  const startRename = () => {
    setNameDraft(me?.display_name ?? "");
    rename.reset();
    setEditingName(true);
  };

  const submitRename = (e: React.FormEvent) => {
    e.preventDefault();
    rename.mutate(nameDraft.trim());
  };

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
            Pharos v0.3.1
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
                <div className="ph-set-acct-main">
                  {editingName ? (
                    <form className="ph-set-acct-edit" onSubmit={submitRename}>
                      <input
                        className="ph-set-input ph-set-input--inline"
                        value={nameDraft}
                        onChange={(e) => setNameDraft(e.target.value)}
                        placeholder="显示名称"
                        aria-label="显示名称"
                        maxLength={64}
                        autoFocus
                      />
                      <button
                        type="submit"
                        className="ph-set-btn ph-set-btn--on"
                        disabled={rename.isPending}
                      >
                        {rename.isPending ? "保存中…" : "保存"}
                      </button>
                      <button
                        type="button"
                        className="ph-set-btn"
                        onClick={() => setEditingName(false)}
                      >
                        取消
                      </button>
                    </form>
                  ) : (
                    <>
                      <div className="ph-set-acct-name">
                        {me ? nameOf(me) : meQuery.isError ? "无法读取账户" : "…"}
                      </div>
                      <div className="ph-set-acct-sub">{me?.email ?? ""}</div>
                    </>
                  )}
                  {rename.isError && (
                    <div className="ph-set-err">{errText(rename.error)}</div>
                  )}
                  {meQuery.isError && !editingName && (
                    <div className="ph-set-err">{errText(meQuery.error)}</div>
                  )}
                </div>
                {!editingName && (
                  <div className="ph-set-acct-actions">
                    <button
                      type="button"
                      className="ph-set-btn"
                      onClick={startRename}
                      disabled={!me}
                    >
                      重命名
                    </button>
                    <button
                      type="button"
                      className="ph-set-btn ph-set-btn--danger"
                      onClick={signOut}
                    >
                      退出登录
                    </button>
                  </div>
                )}
              </div>

              {/* Lives on the 账户 tab, not a tab of its own and not 外观:
                  it is stored against the account and travels with the user,
                  whereas everything on 外观 is a property of this screen. A
                  whole tab for one control would also read thin. */}
              <div className="ph-set-sec">
                <div className="ph-set-sec-head">
                  <span className="ph-set-ic ph-set-ic--tx2">
                    <Icons.spark />
                  </span>
                  <div className="ph-set-sec-title">阅读</div>
                </div>
                <div className="ph-set-label">整篇 PDF 翻译</div>
                <div className="ph-set-seg">
                  {PDF_TX.map(({ on, label, Icon }) => (
                    <button
                      key={label}
                      type="button"
                      disabled={setPdfTx.isPending}
                      onClick={() => setPdfTx.mutate(on)}
                      className={cx("ph-set-seg-btn", pdfTx === on && "ph-set-seg-btn--on")}
                    >
                      {/* Explicit size: Icons.check and Icons.close carry
                          different intrinsic ones (16 vs 12) and would sit
                          visibly uneven beside each other. */}
                      <span className="ph-set-ic">
                        <Icon size={14} />
                      </span>
                      {label}
                    </button>
                  ))}
                </div>
                <div className="ph-set-sec-desc">
                  开启后整篇论文会译成中文并保留原排版，读起来省力，但要等，也要花 API
                  额度；关闭后你直接读原文，遇到读不懂的段落再问领航。已经译好的论文不会消失，仍然可以切到中文对照。
                </div>
                {setPdfTx.isError && <div className="ph-set-err">{errText(setPdfTx.error)}</div>}
              </div>

              <div className="ph-set-zot">
                <div className="ph-set-zot-head">
                  <span className="ph-set-ic ph-set-ic--tx2">
                    <Icons.library />
                  </span>
                  <div className="ph-set-zot-title">Zotero</div>
                </div>

                {zoteroAvailable() && (
                  <div className="ph-set-zot-channel">
                    <div className="ph-set-zot-channel-head">
                      <span className="ph-set-ic">
                        <Icons.library />
                      </span>
                      <span>本机 Zotero</span>
                      <span className="ph-set-zot-badge">推荐</span>
                    </div>
                    <div className="ph-set-zot-desc">
                      直接读取这台 Mac 上的 Zotero 文库与 PDF，不需要 Zotero 云存储。文件默认留在本机，不会自动上传到 Pharos。
                    </div>

                    {localZoteroQuery.isPending && !localZot && (
                      <div className="ph-set-zot-connecting">
                        <span className="ph-set-ic ph-set-ic--spin">
                          <Icons.sync />
                        </span>
                        正在检测本机 Zotero…
                      </div>
                    )}

                    {localZoteroQuery.isError && (
                      <div className="ph-set-err">
                        无法读取本机 Zotero 状态：{errText(localZoteroQuery.error)}
                      </div>
                    )}

                    {localZot && (
                      <>
                        <div className="ph-set-zot-card">
                          <span
                            className={cx(
                              "ph-set-zot-check",
                              localZot.phase === "error" && "ph-set-zot-check--err",
                            )}
                          >
                            {localZot.phase === "error" ? <Icons.alert size={16} /> : <Icons.check />}
                          </span>
                          <div className="ph-set-zot-card-text">
                            <div className="ph-set-zot-card-title">
                              {syncLocal.isPending
                                ? "正在同步本机 Zotero"
                                : localZot.available
                                  ? "本机 Zotero 已连接"
                                  : localZot.itemCount > 0
                                    ? "Zotero 未运行 · 使用离线缓存"
                                    : "等待本机 Zotero"}
                            </div>
                            <div className="ph-set-zot-card-sub">
                              {localZot.libraryCount} 个文库 · {localZot.itemCount} 个 Zotero 对象
                              {localZot.lastSuccessfulSyncMs
                                ? ` · ${fmtEpoch(localZot.lastSuccessfulSyncMs)}`
                                : ""}
                            </div>
                          </div>
                          <button
                            type="button"
                            className="ph-set-btn"
                            onClick={() => syncLocal.mutate()}
                            disabled={!localZot.available || localZot.syncing || syncLocal.isPending}
                          >
                            {syncLocal.isPending || localZot.syncing ? "同步中…" : "同步"}
                          </button>
                        </div>
                        {!localZot.available && (
                          <div className="ph-set-zot-note">
                            请启动 Zotero，并在“设置 → 高级”中开启“允许其他应用与 Zotero 通信”。已有缓存和已导入论文不会丢失。
                          </div>
                        )}
                        {localZot.lastError && (
                          <div className="ph-set-err">{localZot.lastError}</div>
                        )}
                        {syncLocal.isError && (
                          <div className="ph-set-err">{errText(syncLocal.error)}</div>
                        )}
                      </>
                    )}
                  </div>
                )}

                <div
                  className={cx(
                    "ph-set-zot-channel",
                    zoteroAvailable() && "ph-set-zot-channel--cloud",
                  )}
                >
                  <div className="ph-set-zot-channel-head">
                    <span className="ph-set-ic">
                      <Icons.cloud />
                    </span>
                    <span>Zotero 云端</span>
                    {zoteroAvailable() && <span className="ph-set-zot-badge is-muted">可选</span>}
                  </div>
                  <div className="ph-set-zot-desc">
                    用于网页端和跨设备同步书目元数据；只有已经上传到 Zotero 云端的附件才可能跨设备获得。
                  </div>

                  {zoteroOAuthResult && (
                  <div
                    className={cx(
                      "ph-set-zot-result",
                      `ph-set-zot-result--${ZOTERO_RESULT_COPY[zoteroOAuthResult].tone}`,
                    )}
                    role="status"
                    aria-live="polite"
                  >
                    {ZOTERO_RESULT_COPY[zoteroOAuthResult].text}
                  </div>
                  )}

                {zoteroQuery.isPending && !zot && (
                  <div className="ph-set-zot-connecting">
                    <span className="ph-set-ic ph-set-ic--spin">
                      <Icons.sync />
                    </span>
                    正在读取 Zotero 连接状态…
                  </div>
                )}

                {!zoteroQuery.isPending && zoteroQuery.isError && (
                  <div className="ph-set-err">
                    无法读取 Zotero 连接状态：{errText(zoteroQuery.error)}
                  </div>
                )}

                {!zoteroQuery.isPending && !connecting && !connected && zot && (
                  <>
                    <div className="ph-set-zot-desc">
                      授权范围仅包含个人文库的只读访问；Pharos 不会把翻译、阅读状态或批注写回 Zotero。
                    </div>

                    {oauthConfigured ? (
                      <button
                        type="button"
                        className="ph-set-connect ph-set-zot-oauth"
                        onClick={() => oauthStart.mutate()}
                        disabled={oauthStart.isPending}
                      >
                        <span className="ph-set-ic">
                          <Icons.open />
                        </span>
                        {desktopZoteroOAuth.available()
                          ? "在浏览器中授权 Zotero"
                          : "前往 Zotero 授权"}
                      </button>
                    ) : (
                      <div className="ph-set-zot-unavailable" role="note">
                        当前服务器尚未配置一键授权，请暂时使用手动方式。
                      </div>
                    )}

                    {oauthStart.isError && (
                      <div className="ph-set-err">{errText(oauthStart.error)}</div>
                    )}

                    {oauthConfigured && (
                      <button
                        type="button"
                        className="ph-set-zot-manual-toggle"
                        aria-expanded={manualZoteroOpen}
                        onClick={() => setManualZoteroOpen((open) => !open)}
                      >
                        <span className="ph-set-ic">
                          {manualZoteroOpen ? <Icons.caretD /> : <Icons.caretR />}
                        </span>
                        无法使用网页授权？改用 API Key
                      </button>
                    )}

                    {showManualZotero && (
                      <div className="ph-set-zot-manual">
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            link.mutate();
                          }}
                        >
                          <input
                            className="ph-set-input"
                            value={zUserId}
                            onChange={(e) => setZUserId(e.target.value)}
                            placeholder="Zotero 用户 ID（userID）"
                            aria-label="Zotero 用户 ID"
                            autoComplete="off"
                          />
                          <input
                            className="ph-set-input"
                            type="password"
                            value={zApiKey}
                            onChange={(e) => setZApiKey(e.target.value)}
                            placeholder="Zotero API 密钥"
                            aria-label="Zotero API 密钥"
                            autoComplete="off"
                          />
                          <button
                            type="submit"
                            className="ph-set-connect ph-set-connect--manual"
                            disabled={!zUserId.trim() || !zApiKey.trim() || link.isPending}
                          >
                            <span className="ph-set-ic">
                              <Icons.link />
                            </span>
                            使用 API Key 连接
                          </button>
                        </form>
                        {link.isError && <div className="ph-set-err">{errText(link.error)}</div>}
                        <div className="ph-set-zot-note">
                          用户 ID 与只读密钥可在{" "}
                          <a
                            className="ph-set-zot-link"
                            href="https://www.zotero.org/settings/keys"
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Zotero 密钥页面
                          </a>
                          {" "}生成。
                        </div>
                      </div>
                    )}
                  </>
                )}

                {connecting && (
                  <div className="ph-set-zot-connecting">
                    <span className="ph-set-ic ph-set-ic--spin">
                      <Icons.sync />
                    </span>
                    {oauthStart.isPending
                      ? "正在打开 Zotero 授权页…"
                      : link.isPending
                        ? "正在验证 Zotero API Key…"
                        : "正在同步 Zotero 文献…"}
                  </div>
                )}

                  {!connecting && connected && zot && (
                  <>
                    <div className="ph-set-zot-card">
                      <span
                        className={cx(
                          "ph-set-zot-check",
                          zot.status === "error" && "ph-set-zot-check--err",
                        )}
                      >
                        {zot.status === "error" ? <Icons.alert size={16} /> : <Icons.check />}
                      </span>
                      <div className="ph-set-zot-card-text">
                        <div className="ph-set-zot-card-title">
                          {zot.status === "error"
                            ? "已连接 · 上次同步失败"
                            : zot.last_sync_at
                              ? "已连接 · 同步完成"
                              : "已连接 · 等待首次同步"}
                        </div>
                        <div className="ph-set-zot-card-sub">
                          {sync.isPending
                            ? "正在同步…"
                            : zot.last_sync_at
                              ? `${zot.item_count} 条文献 · 上次同步 ${fmtTime(zot.last_sync_at)}`
                              : `Zotero 用户 ${zot.zotero_user_id ?? "已验证"} · 尚未同步`}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="ph-set-btn"
                        onClick={() => sync.mutate()}
                        disabled={sync.isPending || unlink.isPending}
                      >
                        {sync.isPending ? "同步中…" : "同步"}
                      </button>
                      {confirmUnlink ? (
                        <button
                          type="button"
                          className="ph-set-btn ph-set-btn--danger"
                          onClick={() => unlink.mutate()}
                          disabled={unlink.isPending}
                        >
                          {unlink.isPending ? "断开中…" : "确认断开"}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="ph-set-zot-disconnect"
                          onClick={() => setConfirmUnlink(true)}
                        >
                          断开
                        </button>
                      )}
                    </div>

                    {/* The finished run's own numbers, from the polled status —
                        not from the POST, which returns before the sync has
                        added a single row. Rendered only when the backend
                        actually reported the counts: a run this process does
                        not remember (a restart) has nulls, and "新增 undefined"
                        is worse than saying nothing. */}
                    {zot.sync && !zot.sync.running && zot.sync.added !== null && (
                      <div className="ph-set-zot-note">
                        同步完成 · 新增 {zot.sync.added} · 更新 {zot.sync.updated ?? 0} · 共{" "}
                        {zot.sync.total ?? 0} 条文献
                        {zot.sync.skipped ? ` · 跳过 ${zot.sync.skipped}` : ""}
                      </div>
                    )}
                    {sync.isError && <div className="ph-set-err">{errText(sync.error)}</div>}
                    {unlink.isError && <div className="ph-set-err">{errText(unlink.error)}</div>}
                    {/* The backend's own recorded failure, distinct from a failed
                        request made just now — show it until a sync clears it. */}
                    {zot.status === "error" && zot.last_error && (
                      <div className="ph-set-err">{zot.last_error}</div>
                    )}
                  </>
                  )}
                </div>
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

          {/* Mounted only while its tab is showing, which is what keeps its two
              queries off the network the rest of the time — the same rule the
              账户 tab follows with `enabled: onAccount`. Here it falls out of
              the conditional render instead of needing a flag, and it also
              means every half-typed direction is discarded when the tab or the
              dialog is left, like the drafts above. */}
          {settingsTab === "daily" && <DirectionsSettings />}
        </div>
      </div>
    </>
  );
}
