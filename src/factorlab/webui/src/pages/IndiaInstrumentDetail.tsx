import { useEffect, useMemo, useState } from "react";

import { IndiaCheckPill } from "../components/india/IndiaCheckPill";
import { IndiaCollectionPill } from "../components/india/IndiaCollectionPill";
import { IndiaInstrumentDaysTable } from "../components/india/IndiaInstrumentDaysTable";
import type {
  IndiaCandle,
  IndiaCandlePage,
  IndiaInstrumentDay,
  IndiaInstrumentRow,
} from "../indiaTypes";

const numberFormatter = new Intl.NumberFormat("en-IN");
const priceFormatter = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function indiaToday(): string {
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Kolkata" }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function historyStart(dateValue: string): string {
  const timestamp = new Date(`${dateValue}T00:00:00Z`);
  timestamp.setUTCDate(timestamp.getUTCDate() - 35);
  return timestamp.toISOString().slice(0, 10);
}

function numeric(value: number | string | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatPrice(value: number | string | null): string {
  const parsed = numeric(value);
  return parsed === null ? "—" : `₹${priceFormatter.format(parsed)}`;
}

function formatTime(value: string | null, includeDate = false): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    ...(includeDate ? { day: "2-digit", month: "short" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Instrument data could not load (${response.status}).`);
  return response.json() as Promise<T>;
}

function StatCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function CandleChart({ candles, symbol }: { candles: IndiaCandle[]; symbol: string }) {
  const points = useMemo(() => {
    const unique = new Map<string, IndiaCandle>();
    for (const candle of candles) if (!unique.has(candle.bar_time)) unique.set(candle.bar_time, candle);
    return [...unique.values()].reverse().filter((candle) => numeric(candle.high) !== null && numeric(candle.low) !== null);
  }, [candles]);
  if (!points.length) return <div className="candle-chart candle-chart--empty">No price candles are stored for this session.</div>;

  const width = 960;
  const height = 320;
  const left = 58;
  const right = 16;
  const top = 20;
  const bottom = 34;
  const highs = points.map((item) => numeric(item.high) as number);
  const lows = points.map((item) => numeric(item.low) as number);
  const maximum = Math.max(...highs);
  const minimum = Math.min(...lows);
  const spread = Math.max(maximum - minimum, Math.abs(maximum) * 0.001, 1);
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - right;
  const y = (value: number) => top + ((maximum - value) / spread) * plotHeight;
  const step = plotWidth / points.length;
  const bodyWidth = Math.max(1, Math.min(8, step * 0.65));
  const gridValues = [maximum, maximum - spread / 2, minimum];

  return <figure className="candle-chart" aria-labelledby="price-chart-title">
    <figcaption><div><span className="eyebrow">Actual stored candles</span><h2 id="price-chart-title">{symbol} intraday price</h2></div><span>{points.length} unique minutes · Asia/Kolkata</span></figcaption>
    <div className="candle-chart__viewport">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${symbol} one-minute candlestick chart`}>
        {gridValues.map((value) => <g key={value}><line className="chart-gridline" x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text className="chart-axis-label" x={left - 8} y={y(value) + 3} textAnchor="end">{priceFormatter.format(value)}</text></g>)}
        {points.map((candle, index) => {
          const open = numeric(candle.open) ?? numeric(candle.close) ?? minimum;
          const close = numeric(candle.close) ?? open;
          const high = numeric(candle.high) ?? Math.max(open, close);
          const low = numeric(candle.low) ?? Math.min(open, close);
          const rising = close >= open;
          const x = left + (index + 0.5) * step;
          const bodyTop = Math.min(y(open), y(close));
          const bodyHeight = Math.max(1, Math.abs(y(close) - y(open)));
          return <g className={rising ? "candle candle--up" : "candle candle--down"} key={`${candle.bar_time}-${candle.contract_id}`}>
            <title>{`${formatTime(candle.bar_time)} · O ${priceFormatter.format(open)} H ${priceFormatter.format(high)} L ${priceFormatter.format(low)} C ${priceFormatter.format(close)}`}</title>
            <line x1={x} x2={x} y1={y(high)} y2={y(low)} />
            <rect x={x - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} />
          </g>;
        })}
        <text className="chart-axis-label" x={left} y={height - 8}>{formatTime(points[0].bar_time)}</text>
        <text className="chart-axis-label" x={width - right} y={height - 8} textAnchor="end">{formatTime(points.at(-1)?.bar_time ?? null)}</text>
      </svg>
    </div>
  </figure>;
}

function CandleTable({ candles }: { candles: IndiaCandle[] }) {
  return <div className="table-wrap"><table className="india-candle-table">
    <thead><tr><th>Time</th><th className="numeric">Open</th><th className="numeric">High</th><th className="numeric">Low</th><th className="numeric">Close</th><th className="numeric">Volume</th><th className="numeric">Open interest</th><th>Source</th></tr></thead>
    <tbody>{candles.map((candle) => <tr key={`${candle.bar_time}-${candle.contract_id}-${candle.source}`}>
      <td data-label="Time"><code>{formatTime(candle.bar_time)}</code></td>
      <td data-label="Open" className="numeric">{formatPrice(candle.open)}</td>
      <td data-label="High" className="numeric">{formatPrice(candle.high)}</td>
      <td data-label="Low" className="numeric">{formatPrice(candle.low)}</td>
      <td data-label="Close" className="numeric">{formatPrice(candle.close)}</td>
      <td data-label="Volume" className="numeric">{candle.volume === null ? "—" : numberFormatter.format(candle.volume)}</td>
      <td data-label="Open interest" className="numeric">{candle.oi === null ? "—" : numberFormatter.format(candle.oi)}</td>
      <td data-label="Source">{candle.source}<small>Ingested {formatTime(candle.ingested_at)}</small></td>
    </tr>)}</tbody>
  </table></div>;
}

export function IndiaInstrumentDetail({ listingId }: { listingId: string }) {
  const today = useMemo(indiaToday, []);
  const initialDate = new URLSearchParams(window.location.search).get("date") ?? today;
  const [selectedDate, setSelectedDate] = useState(initialDate);
  const [profile, setProfile] = useState<IndiaInstrumentRow | null>(null);
  const [days, setDays] = useState<IndiaInstrumentDay[]>([]);
  const [candlePage, setCandlePage] = useState<IndiaCandlePage | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [candlesLoading, setCandlesLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const range = new URLSearchParams({ date_from: historyStart(selectedDate), date_to: selectedDate });
    setLoading(true);
    void Promise.all([
      read<IndiaInstrumentRow>(`/hub/api/v1/india/instruments/${listingId}?trading_date=${selectedDate}`, controller.signal),
      read<{ items: IndiaInstrumentDay[] }>(`/hub/api/v1/india/instruments/${listingId}/days?${range}`, controller.signal),
    ]).then(([nextProfile, history]) => { setProfile(nextProfile); setDays(history.items); setError(""); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [listingId, selectedDate, refreshKey]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ trading_date: selectedDate, limit: "500" });
    if (cursor) params.set("cursor", cursor);
    setCandlesLoading(true);
    void read<IndiaCandlePage>(`/hub/api/v1/india/instruments/${listingId}/candles?${params}`, controller.signal)
      .then((page) => { setCandlePage(page); setError(""); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setCandlesLoading(false); });
    return () => controller.abort();
  }, [listingId, selectedDate, cursor, refreshKey]);

  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden && selectedDate === indiaToday()) setRefreshKey((value) => value + 1); }, 60_000);
    return () => window.clearInterval(timer);
  }, [selectedDate]);

  function selectDate(dateValue: string) {
    setLoading(true);
    setCandlesLoading(true);
    setCandlePage(null);
    setSelectedDate(dateValue);
    setCursor(null);
    setCursorHistory([]);
    window.history.replaceState({}, "", `/india/instruments/${listingId}?date=${dateValue}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const session = useMemo(() => {
    const chronological = [...(candlePage?.items ?? [])].reverse();
    const first = chronological.find((item) => numeric(item.open) !== null);
    const last = [...chronological].reverse().find((item) => numeric(item.close) !== null);
    const values = chronological.flatMap((item) => [numeric(item.high), numeric(item.low)]).filter((value): value is number => value !== null);
    const open = first ? numeric(first.open) : null;
    const close = last ? numeric(last.close) : null;
    return {
      open,
      close,
      high: values.length ? Math.max(...values) : null,
      low: values.length ? Math.min(...values) : null,
      change: open !== null && close !== null && open !== 0 ? ((close - open) / open) * 100 : null,
      volume: chronological.reduce((total, item) => total + (item.volume ?? 0), 0),
    };
  }, [candlePage]);

  if (loading && !profile) return <main className="loading-state" aria-live="polite"><span className="loading-state__pulse" /><p>Loading instrument and stored candles…</p></main>;
  if (!profile) return <main className="empty-state"><span className="eyebrow">Instrument unavailable</span><h1>This instrument could not be loaded.</h1><p>{error}</p><a className="button-link" href="/india">Back to India markets</a></main>;

  return <main className="india-instrument-page">
    <section className="instrument-hero">
      <a className="instrument-back" href="/india">← India instruments</a>
      <div className="instrument-hero__main"><div><span className="eyebrow">{profile.exchange_code ?? "India"} · {profile.instrument_type ?? "instrument"}</span><h1>{profile.symbol}</h1><p>{profile.name || "Unnamed instrument"}</p></div>
        <div className="instrument-hero__badges"><IndiaCollectionPill status={profile.collection_status} /><IndiaCheckPill status={profile.check_status} /></div></div>
      <div className="instrument-session-control"><label htmlFor="instrument-date">Trading session</label><input id="instrument-date" type="date" max={today} value={selectedDate} onChange={(event) => selectDate(event.target.value)} /><button onClick={() => setRefreshKey((value) => value + 1)} disabled={loading || candlesLoading}>{loading || candlesLoading ? "Refreshing…" : "Refresh"}</button></div>
    </section>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    <dl className="instrument-metadata">
      <div><dt>All stored rows</dt><dd>{numberFormatter.format(profile.data_points)}</dd></div>
      <div><dt>Trading days</dt><dd>{numberFormatter.format(profile.trading_days)}</dd></div>
      <div><dt>Stored range</dt><dd>{formatTime(profile.first_bar_time, true)} — {formatTime(profile.last_bar_time, true)}</dd></div>
      <div><dt>Latest ingestion</dt><dd>{formatTime(profile.last_ingested_at, true)}</dd></div>
      <div><dt>Listing ID</dt><dd><code>{profile.listing_id}</code></dd></div>
    </dl>
    {loading || candlesLoading ? <p className="inline-loading" role="status">Checking the selected exchange session…</p> : <>
      <section className="instrument-verdict"><div><span className="eyebrow">Selected-session verdict</span><h2>{selectedDate} · {profile.check_status.replaceAll("_", " ")}</h2><p>{profile.check_reason}</p></div><div><strong>{profile.coverage_percent === null ? "—" : `${profile.coverage_percent.toFixed(1)}%`}</strong><span>{numberFormatter.format(profile.selected_data_points)} of {numberFormatter.format(profile.expected_data_points)} expected rows</span></div></section>
      <section className="metric-grid" aria-label="Selected session prices">
        <StatCard label="Last price" value={formatPrice(session.close)} detail={session.change === null ? "No comparable open" : `${session.change >= 0 ? "+" : ""}${session.change.toFixed(2)}% from first open`} />
        <StatCard label="Session high" value={formatPrice(session.high)} detail={`Open ${formatPrice(session.open)}`} />
        <StatCard label="Session low" value={formatPrice(session.low)} detail={`${numberFormatter.format(session.volume)} total volume`} />
        <StatCard label="Stored candles" value={numberFormatter.format(candlePage?.items.length ?? 0)} detail={`${profile.unique_series} stored series · page`} />
      </section>
    </>}
    {candlesLoading && !candlePage ? <p className="inline-loading" role="status">Querying one-minute candles…</p> : <CandleChart candles={candlePage?.items ?? []} symbol={profile.symbol} />}
    <section className="inventory instrument-candles" aria-labelledby="candle-table-title"><div className="section-heading"><div><span className="eyebrow">Raw market data</span><h2 id="candle-table-title">One-minute candles</h2></div><span>Actual ClickHouse rows · newest first</span></div>
      {candlesLoading ? <p className="inline-loading" role="status">Loading candle rows…</p> : <CandleTable candles={candlePage?.items ?? []} />}
      {!candlesLoading && !candlePage?.items.length && <p className="political-empty">No candle rows were found for {selectedDate}.</p>}
      <div className="pagination"><button className="button-secondary" disabled={!cursorHistory.length} onClick={() => { const previous = [...cursorHistory]; const nextCursor = previous.pop() ?? null; setCursorHistory(previous); setCursor(nextCursor); }}>Newer candles</button><span>{candlePage?.items.length ?? 0} rows on this page</span><button className="button-secondary" disabled={!candlePage?.next_cursor} onClick={() => { if (!candlePage?.next_cursor) return; setCursorHistory((history) => [...history, cursor]); setCursor(candlePage.next_cursor); }}>Older candles</button></div>
    </section>
    <IndiaInstrumentDaysTable instrument={profile} days={days} loading={loading} selectedDate={selectedDate} onSelectDate={selectDate} />
    <footer>FactorLab · {profile.symbol} · Prices in INR · One-minute rows from {profile.source ?? "configured market sources"}</footer>
  </main>;
}
