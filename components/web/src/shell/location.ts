import { useEffect, useState } from "react";

const EVENT = "factorlab:locationchange";
let patched = false;

/** Pages update the query string with pushState/replaceState, which fire no event; announce them so the shell's tabs follow. */
function patchHistory() {
  if (patched) return;
  patched = true;
  for (const method of ["pushState", "replaceState"] as const) {
    const original = window.history[method].bind(window.history);
    window.history[method] = (...args: Parameters<History["pushState"]>) => {
      original(...args);
      window.dispatchEvent(new Event(EVENT));
    };
  }
}

export function useLocation(): { path: string; search: URLSearchParams } {
  const [href, setHref] = useState(() => window.location.pathname + window.location.search);
  useEffect(() => {
    patchHistory();
    const sync = () => setHref(window.location.pathname + window.location.search);
    window.addEventListener(EVENT, sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);
  const url = new URL(href, window.location.origin);
  return { path: url.pathname, search: url.searchParams };
}
