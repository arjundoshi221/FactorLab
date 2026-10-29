import { useMemo, useRef, useState } from "react";

import { EmptyState, ErrorBanner, Skeleton, Tabs, type TabItem } from "../components/ui";
import type {
  FuturesContract, FuturesGroup, FuturesRange, FuturesResponse, FuturesRoot, FuturesSeries,
} from "../futuresTypes";
import { errorMessage, useRemote } from "../shared/api";
import { formatCompact, formatRelative } from "../shared/format";
import { useUrlParams } from "../shared/urlState";
import "./Indexes.css"; // shared chart primitives: index-grid, index-axis, index-crosshair, index-tooltip, index-toggle
import "./Futures.css";

const GROUPS: { id: FuturesGroup; label: string }[] = [
  { id: "index", label: "Equity index" },
  { id: "energy", label: "Energy" },
  { id: "metals", label: "Metals" },
  { id: "rates", label: "Rates" },
  { id: "fx", label: "FX" },
  { id: "other", label: "Other" },
];
const RANGES: { id: FuturesRange; label: string }[] = [
  { id: "1d", label: "1D" }, { id: "5d", label: "5D" }, { id: "1m", label: "1M" },
  { id: "3m", label: "3M" }, { id: "1y", label: "1Y" }, { id: "max", label: "Max" },
];
const RESOLUTION: Record<FuturesSeries["resolution"], string> = {
  "1min": "last trade per minute", "15min": "last trade per 15 minutes", daily: "daily close",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Dark categorical slot 1 (validated against the hub panel #12161b, as on the Indexes chart);
// the line turns red when the range ends below where it started.
const UP_COLOR = "#3987e5";
const DOWN_COLOR = "#e66767";
const NY_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export const price = (value: number | null) =>
  value === null ? "—" : value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const signed = (value: number | null, digits = 2) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: 6 })}`;
const pct = (value: number | null) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
const monthLabel = (expiry: string) => `${MONTHS[Number(expiry.slice(5, 7)) - 1]} ${expiry.slice(2, 4)}`;
const pointLabel = (t: string, daily: boolean) => daily ? t : `${NY_TIME.format(new Date(t))} ET`;

/** The price a contract is valued at: the last trade, else the previous settlement. */
const markPrice = (contract: FuturesContract) => contract.last ?? contract.settlement_prev;

export function structure(contracts: FuturesContract[]): { label: string; spread: number } | null {
  const priced = contracts.filter((c) => markPrice(c) !== null);
  if (priced.length < 2) return null;
  const front = markPrice(priced[0])!, back = markPrice(priced[priced.length - 1])!;
  const spread = back / front - 1;
  const label = Math.abs(spread) < 0.0005 ? "Flat" : spread > 0 ? "Contango" : "Backwardation";
  return { label, spread };
}

export function Futures({ embedded = false }: { embedded?: boolean }) {
  const { data, error, loading, refresh } = useRemote<FuturesResponse>("/hub/api/v1/us/futures", 60_000);
  const [params, setParams] = useUrlParams();
  const items = data?.items ?? [];
  const groups = GROUPS.filter((g) => items.some((r) => r.group === g.id));
  const group = groups.find((g) => g.id === params.get("group"))?.id ?? groups[0]?.id;
  const roots = items.filter((r) => r.group === group);
  const root = roots.find((r) => r.root === params.get("root")) ?? roots[0];

  const tabs: TabItem<FuturesGroup>[] = groups.map((g) => ({ id: g.id, label: g.label }));
  const select = (change: Record<string, string | null>) =>
    setParams((p) => { for (const [k, v] of Object.entries(change)) { if (v) p.set(k, v); else p.delete(k); } });

  const Root = embedded ? "section" : "main";
  return <Root className="futures" aria-label={embedded ? "Futures" : undefined}>
    {!embedded && <section className="hero"><div><span className="eyebrow">US futures · Schwab quotes every minute</span>
      <h1>Futures</h1>
      <p>Each recorded root's live contracts; pick an expiry to chart that contract's own price history. No rolls or back-adjustment.</p></div></section>}
    {error !== null && <ErrorBanner onRetry={refresh}>{errorMessage(error)}{data ? " The last snapshot remains visible." : ""}</ErrorBanner>}
    {loading && !data && <Skeleton rows={6} />}
    {data && !items.length && <EmptyState title="No futures recorded yet">
      The futures recorder writes roots and contracts on its first session refresh.</EmptyState>}
    {data && group && <>
      <div className="futures-status">
        <span className={`futures-market ${data.market_open ? "is-open" : ""}`}>{data.market_open ? "Globex open" : "Globex closed"}</span>
        <span>Checked {formatRelative(data.generated_at)}</span>
      </div>
      <Tabs tabs={tabs} active={group} label="Futures group" onChange={(id) => select({ group: id, root: null, contract: null })} />
      <div className="futures-roots" role="group" aria-label="Futures root">
        {roots.map((r) => <RootChip key={r.root} root={r} active={r.root === root?.root}
          onSelect={() => select({ group, root: r.root, contract: null })} />)}
      </div>
      {root && <RootDetail root={root} />}
    </>}
  </Root>;
}

function RootChip({ root, active, onSelect }: { root: FuturesRoot; active: boolean; onSelect: () => void }) {
  const front = root.contracts.find((c) => c.last !== null) ?? root.contracts[0];
  return <button type="button" className={`futures-chip${active ? " active" : ""}`} aria-pressed={active} onClick={onSelect}>
    <strong>{root.root}</strong>
    <small>{root.name}</small>
    <span>{price(front?.last ?? null)} <em className={tone(front?.change_pct ?? null)}>{pct(front?.change_pct ?? null)}</em></span>
  </button>;
}

const tone = (value: number | null) => value === null || value === 0 ? "" : value > 0 ? "up" : "down";

function RootDetail({ root }: { root: FuturesRoot }) {
  const [params, setParams] = useUrlParams();
  const shape = structure(root.contracts);
  // The URL names the contract by symbol; an unknown or expired one falls back to the front traded contract.
  const contract = root.contracts.find((c) => c.symbol === params.get("contract"))
    ?? root.contracts.find((c) => c.last !== null) ?? root.contracts[0];
  const range = RANGES.find((r) => r.id === params.get("range"))?.id ?? "5d";
  const pick = (key: string, value: string) => setParams((p) => { p.set(key, value); });
  return <section className="inventory futures-detail">
    <div className="section-heading"><div><span className="eyebrow">{root.root} · {root.contracts.length} contracts</span>
      <h2>{root.name}</h2></div>
      {shape && <p className="futures-shape"><strong>{shape.label}</strong> front to back {pct(shape.spread)}</p>}
    </div>
    {contract && <>
      <div className="futures-controls">
        <div className="index-toggle" role="group" aria-label="Expiry">
          {root.contracts.map((c) => <button key={c.contract_id} type="button" title={c.symbol}
            aria-pressed={c.contract_id === contract.contract_id} className={c.contract_id === contract.contract_id ? "active" : ""}
            onClick={() => pick("contract", c.symbol)}>{monthLabel(c.expiry)}</button>)}
        </div>
        <div className="index-toggle" role="group" aria-label="Range">
          {RANGES.map((r) => <button key={r.id} type="button" aria-pressed={r.id === range}
            className={r.id === range ? "active" : ""} onClick={() => pick("range", r.id)}>{r.label}</button>)}
        </div>
      </div>
      <SeriesChart contract={contract} range={range} />
    </>}
    <ContractTable contracts={root.contracts} selected={contract?.contract_id} onSelect={(c) => pick("contract", c.symbol)} />
  </section>;
}

function SeriesChart({ contract, range }: { contract: FuturesContract; range: FuturesRange }) {
  const intraday = range === "1d" || range === "5d";
  const { data, error, loading } = useRemote<FuturesSeries>(
    `/hub/api/v1/us/futures/${encodeURIComponent(contract.contract_id)}/series?range=${range}`, intraday ? 60_000 : 0);
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 880, height = 280, pad = { top: 20, right: 32, bottom: 32, left: 72 };
  // Ignore a previous contract's or range's response while the next one loads.
  const current = data && data.contract_id === contract.contract_id && data.range === range ? data : null;
  const points = useMemo(() => current?.points ?? [], [current]);
  if (!current && loading) return <Skeleton rows={4} />;
  if (!current && error !== null) return <p className="notice">{errorMessage(error)}</p>;
  if (!current || points.length < 2) return <p className="notice">Not enough recorded prices to chart {contract.symbol} over this range yet.</p>;
  const daily = current.resolution === "daily";
  // The previous settlement is the 1D reference: the table's change is measured against it.
  const settle = range === "1d" ? contract.settlement_prev : null;
  const values = points.map((p) => p.price).concat(settle === null ? [] : [settle]);
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || Math.abs(hi) * 0.01 || 1;
  // Points sit at their index, so closed sessions and weekends leave no gaps.
  const x = (i: number) => pad.left + (i / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + (1 - (v - lo) / span) * (height - pad.top - pad.bottom);
  const ticks = [lo, lo + span / 2, lo + span];
  const first = points[0].price, last = points[points.length - 1].price;
  const color = last >= first ? UP_COLOR : DOWN_COLOR;
  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const ratio = (((event.clientX - box.left) / box.width) * width - pad.left) / (width - pad.left - pad.right);
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
  };
  const hovered = hover === null ? null : points[hover];
  return <figure className="futures-chart">
    <figcaption>{contract.symbol} · expires {contract.expiry} · {RESOLUTION[current.resolution]} · {pct(last / first - 1)} over the range</figcaption>
    <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${contract.symbol} price history`}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      {ticks.map((t) => <g key={t}><line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="index-grid" />
        <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" className="index-axis">{price(Number(t.toPrecision(8)))}</text></g>)}
      {settle !== null && <line data-testid="settle-line" x1={pad.left} x2={width - pad.right} y1={y(settle)} y2={y(settle)} className="index-base" />}
      <text x={pad.left} y={height - 8} className="index-axis">{pointLabel(points[0].t, daily)}</text>
      <text x={width - pad.right} y={height - 8} textAnchor="end" className="index-axis">{pointLabel(points[points.length - 1].t, daily)}</text>
      <polyline data-testid="series-line" fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round"
        points={points.map((p, i) => `${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ")} />
      {hovered && <g>
        <line x1={x(hover!)} x2={x(hover!)} y1={pad.top} y2={height - pad.bottom} className="index-crosshair" />
        <circle cx={x(hover!)} cy={y(hovered.price)} r="4" fill={color} stroke="#12161b" strokeWidth="2" />
      </g>}
    </svg>
    {hovered && <div className="index-tooltip" role="status"><strong>{pointLabel(hovered.t, daily)}</strong>
      <span><i style={{ background: color }} />{contract.symbol}<b>{price(hovered.price)}</b></span>
      <span><i />From range start<b>{signed(hovered.price - first)} ({pct(hovered.price / first - 1)})</b></span></div>}
    {settle !== null && <p className="futures-legend">Dashed line: previous settlement {price(settle)}.</p>}
  </figure>;
}

function ContractTable({ contracts, selected, onSelect }: {
  contracts: FuturesContract[]; selected?: string; onSelect: (contract: FuturesContract) => void;
}) {
  return <div className="table-scroll"><table className="futures-table" aria-label="Contracts">
    <thead><tr><th>Contract</th><th>Expiry</th><th>Last</th><th>Bid</th><th>Ask</th><th>Change</th>
      <th>Volume</th><th>Open interest</th><th>Prev settle</th><th>Quote</th></tr></thead>
    <tbody>{contracts.map((c) => <tr key={c.contract_id}
      className={[c.stale ? "is-stale" : "", c.contract_id === selected ? "is-selected" : ""].join(" ").trim() || undefined}>
      <td><button type="button" className="futures-pick" aria-pressed={c.contract_id === selected}
        onClick={() => onSelect(c)}><code>{c.symbol}</code></button></td>
      <td>{c.expiry}</td>
      <td>{price(c.last)}</td>
      <td>{price(c.bid)}</td>
      <td>{price(c.ask)}</td>
      <td className={tone(c.change)}>{signed(c.change)} <small>{pct(c.change_pct)}</small></td>
      <td>{formatCompact(c.day_volume)}</td>
      <td>{formatCompact(c.oi)}</td>
      <td>{price(c.settlement_prev)}</td>
      <td>{c.observed_at ? formatRelative(c.observed_at) : "No quote"}{c.stale && <span className="futures-stale"> · stale</span>}</td>
    </tr>)}</tbody>
  </table></div>;
}
