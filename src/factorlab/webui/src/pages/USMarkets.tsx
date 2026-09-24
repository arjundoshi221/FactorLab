import { useEffect, useRef, useState } from "react";

import { USInstrumentDaysTable } from "../components/us/USInstrumentDaysTable";
import { USInstrumentTable } from "../components/us/USInstrumentTable";
import { USStatusPill } from "../components/us/USStatusPill";
import { USSummaryCards } from "../components/us/USSummaryCards";
import { USUniverseTabs } from "../components/us/USUniverseTabs";
import type {
  USCandle, USDashboard, USDay, USInstrument, USPage, USResolution, USScope,
} from "../usTypes";

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`US market data could not load (${response.status}).`);
  return response.json() as Promise<T>;
}

export function USSummaryCard() {
  const [data, setData] = useState<USDashboard | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => void read<USDashboard>("/hub/api/v1/us/dashboard", controller.signal)
      .then(setData).catch(() => { if (!controller.signal.aborted) setError(true); });
    refresh();
    const timer = window.setInterval(refresh, 60000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  return <article className="health-card">
    <span className="eyebrow">US · configured universe + Schwab</span><h2><a href="/us">US market coverage</a></h2>
    {data ? <><strong className="health-card__metric">{data.universe.active.toLocaleString()}</strong>
      <p>reference stocks · {data.universe.daily_configured} configured</p>
      <USStatusPill status={data.source.status} /><small>Configured daily universe · liquid minute tier</small></>
      : <p>{error ? "Collection status unavailable" : "Loading US coverage…"}</p>}
  </article>;
}

export function USMarkets() {
  const [date, setDate] = useState("");
  const linkedSymbol = useRef(new URLSearchParams(window.location.search).get("symbol") ?? "");
  const [search, setSearch] = useState(linkedSymbol.current);
  const [scope, setScope] = useState<USScope>("all");
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const [dashboard, setDashboard] = useState<USDashboard | null>(null);
  const [page, setPage] = useState<USPage<USInstrument>>({ items: [], total: 0, offset: 0, next_cursor: null });
  const [selected, setSelected] = useState<USInstrument | null>(null);
  const [resolution, setResolution] = useState<USResolution>("daily");
  const [candles, setCandles] = useState<USCandle[]>([]);
  const [days, setDays] = useState<USDay[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  useEffect(() => { const timer = window.setInterval(() => setTick(value => value + 1), 60000); return () => window.clearInterval(timer); }, []);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ scope, search, offset: String(offset), limit: "100" });
    if (date) params.set("trading_date", date);
    setLoading(true); setError("");
    void Promise.all([
      read<USDashboard>(`/hub/api/v1/us/dashboard?${date ? `trading_date=${date}` : ""}`, controller.signal),
      read<USPage<USInstrument>>(`/hub/api/v1/us/instruments?${params}`, controller.signal),
    ]).then(([summary, instruments]) => {
      setDashboard(summary); setPage(instruments);
      if (linkedSymbol.current) {
        const match = instruments.items.find(item => item.symbol === linkedSymbol.current.toUpperCase());
        if (match) setSelected(match);
        linkedSymbol.current = "";
      }
    })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [date, search, scope, offset, tick]);
  useEffect(() => {
    if (!selected || !dashboard) return;
    const controller = new AbortController();
    const end = dashboard.trading_date;
    const params = new URLSearchParams({ symbol: selected.symbol,
      date_from: resolution === "daily" ? "1970-01-01" : end, date_to: end, limit: "50" });
    if (cursor) params.set("cursor", cursor);
    setDetailLoading(true); setDetailError("");
    void Promise.all([
      read<USPage<USCandle>>(`/hub/api/v1/us/candles/${resolution}?${params}`, controller.signal),
      read<USPage<USDay>>(`/hub/api/v1/us/instruments/${selected.listing_id}/days?date_to=${end}`, controller.signal),
    ]).then(([prices, history]) => { setCandles(prices.items); setNext(prices.next_cursor); setDays(history.items); })
      .catch(reason => { if (!controller.signal.aborted) setDetailError(String(reason.message)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected, resolution, cursor, dashboard]);
  const total = page.total ?? page.items.length;
  return <main className="us-markets">
    <section className="hero"><div><span className="eyebrow">US equities · configured universe + Schwab</span>
      <h1>US markets</h1><p>Configured US stocks daily; up to 250 liquid names with one-minute coverage.</p></div>
      <button onClick={() => setTick(value => value + 1)} disabled={loading}>Refresh data</button></section>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    {loading && <p role="status">Loading US markets…</p>}
    {dashboard && <><USSummaryCards dashboard={dashboard} />
      {dashboard.sources.some(source => source.status === "auth_required") && <div className="notice" role="status">
        Schwab authentication is required for universe validation and price collection. <a href="https://factorlab-upstox-auth.kairo-jai.workers.dev/" target="_blank" rel="noreferrer">Open broker authentication</a></div>}</>}
    <section className="inventory"><div className="section-heading"><div><span className="eyebrow">US reference instruments</span>
      <h2>Instruments and coverage</h2></div><small>{total.toLocaleString()} matching stocks</small></div>
      <USUniverseTabs value={scope} onChange={value => { setScope(value); setOffset(0); setSelected(null); }} />
      <div className="table-tools"><label>Search stocks <input value={search} onChange={event => { setSearch(event.target.value); setOffset(0); }} placeholder="Symbol or company" /></label>
        <label>Trading date <input type="date" value={date || dashboard?.trading_date || ""} onChange={event => { setDate(event.target.value); setCursor(null); }} /></label></div>
      <USInstrumentTable items={page.items} selected={selected} onSelect={item => { setSelected(item); setCursor(null); }} />
      {!loading && !page.items.length && <p className="notice">No US instruments match this view.</p>}
      <div className="table-tools"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous 100</button>
        <button disabled={offset + page.items.length >= total} onClick={() => setOffset(offset + 100)}>Next 100</button></div>
    </section>
    {selected && <section className="inventory us-detail"><div className="section-heading"><div><span className="eyebrow">Instrument history</span>
      <h2>{selected.symbol} · {selected.name}</h2></div><label>Resolution <select value={resolution}
        onChange={event => { setResolution(event.target.value as USResolution); setCursor(null); }}>
        <option value="daily">Daily prices</option><option value="1min">Selected session · one minute</option></select></label></div>
      {detailError && <p role="alert" className="notice notice--error">{detailError}</p>}
      {detailLoading ? <p role="status">Loading instrument history…</p> : <>
        <p className="us-note">Daily and minute data are supplied by Schwab. Prices are USD.</p>
        <div className="table-scroll"><table><thead><tr><th>Session / time (New York)</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead>
          <tbody>{candles.map((candle, index) => <tr key={index}><td>{candle.trade_date || new Date(candle.bar_time!).toLocaleString("en-US", { timeZone: "America/New_York" })}</td>
            {[candle.open, candle.high, candle.low, candle.close].map((value, position) => <td key={position}>{Number(value).toFixed(4)}</td>)}
            <td>{candle.volume.toLocaleString()}</td></tr>)}</tbody></table></div>
        {!candles.length && <p className="notice">No candles available for this selection.</p>}
        <div className="table-tools"><button disabled={!cursor} onClick={() => setCursor(null)}>Latest prices</button>
          <button disabled={!next} onClick={() => setCursor(next)}>Older prices</button></div>
        <USInstrumentDaysTable days={days} resolution={resolution} />
      </>}
    </section>}
  </main>;
}
