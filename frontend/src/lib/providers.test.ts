import { describe, expect, it } from "vitest";

import type { AdminProviders } from "../api/types";
import { isTranslationDegraded } from "./providers";

/** Only the two fields the predicate reads; the provider list is irrelevant. */
const providers = (translator: string, effective: string): AdminProviders => ({
  providers: [],
  translator,
  chat_provider: "deepseek",
  effective_translator: effective,
});

describe("isTranslationDegraded", () => {
  it("is false for the free engines, which cannot fall back to anything", () => {
    // Bing selected and Bing in force is the configuration working, not a
    // fallback — the predicate must not read its own target as a symptom.
    expect(isTranslationDegraded(providers("bing", "bing"))).toBe(false);
    expect(isTranslationDegraded(providers("google", "google"))).toBe(false);
  });

  it("is false for openai, whose effective type is never its own name", () => {
    // THE REGRESSION THIS FILE EXISTS FOR. `translator_config()` reports the
    // wire format (`openai_compatible`), never the provider name, so the old
    // `effective !== translator` test was permanently true here and the admin
    // console carried a degradation warning that had not happened. If this
    // ever goes red because the predicate was "simplified" back to an equality
    // check, that is the bug, not the test.
    expect(isTranslationDegraded(providers("openai", "openai_compatible"))).toBe(false);
    expect(isTranslationDegraded(providers("custom", "openai_compatible"))).toBe(false);
  });

  it("is false for deepseek, whose effective type does happen to match", () => {
    expect(isTranslationDegraded(providers("deepseek", "deepseek"))).toBe(false);
  });

  it("is true only when an LLM selection actually fell back to Bing", () => {
    expect(isTranslationDegraded(providers("openai", "bing"))).toBe(true);
    expect(isTranslationDegraded(providers("deepseek", "bing"))).toBe(true);
    expect(isTranslationDegraded(providers("custom", "bing"))).toBe(true);
  });

  it("is false before the providers query has answered", () => {
    // AdminView passes `providers.data`, which is undefined while loading. A
    // warning banner during the first render would be a claim made with no
    // data behind it.
    expect(isTranslationDegraded(undefined)).toBe(false);
  });

  it("compares case-insensitively, because the backend lower-cases and the env does not", () => {
    // `translator` echoes PHAROS_TRANSLATOR_TYPE as written in .env, while
    // `effective_translator` comes back from `translator_config()` already
    // lower-cased. A case-sensitive test would miss the fallback for anyone
    // who wrote `OpenAI` in their environment file.
    expect(isTranslationDegraded(providers("OpenAI", "BING"))).toBe(true);
    expect(isTranslationDegraded(providers("Bing", "bing"))).toBe(false);
    expect(isTranslationDegraded(providers("OPENAI", "OpenAI_Compatible"))).toBe(false);
  });

  it("is false when either field is missing rather than warning on absence", () => {
    expect(isTranslationDegraded(providers("", ""))).toBe(false);
    expect(isTranslationDegraded(providers("openai", ""))).toBe(false);
  });
});
