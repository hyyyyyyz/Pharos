export declare const PHAROS_FAKE_PROVIDER = "pharos-fake"
export declare const PHAROS_FAKE_MODEL = "pharos-fake-canary"
export declare const name = "pharos-fake-dsh"
export declare const inject: readonly ["llm"]
export declare const CANARY_TEXT = '{"ok":true,"workflow":"harness.canary","step":"actor_turn"}'
import type { LlmAdapter } from '@deepseek-ai/dsh-llm'
import type { Context } from '@deepseek-ai/cordis'
export declare class PharosFakeAdapter extends LlmAdapter {}
export declare function apply(ctx: Context): void
