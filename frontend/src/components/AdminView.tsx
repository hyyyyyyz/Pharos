import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AdminProbeResult, AdminStats, AdminUser } from "../api/types";
import { Icons } from "../design/icons";
import { useSession } from "../store";
import { EmptyState, ModuleShell, PageHeader, SidePanel, SkeletonRows } from "./shell";
import "./AdminView.css";
import { isTranslationDegraded } from "../lib/providers";

type Tab = "users" | "providers";

const cx = (...parts: (string | false | undefined)[]): string =>
  parts.filter(Boolean).join(" ");

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "操作失败，请稍后再试";

/** "2026-07-29" — the console cares about the day, not the minute. */
const day = (iso: string | null): string => (iso ? iso.slice(0, 10) : "—");

/**
 * The administrator console.
 *
 * A module beside 文库 and the rest rather than a separate app: an operator is
 * also a researcher, so switching to 管理员后台 must not mean leaving the
 * workbench.
 */
export function AdminView(): JSX.Element {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <ModuleShell
      side={
        <SidePanel storageKey="admin" title="管理">
          <button
            type="button"
            className={cx("ph-sp-row", tab === "users" && "is-active")}
            aria-current={tab === "users" ? "page" : undefined}
            onClick={() => setTab("users")}
          >
            <Icons.user />
            账号管理
          </button>
          <button
            type="button"
            className={cx("ph-sp-row", tab === "providers" && "is-active")}
            aria-current={tab === "providers" ? "page" : undefined}
            onClick={() => setTab("providers")}
          >
            <Icons.sliders />
            API 配置
          </button>
        </SidePanel>
      }
    >
      {tab === "users" ? <UsersPanel /> : <ProvidersPanel />}
    </ModuleShell>
  );
}

/* ------------------------------------------------------------------- users */

function UsersPanel(): JSX.Element {
  const qc = useQueryClient();
  const me = useSession((s) => s.user);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  /** The account awaiting a typed-email confirmation, if any. */
  const [pendingDelete, setPendingDelete] = useState<AdminUser | null>(null);

  const stats = useQuery({ queryKey: ["admin", "stats"], queryFn: api.admin.stats });
  const users = useQuery({
    queryKey: ["admin", "users", q],
    queryFn: ({ signal }) => api.admin.listUsers({ q: q || undefined, limit: 200, signal }),
  });

  const patch = useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Parameters<typeof api.admin.updateUser>[1]) =>
      api.admin.updateUser(id, body),
    onMutate: () => setError(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
    },
    // The backend refuses the lockout cases with a 409 and an explanatory
    // message; showing it verbatim is more useful than a generic failure.
    onError: (e) => setError(errText(e)),
  });

  const remove = useMutation({
    mutationFn: ({ id, email }: { id: string; email: string }) =>
      api.admin.deleteUser(id, email),
    onMutate: () => setError(null),
    onSuccess: () => {
      setPendingDelete(null);
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
    },
    onError: (e) => setError(errText(e)),
  });

  const s = stats.data;

  return (
    <>
      <PageHeader
        title="账号管理"
        meta={users.data ? `共 ${users.data.total} 个账号` : undefined}
        lead={
          <div className="ph-admin-searchwrap">
            <span className="ph-admin-searchicon">
              <Icons.search />
            </span>
            <input
              className="ph-input ph-admin-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索邮箱或名称"
              aria-label="搜索用户"
            />
          </div>
        }
      />
      <div className="ph-admin-body ph-scroll">
        {s && <AccountSummary stats={s} />}

        {error && <div className="ph-admin-error">{error}</div>}

        {users.isLoading ? (
          <SkeletonRows rows={4} />
        ) : users.isError ? (
          <EmptyState inline icon={Icons.user} tone="error" title="无法载入用户" description={errText(users.error)} />
        ) : !users.data?.users.length ? (
          <EmptyState inline icon={Icons.user} title={q ? "没有匹配的用户" : "还没有用户"} />
        ) : (
          <table className="ph-admin-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>注册</th>
                <th>最近登录</th>
                <th>角色</th>
                <th className="is-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.data.users.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  isSelf={u.id === me?.id}
                  busy={patch.isPending || remove.isPending}
                  onPatch={(body) => patch.mutate({ id: u.id, ...body })}
                  onDelete={() => setPendingDelete(u)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {pendingDelete && (
        <DeleteDialog
          user={pendingDelete}
          busy={remove.isPending}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() =>
            remove.mutate({ id: pendingDelete.id, email: pendingDelete.email })
          }
        />
      )}
    </>
  );
}

function DeleteDialog({
  user,
  busy,
  onCancel,
  onConfirm,
}: {
  user: AdminUser;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  const [typed, setTyped] = useState("");
  const matches = typed.trim().toLowerCase() === user.email.toLowerCase();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="ph-admin-overlay" role="presentation" onClick={onCancel}>
      <div
        className="ph-admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="删除用户"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ph-admin-dialog-title">删除用户</div>
        <p className="ph-admin-dialog-body">
          将永久删除 <b>{user.email}</b> 的 Pharos 账号及服务器端数据。本机
          Zotero/Pharos 文库不会被统计或删除。此操作<b>无法撤销</b>。
        </p>
        <p className="ph-admin-dialog-hint">请输入该用户的邮箱以确认：</p>
        <input
          className="ph-input ph-admin-dialog-input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={user.email}
          autoFocus
          aria-label="确认邮箱"
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches && !busy) onConfirm();
          }}
        />
        <div className="ph-admin-dialog-actions">
          <button className="ph-btn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            className="ph-btn is-danger"
            onClick={onConfirm}
            disabled={!matches || busy}
          >
            {busy ? "删除中" : "永久删除"}
          </button>
        </div>
      </div>
    </div>
  );
}

function AccountSummary({ stats }: { stats: AdminStats }): JSX.Element {
  const share = (n: number): string =>
    stats.users === 0 ? "—" : `占账号 ${Math.round((n / stats.users) * 100)}%`;
  const cards: { label: string; value: string; note: string }[] = [
    { label: "账号", value: String(stats.users), note: "全部已注册账号" },
    { label: "管理员", value: String(stats.admins), note: share(stats.admins) },
    { label: "已停用", value: String(stats.inactive_users), note: share(stats.inactive_users) },
    {
      label: "注册",
      value: stats.allow_registration ? "开放" : "关闭",
      note: "由服务器 .env 控制",
    },
  ];
  return (
    <div className="ph-admin-stats" aria-label="账号概览">
      {cards.map((c) => (
        <div key={c.label} className="ph-admin-stat">
          <span className="ph-admin-stat-label">{c.label}</span>
          <span className="ph-admin-stat-value">{c.value}</span>
          <span className="ph-admin-stat-note">{c.note}</span>
        </div>
      ))}
    </div>
  );
}

function UserRow({
  user,
  isSelf,
  busy,
  onPatch,
  onDelete,
}: {
  user: AdminUser;
  isSelf: boolean;
  busy: boolean;
  onPatch: (body: { is_admin?: boolean; is_active?: boolean }) => void;
  onDelete: () => void;
}): JSX.Element {
  const name = user.display_name?.trim() || user.email.split("@")[0];
  return (
    <tr className={cx(!user.is_active && "is-suspended")}>
      <td>
        <div className="ph-admin-user">
          <div className="ph-admin-userinfo">
            <div className="ph-admin-username">
              {name}
              {isSelf && <span className="ph-admin-self">你</span>}
            </div>
            <div className="ph-admin-useremail">{user.email}</div>
          </div>
        </div>
      </td>
      <td className="ph-admin-date">{day(user.created_at)}</td>
      <td className="ph-admin-date">{day(user.last_login_at)}</td>
      <td>
        <span className={cx("ph-admin-role-text", user.is_admin && "is-admin")}>
          {user.is_admin ? "管理员" : "普通用户"}
          {!user.is_active && " · 已停用"}
        </span>
      </td>
      <td>
        <div className="ph-admin-actions">
          {/* Both actions are hidden for your own row rather than shown and
              rejected: the backend refuses them, and a button that always
              errors is worse than no button. */}
          {!isSelf && (
            <>
              <button
                className="ph-admin-act"
                disabled={busy}
                onClick={() => onPatch({ is_admin: !user.is_admin })}
              >
                {user.is_admin ? "降为普通" : "设为管理员"}
              </button>
              <button
                className={cx("ph-admin-act", user.is_active && "is-danger")}
                disabled={busy}
                onClick={() => onPatch({ is_active: !user.is_active })}
              >
                {user.is_active ? "停用" : "恢复"}
              </button>
              <button
                className="ph-admin-trash"
                disabled={busy}
                onClick={onDelete}
                title={`删除 ${user.email}`}
                aria-label={`删除用户 ${user.email}`}
              >
                <Icons.trash />
              </button>
            </>
          )}
          {isSelf && <span className="ph-admin-selfnote">当前账户</span>}
        </div>
      </td>
    </tr>
  );
}

/* --------------------------------------------------------------- providers */

function ProvidersPanel(): JSX.Element {
  const [probes, setProbes] = useState<Record<string, AdminProbeResult | "running">>({});
  const providers = useQuery({
    queryKey: ["admin", "providers"],
    queryFn: api.admin.providers,
  });

  const probe = async (name: string) => {
    setProbes((p) => ({ ...p, [name]: "running" }));
    try {
      const result = await api.admin.probeProvider(name);
      setProbes((p) => ({ ...p, [name]: result }));
    } catch (e) {
      setProbes((p) => ({
        ...p,
        [name]: { name, ok: false, latency_ms: null, detail: errText(e) },
      }));
    }
  };

  const degraded = useMemo(() => isTranslationDegraded(providers.data), [providers.data]);

  if (providers.isLoading)
    return (
      <>
        <PageHeader title="API 配置" />
        <SkeletonRows rows={4} />
      </>
    );
  if (providers.isError)
    return (
      <>
        <PageHeader title="API 配置" />
        <EmptyState icon={Icons.sliders} tone="error" title="无法载入配置" description={errText(providers.error)} />
      </>
    );
  const d = providers.data!;

  return (
    <>
      <PageHeader title="API 配置" meta="只读 · 由服务器 .env 配置" />
      <div className="ph-admin-body ph-scroll">
      <div className="ph-admin-note">
        API 密钥由服务器的 <code>.env</code> 统一配置，所有用户共享，无需各自填写。
        本页只读——修改密钥需在服务器上编辑 <code>.env</code> 并重启。密钥本身不会传到浏览器。
      </div>

      {degraded && (
        <div className="ph-admin-warn">
          翻译已降级：配置的是 <b>{d.translator}</b>，实际生效的是{" "}
          <b>{d.effective_translator}</b>（免费引擎）。通常是密钥缺失或无效。
        </div>
      )}

      <div className="ph-admin-roles">
        <RoleChip label="翻译" value={d.effective_translator} />
        <RoleChip label="AI 对话" value={d.chat_provider} />
      </div>

      <div className="ph-admin-providers">
        {d.providers.map((p) => {
          const probed = probes[p.name];
          return (
            <div key={p.name} className={cx("ph-admin-provider", p.configured && "is-on")}>
              <div className="ph-admin-provider-head">
                <span className="ph-admin-provider-name">{p.label}</span>
                <span className={cx("ph-admin-role-text", p.configured && "is-ready")}>
                  {p.configured ? "已配置" : "未配置"}
                </span>
              </div>
              <dl className="ph-admin-kv">
                <dt>模型</dt>
                <dd>{p.model || "—"}</dd>
                <dt>地址</dt>
                <dd className="ph-admin-url">{p.base_url || "—"}</dd>
                <dt>密钥</dt>
                <dd>{p.key_hint ? `已设置 · 尾号 ${p.key_hint}` : "未设置"}</dd>
              </dl>
              {p.roles.length > 0 && (
                <div className="ph-admin-provider-roles">
                  {p.roles.map((r) => (
                    <span key={r} className="ph-admin-rolechip">
                      {r === "translate" ? "用于翻译" : "用于对话"}
                    </span>
                  ))}
                </div>
              )}
              <div className="ph-admin-provider-foot">
                <button
                  className="ph-btn"
                  disabled={!p.configured || probed === "running"}
                  onClick={() => probe(p.name)}
                >
                  {probed === "running" ? "测试中" : "测试连通性"}
                </button>
                {probed && probed !== "running" && (
                  <span className={cx("ph-admin-probe", probed.ok ? "is-ok" : "is-bad")}>
                    {probed.ok ? `正常 · ${probed.latency_ms}ms` : probed.detail}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      </div>
    </>
  );
}

function RoleChip({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="ph-admin-role">
      <span className="ph-admin-role-label">{label}</span>
      <span className="ph-admin-role-value">{value}</span>
    </div>
  );
}
