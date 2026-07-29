/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional Bearer token for backend API auth (mirrors backend INTERNAL_API_TOKEN). */
  readonly VITE_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
