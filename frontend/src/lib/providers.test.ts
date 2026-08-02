/**
 * The web client had no test runner at all, and this predicate is why it needed
 * one: the admin console shipped with `effective_translator !== translator`,
 * which is permanently true for the relay providers, so operators saw a
 * "translation degraded" banner that never went away. Nothing but a build ran
 * over this file, and a build cannot tell a wrong boolean from a right one.
 *
 * The desktop client pins the same cases in
 * `client/test/tests/pharosAdminTest.js`. Keep the two in step: the whole point
 * of the shared logic is that both surfaces answer "is translation healthy?"
 * identically.
 */
import { describe, expect, it } from "vitest";

import type { AdminProviders } from "../api/types";
import { isTranslationDegraded } from "./providers";

/** Only two fields matter here; the rest of `ProvidersOut` is noise. */
const providers = (translator: string, effective: string): AdminProviders => ({
  providers: [],
  translator,
  chat_provider: "openai",
  effective_translator: effective,
});

describe("isTranslationDegraded", () => {
  it("stays quiet when the configured engine is the one running", () => {
    expect(isTranslationDegraded(providers("deepseek", "deepseek"))).toBe(false);
  });

  it("stays quiet when a free engine was the deliberate choice", () => {
    // Bing and Google need no credential, so neither can be the missing-key
    // fallback. Choosing one is a decision, not a degradation — including the
    // case where the effective engine differs from what was asked for.
    expect(isTranslationDegraded(providers("bing", "bing"))).toBe(false);
    expect(isTranslationDegraded(providers("google", "google"))).toBe(false);
    expect(isTranslationDegraded(providers("google", "bing"))).toBe(false);
  });

  it("stays quiet for a relay, whose effective name is the wire format", () => {
    // THE REGRESSION. `translator_config()` reports "openai_compatible" for
    // both relay providers — the engine's name for the wire format, never the
    // provider's name — so a plain inequality warns about a fallback that has
    // not happened, on every page load, forever.
    expect(isTranslationDegraded(providers("openai", "openai_compatible"))).toBe(false);
    expect(isTranslationDegraded(providers("custom", "openai_compatible"))).toBe(false);
  });

  it("warns when an LLM translator fell back to the free engine", () => {
    // An effective engine of "bing" under a non-Bing selection has exactly one
    // cause: the credentials are missing and the backend fell back.
    expect(isTranslationDegraded(providers("openai", "bing"))).toBe(true);
    expect(isTranslationDegraded(providers("deepseek", "bing"))).toBe(true);
    expect(isTranslationDegraded(providers("custom", "bing"))).toBe(true);
  });

  it("says nothing before the server has answered", () => {
    expect(isTranslationDegraded(undefined)).toBe(false);
  });

  it("compares engine names case-insensitively", () => {
    expect(isTranslationDegraded(providers("BING", "Bing"))).toBe(false);
    expect(isTranslationDegraded(providers("OpenAI", "OPENAI_COMPATIBLE"))).toBe(false);
    expect(isTranslationDegraded(providers("OpenAI", "Bing"))).toBe(true);
  });

  it("treats a missing engine name as healthy rather than degraded", () => {
    // A server too old to send the field must not produce a banner; silence is
    // the only honest answer when the backend has not told us anything.
    expect(isTranslationDegraded(providers("openai", ""))).toBe(false);
  });
});
