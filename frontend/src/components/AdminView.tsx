import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AdminProbeResult, AdminUser } from "../api/types";
import { Icons } from "../design/icons";
import { useSession } from "../store";
import "./AdminView.css";

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
 * also a researcher, so switching to 管理 must not mean leaving the workbench.
 */
export function AdminView(): JSX.Element {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <div className="ph-admin">
      <header className="ph-admin-head">
        <div className="ph-admin-title">
          <span className="ph-admin-title-icon">
            <Icons.user size={16} />
          </span>
          管理后台
        </div>
        <nav className="ph-admin-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "users"}
            className={cx("ph-admin-tab", tab === "users" && "is-on")}
            onClick={() => setTab("users")}
          >
            用户
          </button>
          <button
            role="tab"
            aria-selected={tab === "providers"}
            className={cx("ph-admin-tab", tab === "providers" && "is-on")}
            onClick={() => setTab("providers")}
          >
            API 配置
          </button>
        </nav>
      </header>

      <div className="ph-admin-body ph-scroll">
        {tab === "users" ? <UsersPanel /> : <ProvidersPanel />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- users */

function UsersPanel(): JSX.Element {
  const qc = useQueryClient();
  const me = useSession((s) => s.user);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  const s = stats.data;

  return (
    <div className="ph-admin-panel">
      {s && (
        <div className="ph-admin-stats">
          <Stat label="用户" value={s.users} hint={`${s.admins} 位管理员`} />
          <Stat label="论文" value={s.papers} hint={`${s.translated_papers} 篇已翻译`} />
          <Stat label="研究项目" value={s.projects} />
          <Stat label="每日论文" value={s.daily_papers} hint={`${s.searches} 次检索`} />
        </div>
      )}

      <div className="ph-admin-toolbar">
        <div className="ph-admin-searchwrap">
          <span className="ph-admin-searchicon">
            <Icons.search size={14} />
          </span>
          <input
            className="ph-admin-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索邮箱或名称…"
            aria-label="搜索用户"
          />
        </div>
        {s && (
          <span className="ph-admin-reg">
            注册{s.allow_registration ? "开放中" : "已关闭"}
            <span className="ph-admin-reg-hint">· 由服务器 .env 控制</span>
          </span>
        )}
      </div>

      {error && <div className="ph-admin-error">{error}</div>}

      {users.isLoading ? (
        <p className="ph-admin-empty">载入中…</p>
      ) : users.isError ? (
        <p className="ph-admin-error">{errText(users.error)}</p>
      ) : !users.data?.users.length ? (
        <p className="ph-admin-empty">{q ? "没有匹配的用户" : "还没有用户"}</p>
      ) : (
        <table className="ph-admin-table">
          <thead>
            <tr>
              <th>用户</th>
              <th className="ph-admin-num">论文</th>
              <th className="ph-admin-num">项目</th>
              <th className="ph-admin-num">高亮</th>
              <th>注册</th>
              <th>最近登录</th>
              <th>角色</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {users.data.users.map((u) => (
              <UserRow
                key={u.id}
                user={u}
                isSelf={u.id === me?.id}
                busy={patch.isPending}
                onPatch={(body) => patch.mutate({ id: u.id, ...body })}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}): JSX.Element {
  return (
    <div className="ph-admin-stat">
      <div className="ph-admin-stat-value">{value}</div>
      <div className="ph-admin-stat-label">{label}</div>
      {hint && <div className="ph-admin-stat-hint">{hint}</div>}
    </div>
  );
}

function UserRow({
  user,
  isSelf,
  busy,
  onPatch,
}: {
  user: AdminUser;
  isSelf: boolean;
  busy: boolean;
  onPatch: (body: { is_admin?: boolean; is_active?: boolean }) => void;
}): JSX.Element {
  const name = user.display_name?.trim() || user.email.split("@")[0];
  return (
    <tr className={cx(!user.is_active && "is-suspended")}>
      <td>
        <div className="ph-admin-user">
          <span className="ph-admin-avatar" aria-hidden>
            <Icons.user size={14} />
          </span>
          <div className="ph-admin-userinfo">
            <div className="ph-admin-username">
              {name}
              {isSelf && <span className="ph-admin-self">你</span>}
            </div>
            <div className="ph-admin-useremail">{user.email}</div>
          </div>
        </div>
      </td>
      <td className="ph-admin-num">{user.papers}</td>
      <td className="ph-admin-num">{user.projects}</td>
      <td className="ph-admin-num">{user.highlights}</td>
      <td className="ph-admin-date">{day(user.created_at)}</td>
      <td className="ph-admin-date">{day(user.last_login_at)}</td>
      <td>
        <span className={cx("ph-admin-badge", user.is_admin ? "is-admin" : "is-plain")}>
          {user.is_admin ? "管理员" : "普通用户"}
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

  const degraded = useMemo(() => {
    const d = providers.data;
    if (!d) return false;
    // The configured translator lost to the keyless fallback — i.e. its key is
    // missing or unusable and translation quietly got worse.
    return d.translator !== "bing" && d.translator !== "google" && d.effective_translator !== d.translator;
  }, [providers.data]);

  if (providers.isLoading) return <p className="ph-admin-empty">载入中…</p>;
  if (providers.isError)
    return <p className="ph-admin-error">{errText(providers.error)}</p>;
  const d = providers.data!;

  return (
    <div className="ph-admin-panel">
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
                <span
                  className={cx(
                    "ph-admin-badge",
                    p.configured ? "is-ready" : "is-plain",
                  )}
                >
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
                  className="ph-admin-act"
                  disabled={!p.configured || probed === "running"}
                  onClick={() => probe(p.name)}
                >
                  {probed === "running" ? "测试中…" : "测试连通性"}
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
