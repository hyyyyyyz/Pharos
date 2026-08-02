import type { AdminProviders } from "../api/types";

/** Whether translation is silently running on the free fallback engine.
 *
 * The backend's `translator_config()` returns Bing whenever an LLM translator is
 * selected but has no usable credentials, so translation keeps working and
 * quietly gets worse. That fallback is the ONLY way a non-Bing selection yields
 * an effective engine of "bing", which makes this test exact rather than
 * heuristic.
 *
 * Deliberately NOT `effective_translator !== translator`, which is what this
 * used to be and which warned constantly: for `openai` and `custom` the
 * effective type is the engine's name for the wire format,
 * `openai_compatible`, so it never equals the provider name and the banner
 * claimed a degradation that had not happened. A warning that is always on is
 * worse than no warning — it trains the operator to ignore the real one.
 *
 * Kept identical to the desktop client's
 * `Zotero.Pharos.Admin.isTranslationDegraded`. Two surfaces disagreeing about
 * whether the service is healthy is its own bug.
 */
export function isTranslationDegraded(d: AdminProviders | undefined): boolean {
  if (!d) return false;
  const configured = (d.translator || "").toLowerCase();
  if (configured === "bing" || configured === "google") return false;
  return (d.effective_translator || "").toLowerCase() === "bing";
}
