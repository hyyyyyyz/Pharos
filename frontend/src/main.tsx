import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AuthGate } from "./auth/AuthGate";
import { ApiError } from "./api/client";
import { getSession, subscribe as subscribeSession } from "./auth/session";
import "./styles/global.css";
import "./styles/tablet.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 5_000,
      // Never retry a 401. The token is already gone by the time this runs
      // (client.ts clears it), so a retry is a guaranteed second failure that
      // only delays the gate showing sign-in.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status === 401 ? false : failureCount < 1,
    },
  },
});

/**
 * Signing out empties the cache — every path, not just the button.
 *
 * `SettingsModal` clears it when the user clicks 退出登录, but that is the one
 * sign-out that is *not* dangerous to miss. The ones that matter are the ones
 * nobody clicks: a 401 handled inside `client.ts`, an expired token dropped by
 * `getToken`, another tab signing out. Those leave a full react-query cache of
 * the previous user's library sitting in memory, and the next person to sign in
 * on this browser sees it painted for them before any refetch can correct it —
 * a stale render, but of someone else's papers.
 *
 * Subscribing to the session covers all of them at once, since every one of
 * those paths ends in `clearSession()`.
 */
let hadToken = getSession().token !== null;
subscribeSession(() => {
  const hasToken = getSession().token !== null;
  if (hadToken && !hasToken) queryClient.clear();
  hadToken = hasToken;
});

/**
 * Service worker: installable on an Android tablet's home screen and able to
 * repaint the shell with no network.
 *
 * `sw.js` deliberately never touches `/api/` — every Pharos answer is
 * owner-scoped and session-shaped, and a cached paper list served after a
 * sign-out would show one user another's papers, the exact failure the
 * `queryClient.clear()` subscription above exists to prevent. The worker only
 * caches the static shell (hashed assets + `index.html`), which is the part
 * that makes an installed PWA open instead of failing with a dinosaur.
 *
 * Dev never registers: Vite's module graph invalidates far faster than a SW's
 * update cycle, and a stale shell serving last week's modules is a bug no
 * developer can see past. Prod only, and a failed registration is swallowed —
 * the app must run where SWs are disabled (private mode, old WebView).
 */
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  // Base-aware: the GitHub Pages "pages" build lives under /Pharos/, and a
  // worker registered at the wrong scope silently covers nothing.
  const swUrl = `${import.meta.env.BASE_URL}sw.js`;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(swUrl).catch(() => {
      /* offline shell is a progressive enhancement, never a dependency */
    });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* Inside the provider, not outside: the gate itself makes an authenticated
        request (GET /auth/me), and SettingsModal's account tab uses queries that
        must share this cache. */}
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <App />
      </AuthGate>
    </QueryClientProvider>
  </React.StrictMode>,
);
