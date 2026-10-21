import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ConstituentsResponse, IndexOverview, PerformanceResponse, Period, Returns, Series, SectorPerformance, Weighting,
} from "../indexTypes";
import "./Indexes.css";

const PERIOD_LABELS: Record<Period, string> = { "1d": "1D", "1w": "1W", "1m": "1M", "3m": "3M", ytd: "YTD", "1y": "1Y" };
const RANGES = { "1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5, Max: 365 * 11 } as const;
type Range = keyof typeof RANGES;
// Dark categorical slots 1-4 (validated against the hub panel #12161b); ETFs are dashed.
const LINES = [
  { key: "ew", label: "FactorLab equal weight", color: "#3987e5", dashed: false },
  { key: "cw", label: "FactorLab cap weight", color: "#d95926", dashed: false },
  { key: "cw_etf", label: "Sector SPDR (cap)", color: "#199e70", dashed: true },
  { key: "ew_etf", label: "Invesco sector EW", color: "#c98500", dashed: true },
] as const;

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Index data could not load (${response.status}).`);
  return response.json() as Promise<T>;
}

const pct = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
const level = (value: number | null) =>
  value === null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

// Diverging blue (gain) <-> red (loss) around a neutral panel; text keeps ink tokens.
function heat(value: number | null, scale: number): string {
  if (value === null) return "transparent";
  const t = Math.min(Math.abs(value) / scale, 1);
  const pole = value >= 0 ? "57, 135, 229" : "230, 103, 103";
  return `rgba(${pole}, ${(0.08 + 0.5 * t).toFixed(3)})`;
}
const SCALE: Record<Period, number> = { "1d": 0.02, "1w": 0.04, "1m": 0.08, "3m": 0.15, ytd: 0.3, "1y": 0.35 };

export function Indexes() {
  const [weighting, setWeighting] = useState<Weighting>("cw");
  const [overview, setOverview] = useState<IndexOverview | null>(null);
  const [performance, setPerformance] = useState<PerformanceResponse | null>(null);
  const [selected, setSelected] = useState<string>("45");
  const [range, setRange] = useState<Range>("1Y");
  const [series, setSeries] = useState<Series[]>([]);
  const [constituents, setConstituents] = useState<ConstituentsResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void Promise.all([
      read<IndexOverview>("/hub/api/v1/us/indexes", controller.signal),
      read<PerformanceResponse>(`/hub/api/v1/us/sectors/performance?weighting=${weighting}`, controller.signal),
    ]).then(([summary, sectors]) => { setOverview(summary); setPerformance(sectors); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [weighting]);

  const sector = performance?.sectors.find(item => item.sector_id === selected) ?? null;

  useEffect(() => {
    if (!sector) return;
    const controller = new AbortController();
    const end = performance?.as_of ?? new Date().toISOString().slice(0, 10);
    const start = new Date(Date.parse(end) - RANGES[range] * 86400000).toISOString().slice(0, 10);
    const codes = [`us_gics_${sector.sector_id}_ew`, `us_gics_${sector.sector_id}_cw`, sector.cw_etf, sector.ew_etf];
    const params = new URLSearchParams({ date_from: start, date_to: end });
    codes.forEach(code => params.append("codes", code));
    void Promise.all([
      read<{ items: Series[] }>(`/hub/api/v1/us/indexes/series?${params}`, controller.signal),
      read<ConstituentsResponse>(`/hub/api/v1/us/indexes/${sector.code}/constituents?limit=25`, controller.signal),
    ]).then(([lines, members]) => { setSeries(lines.items); setConstituents(members); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)); });
    return () => controller.abort();
  }, [sector, range, performance?.as_of]);

  return <main className="indexes">
    <section className="hero"><div><span className="eyebrow">US indexes · Schwab closes · iShares IWV sectors</span>
      <h1>Indexes and sectors</h1>
      <p>Official US indexes, plus FactorLab's own GICS sector indexes over the Russell 3000 proxy, equal- and cap-weighted.</p></div></section>
    {error && <div className="notice notice--error" role="alert">{error}</div>}
    {loading && <p role="status">Loading indexes…</p>}
    {overview && <TrackedStrip overview={overview} />}
    {performance && <section className="inventory">
      <div className="section-heading"><div><span className="eyebrow">Sector performance{performance.as_of ? ` · as of ${performance.as_of}` : ""}</span>
        <h2>GICS sectors</h2></div>
        <div className="index-toggle" role="group" aria-label="Weighting">
          {(["cw", "ew"] as Weighting[]).map(value => <button key={value} aria-pressed={weighting === value}
            className={weighting === value ? "active" : ""} onClick={() => setWeighting(value)}>
            {value === "cw" ? "Cap weight" : "Equal weight"}</button>)}
        </div></div>
      <SectorTable performance={performance} weighting={weighting} selected={selected} onSelect={setSelected} />
      {!performance.as_of && <p className="notice">No index levels yet: the index service writes them after the first daily run.</p>}
    </section>}
    {sector && <section className="inventory index-detail">
      <div className="section-heading"><div><span className="eyebrow">Sector detail</span><h2>{sector.name}</h2></div>
        <div className="index-toggle" role="group" aria-label="Range">
          {(Object.keys(RANGES) as Range[]).map(value => <button key={value} aria-pressed={range === value}
            className={range === value ? "active" : ""} onClick={() => setRange(value)}>{value}</button>)}
        </div></div>
      <ComparisonChart series={series} sector={sector} />
      <div className="index-detail__grid">
        <Movers sector={sector} />
        <Constituents data={constituents} weighting={weighting} />
      </div>
    </section>}
    <p className="us-note">Price return only. Constructed history before the first sector and membership snapshot uses
      today's roster (survivorship bias); see ADR 0017.</p>
  </main>;
}

function TrackedStrip({ overview }: { overview: IndexOverview }) {
  return <section className="index-strip" aria-label="Tracked indexes">
    {overview.tracked.map(item => <article key={item.code} className="index-card">
      <span className="eyebrow">{item.code}</span>
      <strong>{level(item.value)}</strong>
      <small>{item.name}</small>
      <span className="index-card__change">{item.change === null ? "No data yet" :
        `${item.change >= 0 ? "▲" : "▼"} ${pct(item.change, 2)}`}</span>
    </article>)}
  </section>;
}

function SectorTable({ performance, weighting, selected, onSelect }: {
  performance: PerformanceResponse; weighting: Weighting; selected: string; onSelect: (id: string) => void;
}) {
  const benchmark = weighting === "cw" ? "cw_etf" : "ew_etf";
  return <div className="table-scroll"><table className="sector-table" aria-label="Sector returns">
    <thead><tr><th>Sector</th>{performance.periods.map(p => <th key={p} className="number-cell">{PERIOD_LABELS[p]}</th>)}
      <th className="number-cell">Benchmark ETF · YTD</th></tr></thead>
    <tbody>{performance.sectors.map(row => <tr key={row.sector_id} className={row.sector_id === selected ? "us-selected" : ""}>
      <td><button className="us-symbol" onClick={() => onSelect(row.sector_id)}>{row.name}</button>
        {!row.complete && row.level !== null && <small className="us-subtitle">Latest day partially priced</small>}</td>
      {performance.periods.map(p => <ReturnCell key={p} value={row.returns[weighting][p]} period={p} />)}
      <td className="number-cell"><code>{weighting === "cw" ? row.cw_etf : row.ew_etf}</code> {pct(row.returns[benchmark].ytd)}</td>
    </tr>)}</tbody>
    <tfoot><BenchmarkRow label="Russell 3000 proxy" returns={performance.benchmarks[weighting === "cw" ? "composite_cw" : "composite_ew"]} periods={performance.periods} />
      <BenchmarkRow label={weighting === "cw" ? "S&P 500 ($SPX)" : "S&P 500 equal weight (RSP)"}
        returns={performance.benchmarks[weighting === "cw" ? "$SPX" : "RSP"]} periods={performance.periods} /></tfoot>
  </table></div>;
}

function ReturnCell({ value, period }: { value: number | null; period: Period }) {
  return <td className="number-cell heat-cell" style={{ background: heat(value, SCALE[period]) }}
    title={`${PERIOD_LABELS[period]} ${pct(value, 2)}`}>{pct(value)}</td>;
}

function BenchmarkRow({ label, returns, periods }: { label: string; returns: Returns; periods: Period[] }) {
  return <tr><th>{label}</th>{periods.map(p => <td key={p} className="number-cell">{pct(returns[p])}</td>)}<td /></tr>;
}

function ComparisonChart({ series, sector }: { series: Series[]; sector: SectorPerformance }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 880, height = 300, pad = { top: 16, right: 150, bottom: 28, left: 48 };
  const lines = useMemo(() => {
    const codes = { ew: `us_gics_${sector.sector_id}_ew`, cw: `us_gics_${sector.sector_id}_cw`, cw_etf: sector.cw_etf, ew_etf: sector.ew_etf };
    return LINES.map(spec => {
      const found = series.find(item => item.code === codes[spec.key]);
      const points = found?.points ?? [];
      const base = points[0]?.value;
      return { ...spec, code: codes[spec.key],
        points: base ? points.map(p => ({ date: p.trade_date, value: (p.value / base) * 100 })) : [] };
    }).filter(line => line.points.length > 1);
  }, [series, sector]);
  const dates = useMemo(() => Array.from(new Set(lines.flatMap(l => l.points.map(p => p.date)))).sort(), [lines]);
  if (!lines.length) return <p className="notice">No history for this sector yet.</p>;
  const values = lines.flatMap(l => l.points.map(p => p.value));
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const x = (d: string) => pad.left + (dates.indexOf(d) / Math.max(dates.length - 1, 1)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + (1 - (v - lo) / span) * (height - pad.top - pad.bottom);
  const ticks = [lo, lo + span / 2, hi];
  const hoverDate = hover === null ? null : dates[hover];
  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const ratio = ((event.clientX - box.left) / box.width * width - pad.left) / (width - pad.left - pad.right);
    setHover(Math.max(0, Math.min(dates.length - 1, Math.round(ratio * (dates.length - 1)))));
  };
  return <figure className="index-chart">
    <figcaption>Rebased to 100 at {dates[0]} · {sector.name}</figcaption>
    <ul className="index-legend">{lines.map(l => <li key={l.key}>
      <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={l.color} strokeWidth="2" strokeDasharray={l.dashed ? "5 3" : undefined} /></svg>
      {l.label} <code>{l.code}</code></li>)}</ul>
    <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${sector.name} index comparison`}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      {ticks.map(t => <g key={t}><line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="index-grid" />
        <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" className="index-axis">{t.toFixed(0)}</text></g>)}
      <line x1={pad.left} x2={width - pad.right} y1={y(100)} y2={y(100)} className="index-base" />
      <text x={pad.left} y={height - 8} className="index-axis">{dates[0]}</text>
      <text x={width - pad.right} y={height - 8} textAnchor="end" className="index-axis">{dates[dates.length - 1]}</text>
      {lines.map(l => {
        const last = l.points[l.points.length - 1];
        return <g key={l.key}>
          <polyline fill="none" stroke={l.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={l.dashed ? "6 4" : undefined}
            points={l.points.map(p => `${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")} />
          <text x={x(last.date) + 8} y={y(last.value) + 4} className="index-end-label">{l.code.startsWith("us_gics") ? l.key.toUpperCase() : l.code} {last.value.toFixed(1)}</text>
        </g>;
      })}
      {hoverDate && <g>
        <line x1={x(hoverDate)} x2={x(hoverDate)} y1={pad.top} y2={height - pad.bottom} className="index-crosshair" />
        {lines.map(l => { const p = l.points.find(point => point.date === hoverDate);
          return p ? <circle key={l.key} cx={x(p.date)} cy={y(p.value)} r="4" fill={l.color} stroke="#12161b" strokeWidth="2" /> : null; })}
      </g>}
    </svg>
    {hoverDate && <div className="index-tooltip" role="status"><strong>{hoverDate}</strong>
      {lines.map(l => { const p = l.points.find(point => point.date === hoverDate);
        return <span key={l.key}><i style={{ background: l.color }} />{l.label}<b>{p ? p.value.toFixed(2) : "—"}</b></span>; })}</div>}
  </figure>;
}

function Movers({ sector }: { sector: SectorPerformance }) {
  const list = (items: SectorPerformance["movers"]["top"]) => items.length ? <table><tbody>{items.map((m, i) =>
    <tr key={i}><td><a href={`/us?symbol=${m.symbol ?? ""}`}>{m.symbol ?? "—"}</a></td>
      <td className="number-cell">{pct(m.ret_1d, 2)}</td><td className="number-cell">{(m.contribution * 10000).toFixed(1)} bp</td></tr>)}
  </tbody></table> : <p className="us-note">No priced constituents yet.</p>;
  return <article><h3>Top contributors today</h3>{list(sector.movers.top)}<h3>Bottom contributors</h3>{list(sector.movers.bottom)}</article>;
}

function Constituents({ data, weighting }: { data: ConstituentsResponse | null; weighting: Weighting }) {
  if (!data) return <article><p role="status">Loading constituents…</p></article>;
  return <article><h3>Largest weights · {weighting === "cw" ? "cap" : "equal"} weight
    <small className="us-note"> {data.total.toLocaleString()} constituents{data.trade_date ? ` · ${data.trade_date}` : ""}</small></h3>
    {data.items.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Name</th>
      <th className="number-cell">Weight</th><th className="number-cell">1D</th></tr></thead>
      <tbody>{data.items.map(item => <tr key={item.listing_id}><td><a href={`/us?symbol=${item.symbol ?? ""}`}>{item.symbol ?? "—"}</a></td>
        <td>{item.name ?? ""}</td><td className="number-cell">{(item.weight * 100).toFixed(2)}%</td>
        <td className="number-cell">{pct(item.ret_1d, 2)}</td></tr>)}</tbody></table></div>
      : <p className="us-note">No constituents recorded yet.</p>}
  </article>;
}
