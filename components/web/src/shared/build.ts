/** The web bundle's own version, baked in by the web image build (null in dev, tests and
 * the monolith, whose API serves an unversioned build). */
export const WEB_VERSION: string | null = (() => {
  const value = import.meta.env.VITE_FACTORLAB_VERSION;
  return value && value !== "0.0.0-dev" ? value : null;
})();
