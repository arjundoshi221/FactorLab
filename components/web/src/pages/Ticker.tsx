import { useState, type ReactNode } from "react";
import { PageHeader, Panel, Segmented } from "../components/ui";
import { errorMessage, useRemote } from "../shared/api";
import { AreaChart, Candles } from "../shared/charts";
import { formatDate } from "../shared/format";
import { direction, price, signed, signedPct } from "../shared/market";
import { tickerHref } from "../shared/urlState";
import type { Identity, PriceBar, PriceHistory, PriceRange } from "../instrumentTypes";
import type { MatrixCell, MatrixPage } from "../matrixTypes";
import { CellPill, MATRIX_API } from "./DataMatrix";
import "./Ticker.css";

const API = "/hub/api/v1/market-data";
const ranges: { id: PriceRange; label: string }[] = [
  { id: "1d", label: "1D" }, { id: "5d", label: "5D" }, { id: "1m", label: "1M" }, { id: "3m", label: "3M" },
  { id: "6m", label: "6M" }, { id: "ytd", label: "YTD" }, { id: "1y", label: "1Y" }, { id: "5y", label: "5Y" }, { id: "max", label: "Max" },
];
const zones: Record<string, string> = { US: "America/New_York", IN: "Asia/Kolkata" };
const currencies: Record<string, string> = { US: "$", IN: "₹" };
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const volume = (value: number | null | undefined) => value === null || value === undefined ? "—" : compact.format(value);
const shift = (day: string, days: number) => { const d = new Date(`${day.slice(0, 10)}T00:00:00Z`); d.setUTCDate(d.getUTCDate() - days); return d.toISOString().slice(0, 10); };
const last = <T,>(items: T[]) => items[items.length - 1];

function axisTime(range: PriceRange, zone: string) {
  if (range !== "1d" && range !== "5d") return (t: string) => formatDate(t.slice(0, 10));
  const format = new Intl.DateTimeFormat("en-US", { ...(range === "5d" ? { month: "short", day: "numeric" } : {}), hour: "2-digit", minute: "2-digit", timeZone: zone });
  return (t: string) => format.format(new Date(t));
}

/** Headline numbers from a year of daily bars. Returns are close to close. */
export function summarize(bars: PriceBar[]) {
  if (!bars.length) return null;
  const end = last(bars), prev = bars[bars.length - 2];
  const at = (day: string) => [...bars].reverse().find(b => b.t.slice(0, 10) <= day);
  const ret = (from?: PriceBar) => from && from !== end && from.close ? end.close / from.close - 1 : null;
  const recent = bars.slice(-30).flatMap(b => b.volume === null ? [] : [b.volume]);
  return {
    end, prev,
    change: prev ? end.close - prev.close : null,
    changePct: ret(prev),
    high: Math.max(...bars.map(b => b.high)), low: Math.min(...bars.map(b => b.low)),
    avgVolume: recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null,
    returns: [
      ["1M", ret(at(shift(end.t, 30)))], ["3M", ret(at(shift(end.t, 91)))],
      ["YTD", ret(at(`${Number(end.t.slice(0, 4)) - 1}-12-31`))], ["1Y", ret(at(shift(end.t, 365)))],
    ] as [string, number | null][],
  };
}

function Num({ value, children }: { value: number | null; children: ReactNode }) {
  return <span className={`t-num t-num--${direction(value)}`}>{children}</span>;
}

function PriceChart({ listingId, symbol, zone, daily }: { listingId: string; symbol: string; zone: string; daily: PriceBar[] }) {
  const [range, setRange] = useState<PriceRange>(() => {
    const asked = new URLSearchParams(window.location.search).get("range");
    return ranges.find(r => r.id === asked)?.id ?? "1y";
  });
  const [style, setStyle] = useState<"line" | "candles">("line");
  const remote = useRemote<PriceHistory>(`${API}/listings/${listingId}/prices?range=${range}`);
  const data = remote.data?.range === range ? remote.data : null;
  const bars = data?.bars ?? [];
  // Intraday moves are measured from the previous session's close, like a quote screen.
  const session = bars[0]?.t.slice(0, 10);
  const reference = range === "1d" && session ? last(daily.filter(b => b.t < session))?.close ?? null : null;
  const base = reference ?? bars[0]?.close;
  const move = bars.length && base ? last(bars).close / base - 1 : null;
  const formatTime = axisTime(range, zone);
  function pick(next: PriceRange) {
    setRange(next);
    const url = new URL(window.location.href);
    url.searchParams.set("range", next);
    window.history.replaceState(null, "", url);
  }
  return <Panel code="GP" title="Price" className="tk-chart" tools={<>
    {move !== null && <Num value={move}>{signedPct(move)}</Num>}
    <Segmented label="Chart style" value={style} onChange={setStyle} options={[{ id: "line", label: "Line" }, { id: "candles", label: "Candles" }]} />
  </>} footer={data?.source && <span>{data.resolution === "1min" ? "One-minute" : "Daily"} bars from {data.source}{data.derived ? ", rolled up from one-minute bars" : ""}{bars.length ? ` · ${formatTime(bars[0].t)} – ${formatTime(last(bars).t)}` : ""}</span>}>
    <Segmented label="Chart range" value={range} onChange={pick} options={ranges} />
    <div className="tk-chart__plot">
      {Boolean(remote.error) && <p role="alert" className="notice notice--error">{errorMessage(remote.error, "Price history could not be loaded.")}</p>}
      {!data && !remote.error && <div className="t-chart t-chart--empty" style={{ height: 320 }}>Loading prices…</div>}
      {data && !bars.length && <div className="t-chart t-chart--empty" style={{ height: 320 }}>No price bars are stored for this range.</div>}
      {data && bars.length > 0 && (style === "candles"
        ? <Candles bars={bars} label={`${symbol} candlestick chart`} height={320} format={v => price(v)} formatTime={formatTime} />
        : <AreaChart points={bars.map(b => ({ t: b.t, value: b.close }))} label={`${symbol} closing price`} height={320}
            format={v => price(v)} formatTime={formatTime} reference={reference} referenceLabel="Previous close" />)}
    </div>
  </Panel>;
}

function Stats({ summary, currency }: { summary: ReturnType<typeof summarize>; currency: string }) {
  if (!summary) return <Panel code="ST" title="Key stats"><p className="md-empty">No daily prices stored in the last year.</p></Panel>;
  const { end } = summary;
  const rows: [string, ReactNode][] = [
    ["Open", price(end.open)], ["Day range", `${price(end.low)} – ${price(end.high)}`],
    ["Previous close", price(summary.prev?.close)], ["Volume", volume(end.volume)],
    ["Avg volume (30d)", volume(summary.avgVolume)], ["52-week range", `${price(summary.low)} – ${price(summary.high)}`],
  ];
  return <Panel code="ST" title="Key stats" footer={<span>Session of {formatDate(end.t.slice(0, 10))} · prices in {currency}</span>}>
    <dl className="tk-stats">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="t-num">{value}</dd></div>)}</dl>
    <dl className="tk-returns">{summary.returns.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><Num value={value}>{signedPct(value)}</Num></dd></div>)}</dl>
  </Panel>;
}

function Line({ label, children, href, link }: { label: string; children: ReactNode; href?: string; link?: string }) {
  return <li><span>{label}</span><span>{children}</span>{href ? <a className="md-link" href={href}>{link ?? "View"} →</a> : <span />}</li>;
}

function Position({ listingId, cells }: { listingId: string; cells: Record<string, MatrixCell> }) {
  const holdings = cells.positions?.items ?? [];
  const trades = cells.trades, tags = cells.tags;
  return <Panel code="PO" title="Your position">
    {holdings.length > 0
      ? <div className="instrument-grid"><table><thead><tr><th>Account</th><th>Quantity</th><th>Market value</th><th>As of</th></tr></thead>
        <tbody>{holdings.map((h, i) => <tr key={i}><td>{String(h.broker_code).toUpperCase()} {String(h.account_id)}{h.account_mode === "paper" ? " · paper" : ""}</td>
          <td>{String(h.position)}</td><td>{h.market_value === null ? "—" : `${String(h.currency)} ${price(Number(h.market_value))}`}</td><td>{formatDate(String(h.snapshot_time).slice(0, 10))}</td></tr>)}</tbody></table></div>
      : <p className="md-muted tk-pad">Not held in the latest account snapshots.</p>}
    <ul className="tk-lines">
      <Line label="Trades" href={trades?.count ? `/portfolio/trades?listing_id=${listingId}` : undefined} link="Ledger">
        {trades?.count ? `${trades.count} orders · last ${formatDate(trades.latest?.slice(0, 10))}` : "None recorded"}</Line>
      {tags?.state === "needs_tags" && <Line label="Untagged" href={`/portfolio/trades?listing_id=${listingId}&tag=untagged`} link="Tag">{tags.count} trades need tags</Line>}
    </ul>
  </Panel>;
}

function About({ identity, matrix }: { identity: Identity; matrix: MatrixPage | null }) {
  const item = identity.instrument;
  const row = matrix?.items[0];
  const cell = (key: string) => row?.cells[key];
  const political = cell("political"), filings = cell("fundamentals"), futures = cell("futures");
  const feeds = (matrix?.columns ?? []).filter(c => c.group === "market" && c.key.startsWith("bars:") && row?.cells[c.key] && row.cells[c.key].state !== "not_configured");
  const listings = identity.related.filter(r => r.kind === "listing");
  return <Panel code="DE" title="Details">
    <ul className="tk-lines">
      <Line label="Sector" href={item.sector_id ? `/market-data?view=sectors&sector=${item.sector_id}` : undefined} link="Peers">{item.sector || "Unclassified"}</Line>
      <Line label="Type">{item.instrument_type || "—"} · {item.exchange} · {item.country}</Line>
      {political?.count ? <Line label="Congress trades" href="/political" link="Political">{political.count} disclosed · last {formatDate(political.latest?.slice(0, 10))}</Line> : null}
      {filings?.count ? <Line label="Filings">{filings.count} stored · last {formatDate(filings.latest?.slice(0, 10))}</Line> : null}
      {futures && futures.state !== "none" && futures.state !== "not_configured" && <Line label="Futures" href="/market-data?view=futures" link="Futures">{futures.detail || futures.count}</Line>}
      {feeds.map(c => <Line key={c.key} label={`${c.source ?? ""} ${c.resolution ?? ""}`.trim() || c.label}><CellPill cell={row!.cells[c.key]} column={c} /></Line>)}
    </ul>
    {listings.length > 0 && <div className="tk-related"><span>Also listed as</span>{listings.map(r => <a key={r.id} className="t-chip" href={tickerHref(r.id)}>{r.symbol} · {r.exchange}</a>)}</div>}
  </Panel>;
}

export function Ticker({ listingId }: { listingId: string }) {
  const identity = useRemote<Identity>(`${API}/instruments/listing/${listingId}`);
  const year = useRemote<PriceHistory>(`${API}/listings/${listingId}/prices?range=1y`);
  const matrix = useRemote<MatrixPage>(`${MATRIX_API}?listing_id=${listingId}&scope=all&detail=true&limit=1`);
  const item = identity.data?.instrument;
  const daily = year.data?.resolution === "daily" ? year.data.bars : [];
  const summary = summarize(daily);
  const back = <a className="md-link" href="/market-data">← Market data</a>;
  if (identity.error) return <main className="md-page"><PageHeader code="TKR" title="Ticker">{back}</PageHeader>
    <p role="alert" className="notice notice--error">{errorMessage(identity.error, "This ticker could not be loaded.")}</p></main>;
  if (!item) return <main className="md-page"><PageHeader code="TKR" title="Loading…">{back}</PageHeader><p role="status" className="md-loading">Loading ticker…</p></main>;
  const currency = currencies[item.country] ?? "";
  return <main className="md-page tk-page">
    <PageHeader code={item.symbol} title={item.name || item.symbol} eyebrow={`${item.exchange}${item.active ? "" : " · inactive"}`} stats={summary ? [
      { label: "Last", value: `${currency}${price(summary.end.close)}` },
      { label: "Change", value: `${signed(summary.change)} (${signedPct(summary.changePct)})`, tone: summary.change ? (summary.change > 0 ? "up" : "down") : undefined },
      { label: "As of", value: formatDate(summary.end.t.slice(0, 10)) },
    ] : []}>{back}</PageHeader>
    <div className="tk-top">
      <PriceChart listingId={listingId} symbol={item.symbol} zone={zones[item.country] ?? "UTC"} daily={daily} />
      <Stats summary={summary} currency={item.country === "IN" ? "INR" : item.country === "US" ? "USD" : "the listing currency"} />
    </div>
    <div className="t-grid t-grid--2 tk-bottom">
      <Position listingId={listingId} cells={matrix.data?.items[0]?.cells ?? {}} />
      <About identity={identity.data!} matrix={matrix.data} />
    </div>
    <p className="t-buildline">Listing {listingId} · <a href={`/market-data?kind=listing&id=${listingId}`}>Raw records &amp; provenance</a></p>
  </main>;
}
