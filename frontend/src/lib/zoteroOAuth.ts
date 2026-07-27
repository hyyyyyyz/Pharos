import { isTauri } from "@tauri-apps/api/core";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { getCurrent, onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { openUrl } from "@tauri-apps/plugin-opener";

import type { ZoteroDesktopOAuthStart } from "../api/types";

export type ZoteroOAuthResult =
  | "connected"
  | "cancelled"
  | "expired"
  | "invalid"
  | "busy"
  | "error";

export type DesktopOAuthCallback =
  | { code: string; result: null }
  | { code: null; result: Exclude<ZoteroOAuthResult, "connected"> };

interface StoredAttempt {
  desktopSecret: string;
  expiresAt: string;
  pendingCode: string | null;
}

const STORAGE_KEY = "pharos-zotero-desktop-oauth-v1";
const RESULT_CODES = new Set<Exclude<ZoteroOAuthResult, "connected">>([
  "cancelled",
  "expired",
  "invalid",
  "busy",
  "error",
]);
const OPAQUE_CODE = /^[A-Za-z0-9_-]{32,256}$/;

function readAttempt(): StoredAttempt | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredAttempt>;
    if (
      typeof parsed.desktopSecret !== "string" ||
      !OPAQUE_CODE.test(parsed.desktopSecret) ||
      typeof parsed.expiresAt !== "string" ||
      (parsed.pendingCode !== null &&
        parsed.pendingCode !== undefined &&
        (typeof parsed.pendingCode !== "string" || !OPAQUE_CODE.test(parsed.pendingCode)))
    ) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    const expiresAt = new Date(parsed.expiresAt).getTime();
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return {
      desktopSecret: parsed.desktopSecret,
      expiresAt: parsed.expiresAt,
      pendingCode: parsed.pendingCode ?? null,
    };
  } catch {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage may be unavailable */
    }
    return null;
  }
}

function writeAttempt(attempt: StoredAttempt): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(attempt));
}

function validateAuthorizeUrl(raw: string): string {
  const url = new URL(raw);
  if (
    url.protocol !== "https:" ||
    url.hostname !== "www.zotero.org" ||
    url.pathname !== "/oauth/authorize" ||
    url.username !== "" ||
    url.password !== ""
  ) {
    throw new Error("服务器返回了无效的 Zotero 授权地址。");
  }
  return url.toString();
}

export const desktopZoteroOAuth = {
  available: (): boolean => isTauri(),

  start: async (flow: ZoteroDesktopOAuthStart): Promise<void> => {
    if (!isTauri()) throw new Error("桌面 OAuth 只能在 Pharos 客户端中使用。");
    if (!OPAQUE_CODE.test(flow.desktop_secret)) {
      throw new Error("服务器返回了无效的桌面授权状态。");
    }
    const expiresAt = new Date(flow.expires_at).getTime();
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
      throw new Error("Zotero 授权状态已过期，请重新发起。");
    }
    writeAttempt({
      desktopSecret: flow.desktop_secret,
      expiresAt: flow.expires_at,
      pendingCode: null,
    });
    try {
      await openUrl(validateAuthorizeUrl(flow.authorize_url));
    } catch (error) {
      localStorage.removeItem(STORAGE_KEY);
      throw error;
    }
  },

  parseCallback: (raw: string): DesktopOAuthCallback | null => {
    let url: URL;
    try {
      url = new URL(raw);
    } catch {
      return null;
    }
    if (
      url.protocol !== "pharos:" ||
      url.hostname !== "oauth" ||
      url.pathname !== "/zotero" ||
      url.port !== "" ||
      url.username !== "" ||
      url.password !== "" ||
      url.hash !== ""
    ) {
      return null;
    }
    const codes = url.searchParams.getAll("code");
    const results = url.searchParams.getAll("result");
    if (codes.length === 1 && results.length === 0 && OPAQUE_CODE.test(codes[0]!)) {
      return { code: codes[0]!, result: null };
    }
    if (
      results.length === 1 &&
      codes.length === 0 &&
      RESULT_CODES.has(results[0] as Exclude<ZoteroOAuthResult, "connected">)
    ) {
      return {
        code: null,
        result: results[0] as Exclude<ZoteroOAuthResult, "connected">,
      };
    }
    return null;
  },

  rememberCode: (code: string): boolean => {
    if (!OPAQUE_CODE.test(code)) return false;
    const attempt = readAttempt();
    if (!attempt) return false;
    writeAttempt({ ...attempt, pendingCode: code });
    return true;
  },

  pending: (): { code: string; desktopSecret: string } | null => {
    const attempt = readAttempt();
    if (!attempt?.pendingCode) return null;
    return { code: attempt.pendingCode, desktopSecret: attempt.desktopSecret };
  },

  clear: (): void => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage may be unavailable */
    }
  },

  listen: async (listener: (urls: string[]) => void): Promise<UnlistenFn> => {
    if (!isTauri()) return () => undefined;
    const unlisten = await onOpenUrl((urls) => listener(urls.map(String)));
    const current = await getCurrent();
    if (current?.length) listener(current.map(String));
    return unlisten;
  },
};
