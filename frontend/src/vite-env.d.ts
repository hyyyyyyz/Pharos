/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend API base. Defaults to same-origin "/api"; set it when the API is
   *  served from another origin. */
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
