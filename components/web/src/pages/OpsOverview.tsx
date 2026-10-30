import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { USDashboardPanel } from "../components/us/USDashboardPanel";
import { PageHeader, Panel, StatusPill, type HubStatus } from "../components/ui";
import { Gauge } from "../shared/charts";
import { WEB_VERSION } from "../shared/build";
import { formatBytes, formatCompact, formatDate, formatTime } from "../shared/format";

export interface HubTable {
  name: string;
  domain: string;
  category: string;
  engine: string;
  kind?: "table" | "view";
  stored_rows: number;
  bytes_on_disk: number;
  count_kind: "stored";
  first_data_at: string | null;
  last_data_at: string | null;
  last_ingested_at: string | null;
  today_rows: number | null;
  today_status: HubStatus;
  status_reason: string;
}

export interface HubOverview {
  generated_at: string;
  build?: { release_id: string | null; commit: string | null; component?: string | null; version?: string | null } | null;
  summary: {
    database: string;
    table_count: number;
    view_count?: number;
    populated_tables: number;
    stored_rows: number;
    bytes_on_disk: number;
    healthy_tables: number;
    attention_tables: number;
  };
  india: {
    trading_date: string;
    market_status: string;
    expected_series: number;
    data_points: number;
    expected_data_points: number;
    coverage_percent: number;
    last_ingested_at: string | null;
    freshness_seconds: number | null;
    status: HubStatus;
    status_reason: string;
  };
  political: {
    checked_at: string;
    datasets_with_data: number;
    fresh_datasets: number;
    stale_datasets: number;
    last_ingested_at: string | null;
    status: HubStatus;
    status_reason: string;
  };
  tables: HubTable[];
  warnings: string[];
}

type SortKey = "name" | "domain" | "stored_rows" | "bytes_on_disk" | "today_status";
type StatusFilter = "all" | "attention" | "healthy" | "not_configured" | "not_expected";

const statusFilters: Record<StatusFilter, { label: string; matches: (status: HubStatus) => boolean }> = {
  all: { label: "All statuses", matches: () => true },
  attention: { label: "Needs attention", matches: (status) => ["attention", "missing", "unknown"].includes(status) },
  healthy: { label: "Healthy", matches: (status) => status === "healthy" },
  not_configured: { label: "No producer yet", matches: (status) => status === "not_configured" },
  not_expected: { label: "No daily rule", matches: (status) => status === "not_expected" },
};

const numberFormatter = new Intl.NumberFormat("en-IN");

/** The overview snapshot, polled every minute while visible; a failed refresh keeps the last snapshot. */
export function useOverview(pollMs = 60_000) {
  const [data, setData] = useState<HubOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(async (manual = false) => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    if (manual) setRefreshing(true);
    try {
      const response = await fetch("/hub/api/v1/overview", {
        signal: nextController.signal,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
      setData((await response.json()) as HubOverview);
      setError(null);
    } catch (requestError) {
      if ((requestError as Error).name !== "AbortError") {
        setError("Live data is temporarily unavailable. The last successful snapshot remains visible.");
      }
    } finally {
      if (!nextController.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, pollMs);
    const onVisibility = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      controller.current?.abort();
    };
  }, [refresh, pollMs]);

  return { data, error, loading, refreshing, refresh };
}

function SummaryCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

export function BuildLine({ data }: { data: HubOverview }) {
  return (
    <footer className="t-buildline">
      FactorLab · {data.summary.database}
      {data.build?.version && <> · API <a href="/docker-images">v{data.build.version}</a></>}
      {WEB_VERSION && <> · Web v{WEB_VERSION}</>}
      {!data.build?.version && data.build?.release_id && <> · Release <a href="/docker-images">{data.build.release_id}</a></>}
      {data.build?.commit && <> · Commit {data.build.commit.slice(0, 12)}</>}
      {" "}· Asia/Kolkata · Stored counts may include replacing-table versions
    </footer>
  );
}

export function OpsOverview() {
  const { data, error, loading, refreshing, refresh } = useOverview();
  const [filter, setFilter] = useState("");
  const [domain, setDomain] = useState("All domains");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [hideEmpty, setHideEmpty] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("stored_rows");
  const [descending, setDescending] = useState(true);

  const domains = useMemo(
    () => ["All domains", ...Array.from(new Set(data?.tables.map((table) => table.domain) ?? [])).sort()],
    [data],
  );
  const tables = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return [...(data?.tables ?? [])]
      .filter((table) => domain === "All domains" || table.domain === domain)
      .filter((table) => statusFilters[status].matches(table.today_status))
      .filter((table) => !hideEmpty || table.stored_rows > 0)
      .filter((table) => !query || `${table.name} ${table.domain} ${table.category}`.toLowerCase().includes(query))
      .sort((left, right) => {
        const a = left[sortKey];
        const b = right[sortKey];
        const compared = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
        return descending ? -compared : compared;
      });
  }, [data, domain, status, hideEmpty, filter, sortKey, descending]);
  const largest = Math.max(...(data?.tables.map((table) => table.bytes_on_disk) ?? [0]), 1);

  function changeSort(nextKey: SortKey) {
    if (sortKey === nextKey) setDescending((value) => !value);
    else {
      setSortKey(nextKey);
      setDescending(nextKey === "stored_rows" || nextKey === "bytes_on_disk");
    }
  }

  if (loading && !data) {
    return (
      <main className="loading-state" aria-live="polite">
        <span className="loading-state__pulse" />
        <p>Reading the FactorLab data store…</p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="empty-state">
        <span className="eyebrow">Connection unavailable</span>
        <h1>The data hub could not load.</h1>
        <p>{error}</p>
        <button type="button" onClick={() => void refresh(true)}>Try again</button>
      </main>
    );
  }

  const politicalShare = data.political.datasets_with_data ? (data.political.fresh_datasets / data.political.datasets_with_data) * 100 : null;
  return (
    <main className="ops-overview">
      <PageHeader code="OPS" title="Ops overview" eyebrow="ClickHouse v2"
        info="Coverage, history, and freshness across every FactorLab v2 namespace, from raw payloads to curated market, political, and operations tables.">
        <span className="t-stamp">Updated {formatTime(data.generated_at)}</span>
        <button type="button" onClick={() => void refresh(true)} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh data"}
        </button>
      </PageHeader>

      {error && <div className="notice notice--error" role="status">{error}</div>}
      {data.warnings.length > 0 && (
        <div className="notice" role="status">{data.warnings.length} table check(s) returned partial results.</div>
      )}

      <section className="metric-grid" aria-label="Database totals">
        <SummaryCard label="Stored rows" value={formatCompact(data.summary.stored_rows)} detail="Fast physical count" />
        <SummaryCard label="On disk" value={formatBytes(data.summary.bytes_on_disk)} detail={`${domains.length - 1} v2 namespaces`} />
        <SummaryCard label="Tables populated" value={`${data.summary.populated_tables}/${data.summary.table_count}`} detail={data.summary.view_count ? `Plus ${data.summary.view_count} research views` : "Empty tables stay visible"} />
        <SummaryCard label="Needs attention" value={numberFormatter.format(data.summary.attention_tables)} detail={`${data.summary.healthy_tables} evaluated healthy`} />
      </section>

      <div className="t-grid t-grid--2" aria-label="Other pipeline health">
        <Panel code="IN" title="India intraday coverage" href="/india" tools={<StatusPill status={data.india.status} />}>
          <div className="ops-health">
            <Gauge value={data.india.coverage_percent} target={95} label="Coverage" />
            <div>
              <p>{data.india.status_reason}</p>
              <small>{numberFormatter.format(data.india.data_points)} of {numberFormatter.format(data.india.expected_data_points)} expected points · {data.india.market_status.replaceAll("_", " ")}</small>
            </div>
          </div>
        </Panel>
        <Panel code="POL" title="Disclosure freshness" href="/political" tools={<StatusPill status={data.political.status} />}>
          <div className="ops-health">
            <Gauge value={politicalShare} label={`${data.political.fresh_datasets}/${data.political.datasets_with_data} fresh`} />
            <div>
              <p>{data.political.status_reason}</p>
              <small>Latest ingestion {formatTime(data.political.last_ingested_at)}</small>
            </div>
          </div>
        </Panel>
      </div>

      <USDashboardPanel refreshKey={data.generated_at} />

      <section className="inventory" aria-labelledby="inventory-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">ClickHouse inventory</span>
            <h2 id="inventory-title">Table inventory</h2>
          </div>
          <span>{tables.length} of {data.tables.length} tables and views</span>
        </div>
        <div className="table-tools">
          <label>
            <span className="sr-only">Search tables</span>
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Search table or domain" />
          </label>
          <label>
            <span className="sr-only">Filter by domain</span>
            <select value={domain} onChange={(event) => setDomain(event.target.value)}>
              {domains.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>
            <span className="sr-only">Filter by status</span>
            <select value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}>
              {(Object.keys(statusFilters) as StatusFilter[]).map((key) => <option key={key} value={key}>{statusFilters[key].label}</option>)}
            </select>
          </label>
          <label className="toggle">
            <input type="checkbox" checked={hideEmpty} onChange={(event) => setHideEmpty(event.target.checked)} />
            <span>Hide empty tables</span>
          </label>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th><button onClick={() => changeSort("name")}>Table</button></th>
                <th><button onClick={() => changeSort("domain")}>Domain</button></th>
                <th className="numeric"><button onClick={() => changeSort("stored_rows")}>Stored rows</button></th>
                <th className="numeric"><button onClick={() => changeSort("bytes_on_disk")}>On disk</button></th>
                <th>Coverage</th>
                <th>Latest ingest</th>
                <th className="numeric">Today</th>
                <th><button onClick={() => changeSort("today_status")}>Status</button></th>
              </tr>
            </thead>
            <tbody>
              {tables.map((table) => (
                <tr key={table.name}>
                  <td data-label="Table"><code>{table.name}</code><small>{table.kind === "view" ? `View · ${table.category}` : table.category}</small></td>
                  <td data-label="Domain">{table.domain}</td>
                  <td data-label="Stored rows" className="numeric">{numberFormatter.format(table.stored_rows)}</td>
                  <td data-label="On disk" className="numeric">
                    <span className="t-sizebar" aria-hidden="true"><i style={{ width: `${(table.bytes_on_disk / largest) * 100}%` }} /></span>
                    {formatBytes(table.bytes_on_disk)}
                  </td>
                  <td data-label="Coverage"><span>{formatDate(table.first_data_at)}</span><small>to {formatDate(table.last_data_at)}</small></td>
                  <td data-label="Latest ingest">{formatTime(table.last_ingested_at)}</td>
                  <td data-label="Today" className="numeric">{table.today_rows === null ? "—" : numberFormatter.format(table.today_rows)}</td>
                  <td data-label="Status"><StatusPill status={table.today_status} /><small className="status-reason">{table.status_reason}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
          {tables.length === 0 && <p className="no-results">No tables match those filters.</p>}
        </div>
      </section>
      <BuildLine data={data} />
    </main>
  );
}
