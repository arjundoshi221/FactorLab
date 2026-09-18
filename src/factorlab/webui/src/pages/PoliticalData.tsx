import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  PoliticalCoverage,
  PoliticalDashboardData,
  PoliticalMetric,
  PoliticalMetricsSeries,
  PoliticalTrade,
  PoliticalTradesPage,
} from "../politicalTypes";

const METRIC_LABELS: Record<PoliticalMetric, string> = {
  trades: "Disclosed trades",
  unique_legislators: "Active legislators",
  unique_tickers: "Unique tickers",
  amount_min: "Minimum disclosed value",
  late_disclosures: "Late disclosures",
};

const integerFormatter = new Intl.NumberFormat("en-US");
const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function twelveMonthStart(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() - 11, 1);
  return date.toISOString().slice(0, 10);
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

function formatTime(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(new Date(value));
}

function formatMetric(value: number, metric: PoliticalMetric): string {
  return metric === "amount_min" ? usdFormatter.format(value) : compactFormatter.format(value);
}

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Political data could not load (${response.status}).`);
  return response.json() as Promise<T>;
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function ActivityChart({ series }: { series: PoliticalMetricsSeries }) {
  const maximum = Math.max(...series.points.map((point) => point.value), 1);
  return (
    <figure className="political-chart" aria-labelledby="political-chart-title">
      <figcaption>
        <div><span className="eyebrow">12-month trend</span><h2 id="political-chart-title">{METRIC_LABELS[series.metric]}</h2></div>
        <span>Grouped by transaction month</span>
      </figcaption>
      {series.points.length ? (
        <div className="political-chart__plot">
          {series.points.map((point) => (
            <div className="political-chart__column" key={point.bucket}>
              <span className="political-chart__value">{formatMetric(point.value, series.metric)}</span>
              <div className="political-chart__track">
                <span style={{ height: `${Math.max(3, (point.value / maximum) * 100)}%` }} />
              </div>
              <time dateTime={point.bucket}>{new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(new Date(`${point.bucket}T00:00:00Z`))}</time>
            </div>
          ))}
        </div>
      ) : <p className="political-empty">No activity was recorded in this period.</p>}
    </figure>
  );
}

function RateBar({ label, value }: { label: string; value: number }) {
  return <div className="political-rate">
    <div><span>{label}</span><strong>{value.toFixed(1)}%</strong></div>
    <div className="political-rate__track"><span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></div>
  </div>;
}

function CoveragePanel({ coverage }: { coverage: PoliticalCoverage[] }) {
  return <section className="political-coverage" aria-labelledby="coverage-title">
    <div className="section-heading"><div><span className="eyebrow">Data quality</span><h2 id="coverage-title">Disclosure coverage</h2></div><span>All available years</span></div>
    <div className="political-coverage__grid">
      {coverage.map((item) => <article key={item.chamber}>
        <div className="political-coverage__heading"><div><span className="eyebrow">Chamber</span><h3>{item.chamber}</h3></div><strong>{integerFormatter.format(item.trades)} trades</strong></div>
        <RateBar label="Filings parsed" value={item.filing_parse_rate} />
        <RateBar label="Legislators matched" value={item.legislator_match_rate} />
        <RateBar label="Tickers resolved" value={item.ticker_resolution_rate} />
        <small>{formatDate(item.first_filing_date)} — {formatDate(item.latest_filing_date)}</small>
      </article>)}
      {!coverage.length && <p className="political-empty">No chamber coverage is available.</p>}
    </div>
  </section>;
}

function TradeTable({ trades }: { trades: PoliticalTrade[] }) {
  return <div className="table-wrap"><table className="political-trades-table">
    <thead><tr><th>Transaction</th><th>Legislator</th><th>Asset</th><th>Type</th><th>Amount</th><th>Filed</th><th>Source</th></tr></thead>
    <tbody>{trades.map((trade) => <tr key={trade.trade_key}>
      <td data-label="Transaction"><strong>{formatDate(trade.transaction_date)}</strong><small>{trade.chamber} · {trade.state}{trade.district === null ? "" : `-${trade.district}`}</small></td>
      <td data-label="Legislator">{trade.legislator_name}<small>{trade.bioguide_id ?? "Unmatched legislator"}</small></td>
      <td data-label="Asset">{trade.ticker ? <a className="political-ticker" href={`/us?symbol=${encodeURIComponent(trade.ticker)}`}>{trade.ticker}</a> : <span>Unresolved</span>}<small>{trade.asset_name_raw}</small></td>
      <td data-label="Type"><span className={`transaction-type transaction-type--${trade.transaction_type.startsWith("sale") ? "sale" : trade.transaction_type}`}>{trade.transaction_type.replaceAll("_", " ")}</span></td>
      <td data-label="Amount">{trade.amount_str || (trade.amount_min === null ? "—" : usdFormatter.format(trade.amount_min))}</td>
      <td data-label="Filed">{formatDate(trade.filing_date)}<small>{Math.max(0, Math.round((Date.parse(trade.filing_date) - Date.parse(trade.transaction_date)) / 86_400_000))} days later</small></td>
      <td data-label="Source"><a href={trade.filing_url} target="_blank" rel="noreferrer">View filing ↗</a><small>{trade.source.replaceAll("_", " ")}</small></td>
    </tr>)}</tbody>
  </table></div>;
}

export function PoliticalData() {
  const today = useMemo(utcToday, []);
  const [tickerDraft, setTickerDraft] = useState("");
  const [ticker, setTicker] = useState("");
  const [chamber, setChamber] = useState<"all" | "house" | "senate">("all");
  const [metric, setMetric] = useState<PoliticalMetric>("trades");
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dashboard, setDashboard] = useState<PoliticalDashboardData | null>(null);
  const [coverage, setCoverage] = useState<PoliticalCoverage[]>([]);
  const [series, setSeries] = useState<PoliticalMetricsSeries | null>(null);
  const [tradePage, setTradePage] = useState<PoliticalTradesPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const metrics = new URLSearchParams({ metric, date_basis: "transaction", group_by: "month", date_from: twelveMonthStart(today), date_to: today });
    setLoading(true);
    void Promise.all([
      read<PoliticalDashboardData>(`/hub/api/v1/political/dashboard?as_of_date=${today}`, controller.signal),
      read<{ items: PoliticalCoverage[] }>("/hub/api/v1/political/coverage", controller.signal),
      read<PoliticalMetricsSeries>(`/hub/api/v1/political/metrics/timeseries?${metrics}`, controller.signal),
    ]).then(([nextDashboard, nextCoverage, nextSeries]) => {
      setDashboard(nextDashboard); setCoverage(nextCoverage.items); setSeries(nextSeries); setError("");
    }).catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [metric, refreshKey, today]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: "50" });
    if (ticker) params.set("ticker", ticker);
    if (chamber !== "all") params.set("chamber", chamber);
    if (cursor) params.set("cursor", cursor);
    void read<PoliticalTradesPage>(`/hub/api/v1/political/trades?${params}`, controller.signal)
      .then((page) => { setTradePage(page); setError(""); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); });
    return () => controller.abort();
  }, [ticker, chamber, cursor, refreshKey]);

  function searchTicker(event: FormEvent) {
    event.preventDefault();
    setTicker(tickerDraft.trim().toUpperCase());
    setCursor(null);
    setCursorHistory([]);
  }

  if (loading && !dashboard) return <main className="loading-state" aria-live="polite"><span className="loading-state__pulse" /><p>Reading political disclosures…</p></main>;
  if (!dashboard) return <main className="empty-state"><span className="eyebrow">Political data unavailable</span><h1>The disclosure explorer could not load.</h1><p>{error}</p><button onClick={() => setRefreshKey((value) => value + 1)}>Try again</button></main>;

  return <main className="political-data">
    <section className="hero"><div><span className="eyebrow">US Congress · STOCK Act disclosures</span><h1>Political trading activity, made legible.</h1><p>Explore reported transactions, filing coverage, entity resolution, and disclosure quality from the alt_political dataset.</p></div>
      <div className="refresh-block"><span>Latest ingest {formatTime(dashboard.last_ingested_at)}</span><button onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>{loading ? "Refreshing…" : "Refresh data"}</button></div></section>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    <section className="metric-grid" aria-label="Political data totals">
      <MetricCard label="Disclosed trades" value={integerFormatter.format(dashboard.trades)} detail={`${integerFormatter.format(dashboard.filings)} indexed filings`} />
      <MetricCard label="Unique tickers" value={integerFormatter.format(dashboard.unique_tickers)} detail={`${dashboard.ticker_resolution_rate.toFixed(1)}% resolution rate`} />
      <MetricCard label="Legislators" value={integerFormatter.format(dashboard.unique_legislators)} detail={`${dashboard.legislator_match_rate.toFixed(1)}% of trades matched`} />
      <MetricCard label="Quality findings" value={integerFormatter.format(dashboard.anomaly_count)} detail={`${integerFormatter.format(dashboard.late_disclosures)} late disclosures`} />
    </section>
    <section className="political-activity">
      <div className="political-metric-control"><label htmlFor="political-metric">Chart metric</label><select id="political-metric" value={metric} onChange={(event) => setMetric(event.target.value as PoliticalMetric)}>{Object.entries(METRIC_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
      {series && <ActivityChart series={series} />}
    </section>
    <CoveragePanel coverage={coverage} />
    <section className="inventory" aria-labelledby="trades-title">
      <div className="section-heading"><div><span className="eyebrow">Disclosure explorer</span><h2 id="trades-title">Reported transactions</h2></div><span>Newest transactions first</span></div>
      <div className="political-tools"><form onSubmit={searchTicker} role="search"><label><span className="sr-only">Filter by ticker</span><input value={tickerDraft} onChange={(event) => setTickerDraft(event.target.value)} placeholder="Filter by ticker, e.g. AAPL" /></label><button type="submit">Apply</button>{ticker && <button className="button-secondary" type="button" onClick={() => { setTickerDraft(""); setTicker(""); setCursor(null); setCursorHistory([]); }}>Clear</button>}</form>
        <label><span>Chamber</span><select value={chamber} onChange={(event) => { setChamber(event.target.value as typeof chamber); setCursor(null); setCursorHistory([]); }}><option value="all">All chambers</option><option value="house">House</option><option value="senate">Senate</option></select></label></div>
      {tradePage && <TradeTable trades={tradePage.items} />}
      {tradePage && !tradePage.items.length && <p className="political-empty">No disclosures match these filters.</p>}
      <div className="pagination"><button className="button-secondary" disabled={!cursorHistory.length} onClick={() => { const previous = [...cursorHistory]; const nextCursor = previous.pop() ?? null; setCursorHistory(previous); setCursor(nextCursor); }}>Newer</button><span>{tradePage?.items.length ?? 0} disclosures shown</span><button className="button-secondary" disabled={!tradePage?.next_cursor} onClick={() => { if (!tradePage?.next_cursor) return; setCursorHistory((history) => [...history, cursor]); setCursor(tradePage.next_cursor); }}>Older</button></div>
    </section>
    <footer>FactorLab · Alternative political data · Disclosures are not investment recommendations</footer>
  </main>;
}
