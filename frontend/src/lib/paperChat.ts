import { webChat, webChatError } from "./webChat";

/**
 * The one transport left.
 *
 * This module used to dispatch between a native desktop client and the web one.
 * The desktop client is now built from Zotero source and does not run this
 * bundle, so every conversation goes through the Pharos backend. Kept as a named
 * seam rather than importing `webChat` everywhere: the panel and the settings
 * pane speak to "the chat backend", not to a specific HTTP client.
 */
export const paperChat = webChat;

export const paperChatAvailable = (): boolean =>
  typeof window !== "undefined" && typeof window.fetch === "function";

export const paperChatError = webChatError;

export type {
  ChatEvent,
  ChatMessage,
  ConversationDetail,
  ConversationSummary,
  DocumentRef,
  PaperContextStatus,
  ProviderSaveRequest,
  ProviderStatus,
} from "./webChat";
