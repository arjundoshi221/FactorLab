import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
    this.name = "ApiError";
  }
}

/** Fetch JSON; non-2xx responses become an ApiError carrying the API's safe ``detail``. */
export async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...init, headers: { Accept: "application/json", ...(init.headers ?? {}) } });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep the generic message when the body is not JSON.
    }
    throw new ApiError(response.status, detail);
  }
  return await response.json() as T;
}

export function errorMessage(error: unknown, fallback = "Live data is temporarily unavailable."): string {
  return error instanceof ApiError ? error.detail : fallback;
}

export interface Remote<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  refreshing: boolean;
  refresh: () => void;
}

/**
 * Load ``url`` (skipped when null), optionally re-polling while the tab is visible.
 * The last good response stays visible when a refresh fails.
 */
export function useRemote<T>(url: string | null, pollMs = 0): Remote<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(Boolean(url));
  const [refreshing, setRefreshing] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const lastUrl = useRef<string | null>(null);

  const load = useCallback(async (manual: boolean) => {
    if (!url) return;
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    if (lastUrl.current !== url) {
      lastUrl.current = url;
      setLoading(true);
    }
    if (manual) setRefreshing(true);
    try {
      setData(await fetchJson<T>(url, { signal: next.signal }));
      setError(null);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") setError(caught);
    } finally {
      if (!next.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [url]);

  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }
    void load(false);
    if (!pollMs) return () => controller.current?.abort();
    const interval = window.setInterval(() => { if (!document.hidden) void load(false); }, pollMs);
    const onVisibility = () => { if (!document.hidden) void load(false); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      controller.current?.abort();
    };
  }, [url, pollMs, load]);

  const refresh = useCallback(() => { void load(true); }, [load]);
  return { data, error, loading, refreshing, refresh };
}
