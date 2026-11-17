import type { Context } from '@deepseek-ai/cordis'
import { LlmAdapter, type GenerateOptions, type LlmModelInfo, type LlmResolvedModelInfo, type StreamChunk } from '@deepseek-ai/dsh-llm'

/** The only route and model exposed by the CI-only adapter. */
export const PHAROS_FAKE_PROVIDER = 'pharos-fake'
export const PHAROS_FAKE_MODEL = 'pharos-fake-canary'
export const name = 'pharos-fake-dsh'
export const inject = ['llm'] as const

/**
 * This is deliberately a literal, not a prompt-derived response.  The
 * Pharos canary validates this exact typed object before creating an Artifact.
 * Keeping it constant makes a process, machine and retry produce the same
 * bytes without a clock, random source, environment or external service.
 */
export const CANARY_TEXT = '{"ok":true,"workflow":"harness.canary","step":"actor_turn"}'
const CANARY_INPUT_TOKENS = 8
const CANARY_OUTPUT_TOKENS = 7

export class PharosFakeAdapter extends LlmAdapter {
  override providerInfo(provider: string) {
    assertRoute(provider, PHAROS_FAKE_MODEL)
    return { id: provider, name: 'Pharos deterministic canary' }
  }

  override listModels(provider: string): Promise<readonly LlmModelInfo[]> {
    assertRoute(provider, PHAROS_FAKE_MODEL)
    return Promise.resolve([{ provider, id: PHAROS_FAKE_MODEL, name: PHAROS_FAKE_MODEL }])
  }

  override resolveModel(provider: string, model: string): Promise<LlmResolvedModelInfo> {
    assertRoute(provider, model)
    return Promise.resolve({ provider, id: model, name: model, defaultMaxTokens: 128 })
  }

  override async * stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    assertRoute(options.provider, options.model)
    if (options.tools !== undefined && options.tools.length !== 0) {
      throw new Error('pharos-fake does not support model-facing tools')
    }
    if (options.signal?.aborted) throw new Error('pharos-fake call aborted')

    yield { type: 'block-start', index: 0, blockType: 'text' }
    // One delta keeps the canary byte-for-byte deterministic and still uses
    // the official provider-neutral stream vocabulary.
    if (options.signal?.aborted) throw new Error('pharos-fake call aborted')
    yield { type: 'text-delta', index: 0, text: CANARY_TEXT }
    yield { type: 'block-end', index: 0, block: { type: 'text', text: CANARY_TEXT } }
    yield {
      type: 'usage',
      usage: { inputTokens: CANARY_INPUT_TOKENS, outputTokens: CANARY_OUTPUT_TOKENS },
    }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}

/** Cordis entry point referenced by the out-of-tree bundle patch. */
export function apply(ctx: Context): void {
  ctx.llm.registerAdapter([PHAROS_FAKE_PROVIDER], new PharosFakeAdapter())
}

function assertRoute(provider: string, model: string): void {
  if (provider !== PHAROS_FAKE_PROVIDER || model !== PHAROS_FAKE_MODEL) {
    throw new Error('pharos-fake received an unapproved provider or model')
  }
}
