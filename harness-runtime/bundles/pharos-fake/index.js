import { LlmAdapter } from '@deepseek-ai/dsh-llm'

export const PHAROS_FAKE_PROVIDER = 'pharos-fake'
export const PHAROS_FAKE_MODEL = 'pharos-fake-canary'
export const name = 'pharos-fake-dsh'
export const inject = ['llm']
export const CANARY_TEXT = '{"ok":true,"workflow":"harness.canary","step":"actor_turn"}'
const CANARY_INPUT_TOKENS = 8
const CANARY_OUTPUT_TOKENS = 7

export class PharosFakeAdapter extends LlmAdapter {
  providerInfo(provider) {
    assertRoute(provider, PHAROS_FAKE_MODEL)
    return { id: provider, name: 'Pharos deterministic canary' }
  }

  listModels(provider) {
    assertRoute(provider, PHAROS_FAKE_MODEL)
    return Promise.resolve([{ provider, id: PHAROS_FAKE_MODEL, name: PHAROS_FAKE_MODEL }])
  }

  resolveModel(provider, model) {
    assertRoute(provider, model)
    return Promise.resolve({ provider, id: model, name: model, defaultMaxTokens: 128 })
  }

  async * stream(options) {
    assertRoute(options.provider, options.model)
    if (options.tools !== undefined && options.tools.length !== 0) {
      throw new Error('pharos-fake does not support model-facing tools')
    }
    if (options.signal?.aborted) throw new Error('pharos-fake call aborted')
    yield { type: 'block-start', index: 0, blockType: 'text' }
    if (options.signal?.aborted) throw new Error('pharos-fake call aborted')
    yield { type: 'text-delta', index: 0, text: CANARY_TEXT }
    yield { type: 'block-end', index: 0, block: { type: 'text', text: CANARY_TEXT } }
    yield { type: 'usage', usage: { inputTokens: CANARY_INPUT_TOKENS, outputTokens: CANARY_OUTPUT_TOKENS } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}

export function apply(ctx) {
  ctx.llm.registerAdapter([PHAROS_FAKE_PROVIDER], new PharosFakeAdapter())
}

function assertRoute(provider, model) {
  if (provider !== PHAROS_FAKE_PROVIDER || model !== PHAROS_FAKE_MODEL) {
    throw new Error('pharos-fake received an unapproved provider or model')
  }
}
