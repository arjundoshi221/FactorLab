import { useMemo, useRef, useState } from "react";

import { EmptyState, ErrorBanner, Skeleton, Tabs, type TabItem } from "../components/ui";
import type { FuturesContract, FuturesGroup, FuturesResponse, FuturesRoot } from "../futuresTypes";
import { errorMessage, useRemote } from "../shared/api";
import { formatCompact, formatRelative } from "../shared/format";
import { useUrlParams } from "../shared/urlState";
import "./Indexes.css"; // shared chart primitives: index-grid, index-axis, index-crosshair, index-tooltip
import "./Futures.css";

const GROUPS: { id: FuturesGroup; label: string }[] = [
  { id: "index", label: "Equity index" },
  { id: "energy", label: "Energy" },
  { id: "metals", label: "Metals" },
  { id: "rates", label: "Rates" },
  { id: "fx", label: "FX" },
  { id: "other", label: "Other" },
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Dark categorical slot 1 (validated against the hub panel #12161b, as on the Indexes chart).
const CURVE_COLOR = "#3987e5";

export const price = (value: number | null) =>
  value === null ? "—" : value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const signed = (value: number | null, digits = 2) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: 6 })}`;
const pct = (value: number | null) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
const monthLabel = (expiry: string) => `${MONTHS[Number(expiry.slice(5, 7)) - 1]} ${expiry.slice(2, 4)}`;
const dayMs = (expiry: string) => Date.parse(`${expiry}T00:00:00Z`);

/** The price a curve point is drawn at: the last trade, else the previous settlement. */
const curvePrice = (contract: FuturesContract) => contract.last ?? contract.settlement_prev;

export function structure(contracts: FuturesContract[]): { label: string; spread: number } | null {
  const priced = contracts.filter((c) => curvePrice(c) !== null);
  if (priced.length < 2) return null;
  const front = curvePrice(priced[0])!, back = curvePrice(priced[priced.length - 1])!;
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
  return <Root className="futures" aria-label={embedded ? "Futures curves" : undefined}>
    {!embedded && <section className="hero"><div><span className="eyebrow">US futures · Schwab quotes every minute</span>
      <h1>Futures curves</h1>
      <p>Each recorded root's live contracts in expiry order, priced at the latest quote. No rolls or back-adjustment.</p></div></section>}
    {error !== null && <ErrorBanner onRetry={refresh}>{errorMessage(error)}{data ? " The last snapshot remains visible." : ""}</ErrorBanner>}
    {loading && !data && <Skeleton rows={6} />}
    {data && !items.length && <EmptyState title="No futures recorded yet">
      The futures recorder writes roots and contracts on its first session refresh.</EmptyState>}
    {data && group && <>
      <div className="futures-status">
        <span className={`futures-market ${data.market_open ? "is-open" : ""}`}>{data.market_open ? "Globex open" : "Globex closed"}</span>
        <span>Checked {formatRelative(data.generated_at)}</span>
      </div>
      <Tabs tabs={tabs} active={group} label="Futures group" onChange={(id) => select({ group: id, root: null })} />
      <div className="futures-roots" role="group" aria-label="Futures root">
        {roots.map((r) => <RootChip key={r.root} root={r} active={r.root === root?.root}
          onSelect={() => select({ group, root: r.root })} />)}
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
  const shape = structure(root.contracts);
  return <section className="inventory futures-detail">
    <div className="section-heading"><div><span className="eyebrow">{root.root} · {root.contracts.length} contracts</span>
      <h2>{root.name}</h2></div>
      {shape && <p className="futures-shape"><strong>{shape.label}</strong> front to back {pct(shape.spread)}</p>}
    </div>
    <CurveChart root={root} />
    <ContractTable contracts={root.contracts} />
  </section>;
}

function CurveChart({ root }: { root: FuturesRoot }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 880, height = 280, pad = { top: 20, right: 32, bottom: 32, left: 72 };
  const points = useMemo(() => root.contracts
    .map((c) => ({ contract: c, t: dayMs(c.expiry), value: curvePrice(c) }))
    .filter((p): p is { contract: FuturesContract; t: number; value: number } => p.value !== null), [root]);
  if (points.length < 2) return <p className="notice">Not enough priced contracts to draw a curve for {root.root} yet.</p>;
  const t0 = points[0].t, t1 = points[points.length - 1].t, tSpan = t1 - t0 || 1;
  const values = points.map((p) => p.value);
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || Math.abs(hi) * 0.01 || 1;
  const x = (t: number) => pad.left + ((t - t0) / tSpan) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + (1 - (v - lo) / span) * (height - pad.top - pad.bottom);
  const ticks = [lo, lo + span / 2, lo + span];
  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const at = ((event.clientX - box.left) / box.width) * width;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(x(p.t) - at) < Math.abs(x(points[best].t) - at)) best = i; });
    setHover(best);
  };
  const hovered = hover === null ? null : points[hover];
  return <figure className="futures-chart">
    <figcaption>{root.root} curve · price by contract expiry · last trade, else previous settlement</figcaption>
    <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${root.name} futures curve`}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      {ticks.map((t) => <g key={t}><line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="index-grid" />
        <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" className="index-axis">{price(Number(t.toPrecision(8)))}</text></g>)}
      {points.map((p) => <text key={p.contract.symbol} x={x(p.t)} y={height - 10} textAnchor="middle" className="index-axis">{monthLabel(p.contract.expiry)}</text>)}
      <polyline fill="none" stroke={CURVE_COLOR} strokeWidth="2" strokeLinejoin="round"
        points={points.map((p) => `${x(p.t).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")} />
      {points.map((p, i) => <circle key={p.contract.symbol} data-testid="curve-point" cx={x(p.t)} cy={y(p.value)}
        r={i === hover ? 5 : 3.5} fill={p.contract.last === null || p.contract.stale ? "#12161b" : CURVE_COLOR}
        stroke={CURVE_COLOR} strokeWidth="2" />)}
      {hovered && <line x1={x(hovered.t)} x2={x(hovered.t)} y1={pad.top} y2={height - pad.bottom} className="index-crosshair" />}
    </svg>
    {hovered && <div className="index-tooltip" role="status"><strong>{hovered.contract.symbol} · expires {hovered.contract.expiry}</strong>
      <span><i style={{ background: CURVE_COLOR }} />Price<b>{price(hovered.value)}</b></span>
      <span><i />Change<b>{signed(hovered.contract.change)} ({pct(hovered.contract.change_pct)})</b></span></div>}
    <p className="futures-legend">Hollow points have no trade yet or a stale quote and sit at the previous settlement or last quote.</p>
  </figure>;
}

function ContractTable({ contracts }: { contracts: FuturesContract[] }) {
  return <div className="table-scroll"><table className="futures-table" aria-label="Contracts">
    <thead><tr><th>Contract</th><th>Expiry</th><th>Last</th><th>Bid</th><th>Ask</th><th>Change</th>
      <th>Volume</th><th>Open interest</th><th>Prev settle</th><th>Quote</th></tr></thead>
    <tbody>{contracts.map((c) => <tr key={c.contract_id} className={c.stale ? "is-stale" : ""}>
      <td><code>{c.symbol}</code></td>
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
