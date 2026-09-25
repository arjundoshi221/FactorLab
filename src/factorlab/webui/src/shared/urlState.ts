import { useCallback, useEffect, useState } from "react";

/** Read and update the page's query string without a router. */
export function useUrlParams(): [URLSearchParams, (update: (params: URLSearchParams) => void, push?: boolean) => void] {
  const [search, setSearch] = useState(() => window.location.search);

  useEffect(() => {
    const onPop = () => setSearch(window.location.search);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const update = useCallback((change: (params: URLSearchParams) => void, push = false) => {
    const params = new URLSearchParams(window.location.search);
    change(params);
    const query = params.toString();
    const next = `${window.location.pathname}${query ? `?${query}` : ""}`;
    if (push) window.history.pushState({}, "", next);
    else window.history.replaceState({}, "", next);
    setSearch(query ? `?${query}` : "");
  }, []);

  return [new URLSearchParams(search), update];
}
