/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Xuanzang backend API. Dev: "/api" (proxied). Prod: full URL. */
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
