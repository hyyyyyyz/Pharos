/**
 * Shared vocabulary for literature results.
 *
 * 文献探索 and 研究项目 render the same `LiteratureResult` in two places, and
 * the two used to answer "where did this reading come from" differently — one
 * of them by printing the raw model id where the analysis mode belonged. These
 * helpers exist so provenance has exactly one implementation, the way the
 * desktop keeps its equivalents on `Zotero.Pharos.Discovery`.
 */

/** Wire id → the name the project writes for itself. */
const SOURCE_NAMES: Record<string, string> = {
  arxiv: "arXiv",
  openalex: "OpenAlex",
};

/**
 * A retrieval source's display name.
 *
 * Never print the bare wire id: neither project spells its own name
 * `arxiv` or `openalex`, and an id shown to a reader looks like a
 * database leak rather than a citation. An id with no mapping is returned
 * unchanged — a source this build has not heard of still has to be nameable.
 */
export function sourceName(source: string): string {
  return SOURCE_NAMES[source.toLowerCase()] ?? source;
}

export function sourceList(sources: readonly string[]): string {
  return sources.length === 0 ? "来源未知" : sources.map(sourceName).join("、");
}

/**
 * Whether this result is a rules extraction rather than a model reading.
 *
 * `!== "llm"`, deliberately, and never `=== "rules"`: a mode this build does
 * not know, a missing field, or a half-built object all have to read as rules.
 * The failure in that direction is a cautious label on a real AI reading; the
 * failure in the other direction is an English sentence cut out of an abstract
 * presented as a model's Chinese analysis. The union type says only "rules" and
 * "llm" are possible, but the type describes what the backend means to send,
 * not what arrives.
 */
export function isRulesAnalysis(result: { analysis_mode: string }): boolean {
  return (result.analysis_mode ?? "").toLowerCase() !== "llm";
}
