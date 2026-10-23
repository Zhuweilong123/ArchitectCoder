/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional Bearer token for backend API auth (mirrors backend INTERNAL_API_TOKEN). */
  readonly VITE_API_TOKEN?: string;
  readonly VITE_USERPROFILE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
