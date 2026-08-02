import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api } from "../api/client";
import { Icons } from "../design/icons";
import { themeStyle } from "../design/tokens";
import { useSession, useUI } from "../store";
import { POSTER_ALT, POSTER_SRC } from "./poster";
import wordmark from "../assets/pharos-wordmark.png";
import "./AuthGate.css";

type Mode = "login" | "register";

/** Mirrors MIN_PASSWORD_LENGTH in pharos/auth/passwords.py. Checked here only
 *  to save a round-trip; the backend is still the authority. */
const MIN_PASSWORD = 8;

/** Deliberately loose. Strict email regexes reject valid addresses, and the
 *  only real test is whether the server accepts it — this catches typos. */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const cx = (...parts: (string | false)[]): string => parts.filter(Boolean).join(" ");

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "无法连接服务器，请稍后再试";

/**
 * A line for the sign-in footer.
 *
 * Aimed at someone opening this at the start of a long reading day, so the tone
 * is quiet encouragement rather than a slogan — nothing here congratulates the
 * user for showing up, and nothing promises what the product will do for them.
 *
 * The licence notice that used to sit here has not been dropped, only moved:
 * it lives in 设置 → 账户, which is what AGPL-3.0 §13 actually asks for — a way
 * for someone using this over a network to reach the source.
 */
const GREETINGS = [
  "愿今天有一篇，正好照亮你卡住的地方",
  "读不完的文献，一篇一篇来",
  "慢一点也没关系，读懂比读完重要",
  "好问题比好答案更难得",
  "今天也在往前，哪怕只挪了一点",
  "灯亮着，海就不算太黑",
  "你正在做的事，值得慢慢做",
  "先读一篇，再说别的",
];

/**
 * Picks by date rather than at random: a line that changed on every keystroke-
 * triggered re-render would be noise, and one that never changed would go stale.
 * Local date, so it turns over at the reader's midnight, not UTC's.
 */
function greeting(): string {
  const now = new Date();
  const days = Math.floor(
    (now.getTime() - now.getTimezoneOffset() * 60_000) / 86_400_000,
  );
  return GREETINGS[((days % GREETINGS.length) + GREETINGS.length) % GREETINGS.length]!;
}

export interface AuthGateProps {
  children: ReactNode;
}

/**
 * The boundary between "anonymous" and "signed in".
 *
 * Every user-scoped screen renders inside this, so there is exactly one place
 * that decides whether the app is reachable. It keys off the session token
 * alone: when `client.ts` clears the token on a 401 — expired, revoked by
 * logout-all, account deleted — the store notifies, this re-renders, and the
 * user lands on sign-in. That is the whole point of routing 401s through the
 * session store rather than letting each query fail on its own: the alternative
 * is an app that looks logged in and quietly fails every request.
 */
export function AuthGate({ children }: AuthGateProps): JSX.Element {
  const theme = useUI((s) => s.theme);
  const accent = useUI((s) => s.accent);
  const token = useSession((s) => s.token);
  const user = useSession((s) => s.user);

  /* A token restored from localStorage has not been checked against this
     server yet. Confirm it before painting the workbench, so a stale token
     does not produce a library that renders and then empties out. When the
     user is already cached (a normal in-session render) there is nothing to
     wait for and no request is made. */
  const verifying = token !== null && user === null;
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!verifying) return;
    let cancelled = false;
    setVerifyError(null);
    api.auth.me().catch((e: unknown) => {
      // A 401 has already cleared the session inside client.ts, so this branch
      // only really shows for a network/5xx failure — where signing the user
      // out would be wrong; they are not unauthenticated, the server is down.
      if (!cancelled && !(e instanceof ApiError && e.status === 401)) setVerifyError(errText(e));
    });
    return () => {
      cancelled = true;
    };
  }, [verifying, retry]);

  if (token !== null && !verifying) return <>{children}</>;

  return (
    <div className="ph-auth" style={themeStyle(theme, accent)}>
      <Poster />
      <main className="ph-auth-side">
        <div className="ph-auth-panel">
          {/* The real wordmark, not the line icon: this is the one place the
              product introduces itself, so it should be the brand asset. */}
          <div className="ph-auth-brand">
            <img className="ph-auth-wordmark-img" src={wordmark} alt="Pharos" />
          </div>
          <div className="ph-auth-tagline">从文献发现到研究推进的一体化科研工作台</div>

          {verifying ? (
            <Restoring
              error={verifyError}
              onRetry={() => setRetry((n) => n + 1)}
              onSignOut={() => api.auth.logout()}
            />
          ) : (
            <SignInForm />
          )}
        </div>
        <div className="ph-auth-foot">{greeting()}</div>
      </main>
    </div>
  );
}

/* --------------------------------------------------------------- the poster */

/**
 * The large left panel. Holds the artwork once one is installed (see
 * `poster.ts`); until then it renders a branded field so the page never looks
 * half-built. Decorative either way — the form carries all the meaning, so the
 * whole panel is hidden from assistive tech and dropped entirely on narrow
 * screens rather than squeezed into a stripe.
 */
function Poster() {
  return (
    <aside className="ph-auth-poster" aria-hidden={POSTER_ALT === "" ? true : undefined}>
      {POSTER_SRC ? (
        <img className="ph-auth-poster-img" src={POSTER_SRC} alt={POSTER_ALT} />
      ) : (
        <div className="ph-auth-poster-fallback">
          <span className="ph-auth-poster-mark">
            <Icons.brand size={132} sw={0.55} />
          </span>
          <div className="ph-auth-poster-text">
            <div className="ph-auth-poster-title">Pharos</div>
            <div className="ph-auth-poster-sub">照亮文献之海</div>
          </div>
        </div>
      )}
    </aside>
  );
}

/* ------------------------------------------------------- restoring a session */

function Restoring({
  error,
  onRetry,
  onSignOut,
}: {
  error: string | null;
  onRetry: () => void;
  onSignOut: () => void;
}): JSX.Element {
  if (error === null) {
    return (
      <div className="ph-auth-restoring">
        <span className="ph-auth-spin">
          <Icons.sync />
        </span>
        正在恢复会话…
      </div>
    );
  }
  return (
    <>
      <div className="ph-auth-alert">{error}</div>
      <div className="ph-auth-row">
        <button type="button" className="ph-auth-submit" onClick={onRetry}>
          重试
        </button>
        {/* Not a dead end: if the server is unreachable for good, the user can
            still drop the stored token and start over. */}
        <button type="button" className="ph-auth-ghost" onClick={onSignOut}>
          退出登录
        </button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------- form */

function SignInForm(): JSX.Element {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  /* Validation messages appear only after a submit attempt: flagging an empty
     field the instant it is focused reads as scolding. */
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  /* Three states, and they are not two: true = open, false = closed, null =
     unknown. Unknown has to behave like open — a self-hosted backend that
     predates GET /auth/status, or one that is momentarily unreachable, must not
     lose its sign-up form on an instance that is in fact accepting accounts.
     The 403 on submit remains the authority and can still turn this to false. */
  const [registrationOpen, setRegistrationOpen] = useState<boolean | null>(null);
  /* Separate from the answer, because `null` means both "still asking" and
     "asked, no answer". Without this the tab row would stay invisible forever
     against a backend that has no such endpoint. */
  const [probing, setProbing] = useState(true);

  const registrationClosed = registrationOpen === false;
  const isRegister = mode === "register";
  const trimmedEmail = email.trim();

  const emailError = !trimmedEmail
    ? "请输入邮箱"
    : !EMAIL_RE.test(trimmedEmail)
      ? "邮箱格式不正确"
      : null;

  // The length rule applies to registration only. Enforcing it on sign-in would
  // reject a short password locally while a wrong one fails at the server —
  // telling an attacker their guess was at least the right shape. The backend
  // declines to validate login passwords for exactly this reason; so do we.
  const passwordError = !password
    ? "请输入密码"
    : isRegister && password.length < MIN_PASSWORD
      ? `密码至少 ${MIN_PASSWORD} 个字符`
      : null;

  /* Asked once, up front, rather than discovered by making the user type a
     password and submit it — finding out that a form could never work only
     after filling it in is the worst of the three outcomes. */
  useEffect(() => {
    let cancelled = false;
    api.auth
      .status()
      .then((s) => {
        if (!cancelled) setRegistrationOpen(s.allow_registration);
      })
      .catch(() => {
        // Unknown, not closed. No console noise either: a backend without this
        // route is an ordinary state for this screen, not a fault.
        if (!cancelled) setRegistrationOpen(null);
      })
      .finally(() => {
        if (!cancelled) setProbing(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /* The tab row is invisible while the probe is in flight, so nobody can be on
     the register tab when a "closed" answer lands — but a 403 can also close it
     mid-session, and leaving the form on a mode that cannot submit is the kind
     of state that only shows up once. */
  useEffect(() => {
    if (registrationClosed) setMode("login");
  }, [registrationClosed]);

  const switchMode = (next: Mode) => {
    if (next === "register" && registrationClosed) return;
    setMode(next);
    setSubmitted(false);
    setServerError(null);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (emailError !== null || passwordError !== null) return;

    setPending(true);
    setServerError(null);
    try {
      if (isRegister) {
        const name = displayName.trim();
        await api.auth.register({
          email: trimmedEmail,
          password,
          ...(name ? { display_name: name } : {}),
        });
      } else {
        await api.auth.login({ email: trimmedEmail, password });
      }
      // No navigation here: `api.auth.*` stores the session, the store notifies,
      // and AuthGate re-renders to the app. One source of truth, one path in.
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 403 && isRegister) {
        // The authoritative answer, and the only one available when the probe
        // came back unknown. Hide the tab rather than leave a control that can
        // only ever fail; the effect above puts the user back on sign-in.
        setRegistrationOpen(false);
      }
      setServerError(errText(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="ph-auth-form" onSubmit={submit} noValidate>
      {/* Rendered but invisible while the probe is in flight, never removed: an
          absent row collapses the layout, so the panel would jump the moment
          the answer arrives — and offering the register tab only to retract it
          is worse than showing nothing for the length of one request.
          `visibility: hidden` also takes the buttons out of hit-testing and the
          tab order, so there is nothing to click or tab into meanwhile. */}
      {!registrationClosed && (
        <div className={cx("ph-auth-modes", probing && "ph-auth-modes--probing")}>
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => switchMode(m)}
              className={cx("ph-auth-mode", mode === m && "ph-auth-mode--on")}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>
      )}

      {registrationClosed && (
        <div className="ph-auth-note">本实例已关闭注册 · 请使用已有账户登录</div>
      )}

      <label className="ph-auth-label" htmlFor="ph-auth-email">
        邮箱
      </label>
      <input
        id="ph-auth-email"
        className="ph-auth-input"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@example.com"
        autoComplete="username"
        autoCapitalize="none"
        spellCheck={false}
        maxLength={320}
        autoFocus
        aria-invalid={submitted && emailError !== null}
      />
      {submitted && emailError && <div className="ph-auth-err">{emailError}</div>}

      <label className="ph-auth-label" htmlFor="ph-auth-password">
        密码
      </label>
      <input
        id="ph-auth-password"
        className="ph-auth-input"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder={isRegister ? `至少 ${MIN_PASSWORD} 个字符` : "••••••••"}
        autoComplete={isRegister ? "new-password" : "current-password"}
        maxLength={1024}
        aria-invalid={submitted && passwordError !== null}
      />
      {submitted && passwordError && <div className="ph-auth-err">{passwordError}</div>}

      {isRegister && (
        <>
          <label className="ph-auth-label" htmlFor="ph-auth-name">
            显示名称 <span className="ph-auth-label-sub">· 可选</span>
          </label>
          <input
            id="ph-auth-name"
            className="ph-auth-input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="留空则使用邮箱"
            autoComplete="nickname"
            maxLength={128}
          />
        </>
      )}

      {/* The server's own words. Mapping them to friendlier copy would mean
          guessing which failure occurred, and guessing wrong on a 409 or a
          rate-limit leaves the user with no idea what to change. */}
      {serverError && <div className="ph-auth-alert">{serverError}</div>}

      <button type="submit" className="ph-auth-submit" disabled={pending}>
        {pending ? (isRegister ? "创建中…" : "登录中…") : isRegister ? "创建账户" : "登录"}
      </button>

      {isRegister && (
        <div className="ph-auth-note">
          注册即拥有独立文库 · 你的论文与翻译只有你能看到
        </div>
      )}
    </form>
  );
}
