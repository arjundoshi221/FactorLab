import { useEffect, useState } from "react";
import { errorMessage, fetchJson } from "../shared/api";
import "./MarketData.css";

type Market = "US" | "IN";
type Stream = "daily" | "historical";
type DataFilter = "all" | "has_data" | "no_data";

interface Ticker {
  listing_id: string;
  contract_id: string | null;
  symbol: string;
  name: string | null;
  source: string;
  universe: string;
  resolution: string;
  series_count: number;
  rows: number;
  trading_days: number;
  first_date: string | null;
  latest_date: string | null;
  last_ingested_at: string | null;
  history_complete: boolean | null;
  recovery_error: string | null;
}

interface UniversePage {
  market: Market;
  stream: Stream;
  configured_total: number;
  total: number;
  limit: number;
  offset: number;
  configuration_as_of: string | null;
  items: Ticker[];
}

interface TickerCoverage extends Ticker {
  gap_window_from: string | null;
  gap_window_to: string | null;
  missing_trading_days: number | null;
}

const count = new Intl.NumberFormat("en-IN");
const time = (value: string | null) => value
  ? new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })
  : "—";

export function CollectionCoverage() {
  const [market, setMarket] = useState<Market>("US");
  const [stream, setStream] = useState<Stream>("daily");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<DataFilter>("all");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<UniversePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Ticker | null>(null);
  const [detail, setDetail] = useState<TickerCoverage | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ market, stream, search: query, data_filter: filter,
      limit: "50", offset: String(offset) });
    setLoading(true);
    setError("");
    setPage(null);
    void fetchJson<UniversePage>(`/hub/api/v1/market-data/universe?${params}`, { signal: controller.signal })
      .then(setPage)
      .catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [market, stream, query, filter, offset, refresh]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    const controller = new AbortController();
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    const contract = selected.contract_id ? `?contract_id=${encodeURIComponent(selected.contract_id)}` : "";
    void fetchJson<TickerCoverage>(
      `/hub/api/v1/market-data/universe/${market}/${stream}/${selected.listing_id}${contract}`,
      { signal: controller.signal },
    ).then(setDetail)
      .catch(reason => { if (!controller.signal.aborted) setDetailError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [market, stream, selected, refresh]);

  const switchMarket = (value: Market) => { setMarket(value); setOffset(0); setSelected(null); };
  const switchStream = (value: Stream) => { setStream(value); setOffset(0); setSelected(null); };
  const selectedPage = page?.market === market && page.stream === stream && query === search.trim() ? page : null;
  const rangeEnd = selectedPage ? Math.min(offset + selectedPage.items.length, selectedPage.total) : 0;

  return <main className="market-data">
    <section className="hero market-data__hero">
      <div><span className="eyebrow">Configured collection · US and India</span>
        <h1>Market data</h1>
        <p>Explore every ticker selected for collection and see the data stored for each one.</p></div>
      <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading}>Refresh data</button>
    </section>

    <section className="inventory market-data__inventory" aria-label="Market universe">
      <div className="market-data__switch" aria-label="Market">
        <button type="button" aria-pressed={market === "US"} className={market === "US" ? "is-active" : ""}
          onClick={() => switchMarket("US")}>US</button>
        <button type="button" aria-pressed={market === "IN"} className={market === "IN" ? "is-active" : ""}
          onClick={() => switchMarket("IN")}>India</button>
      </div>
      <div className="market-data__switch" aria-label="Collection view">
        <button type="button" aria-pressed={stream === "daily"} className={stream === "daily" ? "is-active" : ""}
          onClick={() => switchStream("daily")}>Daily collection</button>
        <button type="button" aria-pressed={stream === "historical"} className={stream === "historical" ? "is-active" : ""}
          onClick={() => switchStream("historical")}>Historical recovery</button>
      </div>
      <p className="market-data__hint">{stream === "daily"
        ? "Current collector universe and bars stored in the last 30 calendar days. Select a ticker for its recent coverage."
        : "Recovery uses the configured collector universe and the same stored bars. Select a ticker for its full stored range and recovery status."}</p>

      <div className="market-data__summary">
        <div><span>Configured tickers</span><strong>{selectedPage ? count.format(selectedPage.configured_total) : "—"}</strong></div>
        <div><span>Matching tickers</span><strong>{selectedPage ? count.format(selectedPage.total) : "—"}</strong></div>
        <div><span>Universe updated</span><strong>{selectedPage ? time(selectedPage.configuration_as_of) : "—"}</strong></div>
      </div>

      <div className="table-tools market-data__tools">
        <label htmlFor="market-data-search">Search tickers or companies
          <input id="market-data-search" type="search" value={search} maxLength={100}
            placeholder="Symbol or company name" onChange={event => { setSearch(event.target.value); setOffset(0); }} />
        </label>
        <label htmlFor="market-data-filter">Stored data
          <select id="market-data-filter" value={filter} onChange={event => {
            setFilter(event.target.value as DataFilter); setOffset(0);
          }}><option value="all">All configured</option><option value="has_data">Has data</option>
            <option value="no_data">No data</option></select>
        </label>
      </div>
      {loading && <p role="status">Loading configured tickers…</p>}
      {error && <p role="alert" className="notice notice--error">Coverage is unavailable: {error}</p>}
      {selectedPage && <>
        <div className="table-scroll"><table className="market-data__table"><thead><tr>
          <th>Ticker</th><th>Company</th><th>Series</th><th>Universe</th><th>{stream === "daily" ? "Rows · 30 days" : "Stored rows"}</th><th>Trading days</th>
          <th>First date</th><th>Latest date</th><th>Last ingested</th>
        </tr></thead><tbody>{selectedPage.items.map(item => <tr key={`${item.listing_id}:${item.contract_id ?? "equity"}`}>
          <td><button className="market-data__ticker" type="button" onClick={() => setSelected(item)}
            aria-label={`View coverage for ${item.symbol}${item.contract_id ? " future" : ""}`}>{item.symbol}</button></td>
          <td>{item.name || "—"}</td><td>{item.contract_id ? "Future" : "Listing"}</td><td>{item.universe}</td>
          <td>{item.rows ? count.format(item.rows) : <span className="market-data__empty">No data</span>}</td>
          <td>{count.format(item.trading_days)}</td><td>{item.first_date || "—"}</td>
          <td>{item.latest_date || "—"}</td><td>{time(item.last_ingested_at)}</td>
        </tr>)}</tbody></table></div>
        {!selectedPage.items.length && <p className="notice">No configured tickers match this search and filter.</p>}
        <div className="market-data__pager"><span>{selectedPage.total
          ? `${count.format(offset + 1)}–${count.format(rangeEnd)} of ${count.format(selectedPage.total)}`
          : "0 results"}</span><div>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
          <button type="button" disabled={rangeEnd >= selectedPage.total}
            onClick={() => setOffset(offset + 50)}>Next</button>
        </div></div>
      </>}
    </section>

    {selected && <section className="inventory market-data__detail" aria-label={`${selected.symbol} coverage`}>
      <div className="section-heading"><div><span className="eyebrow">{market === "US" ? "US" : "India"} · {stream}</span>
        <h2>{selected.symbol} <small>{selected.name}</small></h2></div>
        <button type="button" onClick={() => setSelected(null)} aria-label="Close ticker details">Close</button></div>
      {detailLoading && <p role="status">Loading ticker coverage…</p>}
      {detailError && <p role="alert" className="notice notice--error">{detailError}</p>}
      {detail && <>
        <dl className="market-data__facts">
          <div><dt>{stream === "daily" ? "Rows · last 30 days" : "Stored rows"}</dt><dd>{count.format(detail.rows)}</dd></div>
          <div><dt>Trading days with data</dt><dd>{count.format(detail.trading_days)}</dd></div>
          <div><dt>First data date</dt><dd>{detail.first_date || "No data"}</dd></div>
          <div><dt>Latest data date</dt><dd>{detail.latest_date || "No data"}</dd></div>
          <div><dt>Last ingested</dt><dd>{time(detail.last_ingested_at)}</dd></div>
          <div><dt>Configured series</dt><dd>{count.format(detail.series_count)} · {detail.resolution}</dd></div>
          {detail.contract_id && <div><dt>Contract ID</dt><dd>{detail.contract_id}</dd></div>}
          <div><dt>Source</dt><dd>{detail.source}</dd></div>
          <div><dt>Universe</dt><dd>{detail.universe}</dd></div>
          {stream === "historical" && <div><dt>Recovery</dt><dd>{detail.history_complete === null
            ? "No recovery report" : detail.history_complete ? "History complete" : "In progress"}</dd></div>}
          {detail.missing_trading_days !== null && <div><dt>Missing trading days</dt>
            <dd>{count.format(detail.missing_trading_days)} <small>({detail.gap_window_from} to {detail.gap_window_to})</small></dd></div>}
        </dl>
        {detail.recovery_error && <p className="notice notice--error">Recovery: {detail.recovery_error}</p>}
        {detail.rows === 0 && <p className="notice">This ticker is configured, but no bars are stored {stream === "daily" ? "in the last 30 calendar days" : "yet"}.</p>}
      </>}
    </section>}
  </main>;
}
