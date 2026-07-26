/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend API base. Web/dev default to same-origin "/api"; desktop may override it. */
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
