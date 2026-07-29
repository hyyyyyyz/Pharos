import { isTauri } from "@tauri-apps/api/core";
import { desktopChat, desktopError } from "./desktopChat";
import { webChat, webChatError } from "./webChat";

const desktop = isTauri();

/** One UI, two durable transports: local Workspace on desktop, FastAPI on web. */
export const paperChat = desktop ? desktopChat : webChat;

export const paperChatAvailable = (): boolean =>
  desktop || (typeof window !== "undefined" && typeof window.fetch === "function");

/** Only the native transport needs pdf.js to send the extracted text to Rust. */
export const paperChatNeedsClientContext = (): boolean => desktop;

/** Browser sandboxing prevents ~/.codex discovery and spawning `codex exec`. */
export const paperChatNativeCodexAvailable = (): boolean => desktop;

export const paperChatIsDesktop = (): boolean => desktop;

export const paperChatError = (error: unknown): string =>
  desktop ? desktopError(error) : webChatError(error);

export type {
  ChatEvent,
  ChatMessage,
  CodexCapabilities,
  CodexHandoffResult,
  CodexSessionSummary,
  ConversationDetail,
  ConversationSummary,
  DocumentContext,
  DocumentRef,
  PaperContextStatus,
  ProviderSaveRequest,
  ProviderStatus,
  WorkspaceRelocateResult,
  WorkspaceStatus,
} from "./desktopChat";
