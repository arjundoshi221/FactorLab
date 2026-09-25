import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { USDashboardPanel } from "./components/us/USDashboardPanel";
import { IndiaMarkets } from "./pages/IndiaMarkets";
import { IndiaInstrumentDetail } from "./pages/IndiaInstrumentDetail";
import { PoliticalData } from "./pages/PoliticalData";
import { USMarkets } from "./pages/USMarkets";
import { DockerImages } from "./pages/DockerImages";

import { StatusPill, type HubStatus } from "./components/ui";
import { formatBytes, formatCompact, formatDate, formatTime } from "./shared/format";
import { DataHome } from "./pages/catalog/DataHome";
import { Pipelines } from "./pages/catalog/Pipelines";

const SchemaMap = lazy(async () => {
  const module = await import("./pages/SchemaMap");
  return { default: module.SchemaMap };
});

const TableDetail = lazy(async () => {
  const module = await import("./pages/catalog/TableDetail");
  return { default: module.TableDetail };
});

interface HubTable {
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

interface HubOverview {
  generated_at: string;
  build?: { release_id: string | null; commit: string | null } | null;
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

const operationsLinks = [
  { href: "/", label: "Dashboard", active: (path: string) => path === "/" },
  { href: "/india", label: "India markets", active: (path: string) => path.startsWith("/india") },
  { href: "/us", label: "US markets", active: (path: string) => path === "/us" },
  { href: "/political", label: "Political", active: (path: string) => path === "/political" },
  { href: "/schema", label: "Schema map", active: (path: string) => path.startsWith("/schema") },
  { href: "/docker-images", label: "Docker images", active: (path: string) => path === "/docker-images" },
  { href: "/roadmap", label: "Roadmap", active: (path: string) => path === "/roadmap" },
];

function Shell({ children }: { children: React.ReactNode }) {
  const path = window.location.pathname;
  const inOperations = operationsLinks.some((link) => link.active(path));
  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/data" aria-label="FactorLab Data Hub home">
          <span className="brand__mark">FL</span>
          <span>
            <strong>FactorLab</strong>
            <small>Data Hub</small>
          </span>
        </a>
        <nav aria-label="Primary navigation">
          <a className={path === "/data" || path.startsWith("/data/tables") ? "active" : ""} href="/data">Data catalog</a>
          <a className={path === "/data/pipelines" ? "active" : ""} href="/data/pipelines">Pipelines</a>
          <details className={`nav-menu${inOperations ? " is-active" : ""}`}>
            <summary>Operations</summary>
            <div className="nav-menu__panel">
              {operationsLinks.map((link) => (
                <a key={link.href} className={link.active(path) ? "active" : ""} href={link.href}>{link.label}</a>
              ))}
            </div>
          </details>
        </nav>
        <span className="private-badge">Private system</span>
      </header>
      {children}
    </div>
  );
}

function useOverview() {
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
    }, 60_000);
    const onVisibility = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      controller.current?.abort();
    };
  }, [refresh]);

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

function HealthCard({
  eyebrow,
  title,
  status,
  metric,
  reason,
  footer,
}: {
  eyebrow: string;
  title: string;
  status: HubStatus;
  metric: string;
  reason: string;
  footer: string;
}) {
  return (
    <article className="health-card">
      <div className="health-card__top">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        <StatusPill status={status} />
      </div>
      <strong className="health-card__metric">{metric}</strong>
      <p>{reason}</p>
      <small>{footer}</small>
    </article>
  );
}

function Dashboard() {
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

  return (
    <main>
      <section className="hero">
        <div>
          <span className="eyebrow">System overview · ClickHouse v2</span>
          <h1>Your data, at a glance.</h1>
          <p>Coverage, history, and freshness across every FactorLab v2 namespace, from raw payloads to curated market, political, and operations tables.</p>
        </div>
        <div className="refresh-block">
          <span>Updated {formatTime(data.generated_at)}</span>
          <button type="button" onClick={() => void refresh(true)} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh data"}
          </button>
        </div>
      </section>

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

      <USDashboardPanel refreshKey={data.generated_at} />

      <section className="health-grid" aria-label="Other pipeline health">
        <HealthCard
          eyebrow="India · intraday"
          title="Market coverage"
          status={data.india.status}
          metric={`${data.india.coverage_percent.toFixed(1)}%`}
          reason={data.india.status_reason}
          footer={`${numberFormatter.format(data.india.data_points)} of ${numberFormatter.format(data.india.expected_data_points)} expected points · ${data.india.market_status.replaceAll("_", " ")}`}
        />
        <HealthCard
          eyebrow="US · political"
          title="Disclosure freshness"
          status={data.political.status}
          metric={`${data.political.fresh_datasets}/${data.political.datasets_with_data}`}
          reason={data.political.status_reason}
          footer={`Latest ingestion ${formatTime(data.political.last_ingested_at)}`}
        />
      </section>

      <section className="inventory" aria-labelledby="inventory-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">ClickHouse inventory</span>
            <h2 id="inventory-title">Every table, one view</h2>
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
                  <td data-label="On disk" className="numeric">{formatBytes(table.bytes_on_disk)}</td>
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
      <footer>
        FactorLab · {data.summary.database}
        {data.build?.release_id && <> · Release <a href="/docker-images">{data.build.release_id}</a></>}
        {data.build?.commit && <> · Commit {data.build.commit.slice(0, 12)}</>}
        {" "}· Asia/Kolkata · Stored counts may include replacing-table versions
      </footer>
    </main>
  );
}

const roadmap = [
  { version: "V1", title: "Data health", state: "Now", copy: "Table inventory, coverage dates, and schedule-aware freshness." },
  { version: "V2", title: "Data explorer", state: "Next", copy: "Filter and query datasets without writing ClickHouse SQL." },
  { version: "V3", title: "Pipeline operations", state: "Planned", copy: "Live run monitoring, failure diagnosis, and alert delivery." },
  { version: "V4", title: "Live markets", state: "Planned", copy: "Option chains, live market data, and research visualizations." },
  { version: "V5", title: "Backtesting", state: "Planned", copy: "Reproducible strategy experiments on point-in-time data." },
];

function Roadmap() {
  return (
    <main>
      <section className="hero hero--roadmap">
        <div>
          <span className="eyebrow">Product direction</span>
          <h1>From data confidence to research execution.</h1>
          <p>Each release builds on the same APIs, identifiers, and point-in-time ClickHouse data.</p>
        </div>
        <a className="button-link" href="/">Open dashboard</a>
      </section>
      <section className="roadmap" aria-label="FactorLab roadmap">
        {roadmap.map((item, index) => (
          <article className={index === 0 ? "roadmap-card roadmap-card--active" : "roadmap-card"} key={item.version}>
            <div className="roadmap-card__line" aria-hidden="true"><span>{index + 1}</span></div>
            <div>
              <span className="eyebrow">{item.version} · {item.state}</span>
              <h2>{item.title}</h2>
              <p>{item.copy}</p>
            </div>
          </article>
        ))}
      </section>
      <aside className="principle-card">
        <span className="eyebrow">Foundation</span>
        <h2>One platform, one source of truth.</h2>
        <p>Research, monitoring, live market views, and backtests will share stable identifiers and point-in-time data instead of creating parallel systems.</p>
      </aside>
      <footer>FactorLab · Research-grade infrastructure, built in deliberate stages</footer>
    </main>
  );
}

export function App() {
  const path = window.location.pathname;
  const indiaInstrumentMatch = path.match(/^\/india\/instruments\/([0-9a-f-]+)$/i);
  const tableMatch = path.match(/^\/data\/tables\/([a-z_]+\.[a-z0-9_]+)$/);
  if (path === "/data") return <Shell><DataHome /></Shell>;
  if (path === "/data/pipelines") return <Shell><Pipelines /></Shell>;
  if (tableMatch) {
    return (
      <Shell>
        <Suspense fallback={<main className="loading-state">Loading table…</main>}>
          <TableDetail key={tableMatch[1]} name={tableMatch[1]} />
        </Suspense>
      </Shell>
    );
  }
  if (path === "/schema/v2") {
    return (
      <Shell>
        <Suspense fallback={<main className="loading-state">Loading v2 schema canvas...</main>}>
          <SchemaMap version="v2" />
        </Suspense>
      </Shell>
    );
  }
  return (
    <Shell>
      {path === "/roadmap" ? <Roadmap /> : path === "/docker-images" ? <DockerImages /> : path === "/schema" ? <Suspense fallback={<main className="loading-state">Loading schema canvas…</main>}><SchemaMap /></Suspense> : indiaInstrumentMatch ? <IndiaInstrumentDetail listingId={indiaInstrumentMatch[1]} /> : path === "/india" ? <IndiaMarkets /> : path === "/us" ? <USMarkets /> : path === "/political" ? <PoliticalData /> : <Dashboard />}
    </Shell>
  );
}
