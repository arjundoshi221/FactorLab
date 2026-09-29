/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Stamped by components/web/Dockerfile from the release version; unset in dev and tests. */
  readonly VITE_FACTORLAB_VERSION?: string;
  readonly VITE_FACTORLAB_COMMIT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
