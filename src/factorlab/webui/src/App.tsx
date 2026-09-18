import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { USDashboardPanel } from "./components/us/USDashboardPanel";
import { IndiaMarkets } from "./pages/IndiaMarkets";
import { IndiaInstrumentDetail } from "./pages/IndiaInstrumentDetail";
import { PoliticalData } from "./pages/PoliticalData";
import { USMarkets } from "./pages/USMarkets";

type HubStatus =
  | "healthy"
  | "attention"
  | "missing"
  | "not_expected"
  | "not_configured"
  | "unknown";

interface HubTable {
  name: string;
  domain: string;
  category: string;
  engine: string;
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
  summary: {
    database: string;
    table_count: number;
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

const statusCopy: Record<HubStatus, string> = {
  healthy: "Healthy",
  attention: "Needs attention",
  missing: "Missing",
  not_expected: "Not expected",
  not_configured: "Not configured",
  unknown: "Unknown",
};

const numberFormatter = new Intl.NumberFormat("en-IN");
const compactFormatter = new Intl.NumberFormat("en-IN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(parsed);
}

function formatTime(value: string | null): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(parsed);
}

function StatusPill({ status }: { status: HubStatus }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {statusCopy[status]}
    </span>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const path = window.location.pathname;
  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="FactorLab Data Hub home">
          <span className="brand__mark">FL</span>
          <span>
            <strong>FactorLab</strong>
            <small>Data Hub</small>
          </span>
        </a>
        <nav aria-label="Primary navigation">
          <a className={path === "/" ? "active" : ""} href="/">Dashboard</a>
          <a className={path.startsWith("/india") ? "active" : ""} href="/india">India markets</a>
          <a className={path === "/us" ? "active" : ""} href="/us">US markets</a>
          <a className={path === "/political" ? "active" : ""} href="/political">Political</a>
          <a className={path === "/roadmap" ? "active" : ""} href="/roadmap">Roadmap</a>
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
      .filter((table) => !query || `${table.name} ${table.domain} ${table.category}`.toLowerCase().includes(query))
      .sort((left, right) => {
        const a = left[sortKey];
        const b = right[sortKey];
        const compared = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
        return descending ? -compared : compared;
      });
  }, [data, domain, filter, sortKey, descending]);

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
          <span className="eyebrow">System overview</span>
          <h1>Your data, at a glance.</h1>
          <p>Coverage, history, and freshness across every FactorLab table.</p>
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
        <SummaryCard label="Stored rows" value={compactFormatter.format(data.summary.stored_rows)} detail="Fast physical count" />
        <SummaryCard label="On disk" value={formatBytes(data.summary.bytes_on_disk)} detail={`${data.summary.database} database`} />
        <SummaryCard label="Tables populated" value={`${data.summary.populated_tables}/${data.summary.table_count}`} detail="Empty tables stay visible" />
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
          <span>{tables.length} of {data.summary.table_count} tables</span>
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
                  <td data-label="Table"><code>{table.name}</code><small>{table.category}</small></td>
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
      <footer>FactorLab · Asia/Kolkata · Stored counts may include replacing-table versions</footer>
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
  return (
    <Shell>
      {path === "/roadmap" ? <Roadmap /> : indiaInstrumentMatch ? <IndiaInstrumentDetail instrumentId={indiaInstrumentMatch[1]} /> : path === "/india" ? <IndiaMarkets /> : path === "/us" ? <USMarkets /> : path === "/political" ? <PoliticalData /> : <Dashboard />}
    </Shell>
  );
}
