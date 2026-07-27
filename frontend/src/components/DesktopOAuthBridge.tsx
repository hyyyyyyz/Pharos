import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import { desktopZoteroOAuth } from "../lib/zoteroOAuth";
import { useUI } from "../store";

/** Receives `pharos://` callbacks even when the settings dialog is closed. */
export function DesktopOAuthBridge(): null {
  const qc = useQueryClient();
  const openSettings = useUI((state) => state.openSettings);
  const setZoteroOAuthResult = useUI((state) => state.setZoteroOAuthResult);

  useEffect(() => {
    if (!desktopZoteroOAuth.available()) return;
    let disposed = false;
    let completing = false;
    const seen = new Set<string>();

    const finishPending = async (): Promise<void> => {
      if (disposed || completing) return;
      const pending = desktopZoteroOAuth.pending();
      if (!pending) return;
      completing = true;
      try {
        const status = await api.zotero.oauthDesktopFinish({
          code: pending.code,
          desktop_secret: pending.desktopSecret,
        });
        if (disposed) return;
        desktopZoteroOAuth.clear();
        qc.setQueryData(["zotero", "status"], status);
        void qc.invalidateQueries({ queryKey: ["papers"] });
        setZoteroOAuthResult("connected");
        openSettings("account");
      } catch (error) {
        if (disposed) return;
        // If the response was lost after the server stored the link, a retry of
        // the one-use handoff returns 400. Status is the durable source of truth.
        try {
          const status = await api.zotero.status();
          if (status.linked) {
            desktopZoteroOAuth.clear();
            qc.setQueryData(["zotero", "status"], status);
            void qc.invalidateQueries({ queryKey: ["papers"] });
            setZoteroOAuthResult("connected");
            openSettings("account");
            return;
          }
        } catch {
          /* preserve the original failure below */
        }
        if (error instanceof ApiError && error.status === 400) {
          desktopZoteroOAuth.clear();
          setZoteroOAuthResult(
            error.message.toLowerCase().includes("expired") ? "expired" : "invalid",
          );
        } else {
          setZoteroOAuthResult("error");
        }
        openSettings("account");
      } finally {
        completing = false;
      }
    };

    const receive = (urls: string[]): void => {
      for (const raw of urls) {
        if (seen.has(raw)) continue;
        seen.add(raw);
        const callback = desktopZoteroOAuth.parseCallback(raw);
        if (!callback) continue;
        if (callback.code) {
          if (!desktopZoteroOAuth.rememberCode(callback.code)) {
            setZoteroOAuthResult("expired");
            openSettings("account");
            continue;
          }
          void finishPending();
        } else if (callback.result) {
          desktopZoteroOAuth.clear();
          setZoteroOAuthResult(callback.result);
          openSettings("account");
        }
      }
    };

    let unlisten: (() => void) | undefined;
    void desktopZoteroOAuth
      .listen(receive)
      .then((stop) => {
        if (disposed) stop();
        else unlisten = stop;
      })
      .catch(() => {
        if (!disposed) setZoteroOAuthResult("error");
      });
    void finishPending();

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [openSettings, qc, setZoteroOAuthResult]);

  return null;
}
