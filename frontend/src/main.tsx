import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AuthGate } from "./auth/AuthGate";
import { ApiError } from "./api/client";
import { getSession, subscribe as subscribeSession } from "./auth/session";
import "./styles/global.css";

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
