import { useMemo } from "react";

import { Panel } from "../components/ui";
import type { FuturesResponse, FuturesRoot } from "../futuresTypes";
import type { IndexOverview, Mover, Period, PerformanceResponse, Series } from "../indexTypes";
import type { PoliticalMetricsSeries, PoliticalTradesPage } from "../politicalTypes";
import type { AccountSummary, BrokerAccount, ItemsPage, Profile, SnapshotPage } from "../portfolioTypes";
import { useRemote, type Remote } from "../shared/api";
import { AreaChart, Gauge, heatFill, SignedBars, Sparkline, Treemap } from "../shared/charts";
import { formatCompact, formatDate, formatTime } from "../shared/format";
import { arrow, compactMoney, direction, price, signedPct, toNumber } from "../shared/market";
import type { StatusSummary } from "./MonitoringStatus";
import { useOverview } from "./OpsOverview";
import "./Cockpit.css";

const iso = (date: Date) => date.toISOString().slice(0, 10);
const daysAgo = (days: number) => iso(new Date(Date.now() - days * 86_400_000));

function PanelState({ remote, empty }: { remote: Remote<unknown>; empty?: string }) {
  if (remote.loading && !remote.data) return <p className="ck-state" role="status">Loading…</p>;
  if (remote.error && !remote.data) return <p className="ck-state ck-state--error" role="alert">Feed unavailable</p>;
  if (empty) return <p className="ck-state">{empty}</p>;
  return null;
}

/** A fractional change (0.0042) as an arrow and signed percentage. */
function Change({ value }: { value: number | null }) {
  const tone = direction(value);
  return <span className={`t-num t-num--${tone}`}>{arrow(value)} {signedPct(value)}</span>;
}

/* ── Indexes ─────────────────────────────────────────────────────────────── */

function IndexPanel() {
  const overview = useRemote<IndexOverview>("/hub/api/v1/us/indexes", 60_000);
  const codes = overview.data?.tracked.map((item) => item.code) ?? [];
  const params = new URLSearchParams({ date_from: daysAgo(92), date_to: iso(new Date()) });
  codes.forEach((code) => params.append("codes", code));
  const series = useRemote<{ items: Series[] }>(codes.length ? `/hub/api/v1/us/indexes/series?${params}` : null, 300_000);
  const byCode = new Map(series.data?.items.map((item) => [item.code, item.points.map((point) => point.value)]) ?? []);
  const tracked = overview.data?.tracked ?? [];
  return (
    <Panel code="INDX" title="Indexes" href="/market-data?view=indexes" className="ck-indexes">
      {!tracked.length ? <PanelState remote={overview} empty="No index levels yet" /> : (
        <table className="ck-table" aria-label="Tracked indexes">
          <thead><tr><th>Index</th><th className="numeric">Last</th><th className="numeric">1D</th><th>3M</th></tr></thead>
          <tbody>
            {tracked.map((item) => (
              <tr key={item.code} title={item.name}>
                <td><b>{item.code}</b><small>{item.name}</small></td>
                <td className="numeric">{price(item.value)}</td>
                <td className="numeric"><Change value={item.change} /></td>
                <td><Sparkline values={byCode.get(item.code) ?? []} label={`${item.code} three months`} width={84} height={20} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/* ── Sector heat grid and movers ─────────────────────────────────────────── */

const HEAT_PERIODS: { id: Period; label: string; scale: number }[] = [
  { id: "1d", label: "1D", scale: 0.02 }, { id: "1w", label: "1W", scale: 0.04 }, { id: "1m", label: "1M", scale: 0.08 },
  { id: "3m", label: "3M", scale: 0.15 }, { id: "ytd", label: "YTD", scale: 0.3 }, { id: "1y", label: "1Y", scale: 0.35 },
];

function SectorPanel({ performance }: { performance: Remote<PerformanceResponse> }) {
  const sectors = performance.data?.sectors ?? [];
  return (
    <Panel code="SECT" title="Sector heat" href="/market-data?view=indexes" className="ck-sectors"
      tools={performance.data?.as_of ? <span className="t-stamp">{formatDate(performance.data.as_of)}</span> : undefined}>
      {!sectors.length ? <PanelState remote={performance} empty="No sector levels yet" /> : (
        <table className="ck-heat" aria-label="Sector returns, cap weight">
          <thead><tr><th>GICS sector · cap wt</th>{HEAT_PERIODS.map((period) => <th key={period.id} className="numeric">{period.label}</th>)}</tr></thead>
          <tbody>
            {sectors.map((sector) => (
              <tr key={sector.sector_id}>
                <td>{sector.name}</td>
                {HEAT_PERIODS.map((period) => {
                  const value = sector.returns.cw[period.id];
                  return <td key={period.id} className="numeric ck-heat__cell" style={{ background: heatFill(value, period.scale) }}
                    title={`${sector.name} ${period.label} ${signedPct(value)}`}>{signedPct(value, 1)}</td>;
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th>S&amp;P 500</th>
              {HEAT_PERIODS.map((period) => <td key={period.id} className="numeric">{signedPct(performance.data?.benchmarks.$SPX[period.id] ?? null, 1)}</td>)}
            </tr>
          </tfoot>
        </table>
      )}
    </Panel>
  );
}

export function rankMovers(performance: PerformanceResponse | null): { top: Mover[]; bottom: Mover[] } {
  const seen = new Map<string, Mover>();
  for (const sector of performance?.sectors ?? []) {
    for (const mover of [...sector.movers.top, ...sector.movers.bottom]) {
      if (mover.symbol && mover.ret_1d !== null && !seen.has(mover.symbol)) seen.set(mover.symbol, mover);
    }
  }
  const ranked = [...seen.values()].sort((a, b) => (b.ret_1d ?? 0) - (a.ret_1d ?? 0));
  return { top: ranked.filter((mover) => (mover.ret_1d ?? 0) > 0).slice(0, 6), bottom: ranked.filter((mover) => (mover.ret_1d ?? 0) < 0).slice(-6).reverse() };
}

function MoversPanel({ performance }: { performance: Remote<PerformanceResponse> }) {
  const { top, bottom } = useMemo(() => rankMovers(performance.data), [performance.data]);
  const bars = (items: Mover[]) => items.map((mover) => ({
    key: mover.symbol ?? "", label: mover.symbol ?? "—", value: mover.ret_1d, display: signedPct(mover.ret_1d),
    title: `${mover.symbol}: ${signedPct(mover.ret_1d)} · ${(mover.contribution * 10000).toFixed(1)} bp to its sector`,
  }));
  return (
    <Panel code="MOVR" title="Movers · 1D" href="/market-data?view=indexes" className="ck-movers">
      {!top.length && !bottom.length ? <PanelState remote={performance} empty="No priced movers yet" /> : (
        <div className="ck-movers__cols">
          <SignedBars label="Top gainers" bars={bars(top)} />
          <SignedBars label="Top decliners" bars={bars(bottom)} />
        </div>
      )}
    </Panel>
  );
}

/* ── Futures board ───────────────────────────────────────────────────────── */

export function curveShape(root: FuturesRoot): "Contango" | "Backwardation" | "Flat" | null {
  const priced = root.contracts.filter((contract) => contract.last !== null);
  if (priced.length < 2) return null;
  const front = priced[0].last as number;
  const back = priced[priced.length - 1].last as number;
  const move = (back - front) / Math.abs(front || 1);
  return Math.abs(move) < 0.0005 ? "Flat" : move > 0 ? "Contango" : "Backwardation";
}

function FuturesPanel() {
  const futures = useRemote<FuturesResponse>("/hub/api/v1/us/futures", 60_000);
  const roots = (futures.data?.items ?? []).filter((root) => root.contracts.some((contract) => contract.last !== null));
  return (
    <Panel code="FUT" title="Futures" href="/market-data?view=futures" className="ck-futures"
      tools={futures.data ? <span className={`t-chip${futures.data.market_open ? " is-live" : ""}`}>{futures.data.market_open ? "Globex open" : "Closed"}</span> : undefined}>
      {!roots.length ? <PanelState remote={futures} empty="No futures recorded yet" /> : (
        <table className="ck-table" aria-label="Front-month futures">
          <thead><tr><th>Root</th><th className="numeric">Front</th><th className="numeric">Chg</th><th>Curve</th><th>Shape</th></tr></thead>
          <tbody>
            {roots.map((root) => {
              const front = root.contracts.find((contract) => contract.last !== null)!;
              const curve = root.contracts.map((contract) => contract.last).filter((value): value is number => value !== null);
              const shape = curveShape(root);
              return (
                <tr key={root.root} className={front.stale ? "is-stale" : ""}>
                  <td><a href={`/market-data?view=futures&group=${root.group}&root=${encodeURIComponent(root.root)}`}><b>{root.root}</b></a><small>{root.name}</small></td>
                  <td className="numeric">{price(front.last)}</td>
                  <td className="numeric"><Change value={front.change_pct} /></td>
                  <td><Sparkline values={curve} label={`${root.root} term structure by expiry`} width={72} height={18} tone="accent" /></td>
                  <td><small className={`ck-shape ck-shape--${(shape ?? "none").toLowerCase()}`}>{shape ?? "—"}</small></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/* ── Portfolio ───────────────────────────────────────────────────────────── */

const PORTFOLIO = "/hub/api/v1/portfolio";

function PortfolioPanel() {
  const profiles = useRemote<ItemsPage<Profile>>(`${PORTFOLIO}/profiles`, 300_000);
  const profile = profiles.data?.items.find((item) => item.active) ?? profiles.data?.items[0] ?? null;
  const accounts = useRemote<ItemsPage<BrokerAccount>>(profile ? `${PORTFOLIO}/accounts?user_id=${encodeURIComponent(profile.user_id)}` : null, 300_000);
  const account = accounts.data?.items.find((item) => !item.simulated) ?? accounts.data?.items[0] ?? null;
  const base = account && profile
    ? `${PORTFOLIO}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`
    : null;
  const filter = profile ? `user_id=${encodeURIComponent(profile.user_id)}` : "";
  const summary = useRemote<AccountSummary>(base && `${base}/summary?${filter}`, 60_000);
  const positions = useRemote<SnapshotPage>(base && `${base}/positions?${filter}`, 60_000);
  const equity = useRemote<{ currency: string | null; points: { t: string; value: string }[] }>(base && `${base}/equity?${filter}&range=3m`, 300_000);

  const currency = account?.base_currency ?? "";
  const metric = (name: string) => toNumber(summary.data?.balances.items.find((row) => row.metric === name && !row.segment && row.currency === currency)?.value_num);
  const netLiq = metric("NetLiquidation");
  const cash = metric("TotalCashValue");
  const rows = positions.data?.items ?? [];
  const unrealized = rows.reduce((sum, row) => sum + (toNumber(row.unrealized_pnl) ?? 0), 0);
  const gross = rows.reduce((sum, row) => sum + Math.abs(toNumber(row.market_value) ?? 0), 0);
  const points = (equity.data?.points ?? []).map((point) => ({ t: point.t, value: Number(point.value) })).filter((point) => Number.isFinite(point.value));
  const href = profile && account ? `/portfolio?${new URLSearchParams({ user_id: profile.user_id, account: JSON.stringify([account.broker_code, account.account_id]) })}` : "/portfolio";
  const tiles = rows.map((row) => {
    const value = toNumber(row.market_value) ?? 0;
    const pnl = toNumber(row.unrealized_pnl);
    const cost = value - (pnl ?? 0);
    const change = pnl !== null && cost ? pnl / Math.abs(cost) : null;
    const symbol = String(row.trading_symbol ?? "—");
    return {
      key: `${symbol}-${row.vendor_id ?? ""}`, label: symbol, size: Math.abs(value), change, scale: 0.15,
      sublabel: signedPct(change, 1), title: `${symbol}: ${compactMoney(value)} ${row.currency ?? ""} · unrealized ${compactMoney(pnl)} (${signedPct(change, 1)})`,
    };
  });

  return (
    <Panel code="PORT" title={account ? `Book · ${account.broker_code.toUpperCase()} ${account.account_id}` : "Book"} href={href} className="ck-portfolio"
      tools={account?.simulated ? <span className="t-chip t-chip--warn">Simulated</span> : undefined}>
      {!account ? <PanelState remote={profile ? accounts : profiles} empty={profiles.data ? "No broker accounts registered" : undefined} /> : <>
        <dl className="ck-kpis">
          <div><dt>Net liq {currency}</dt><dd>{compactMoney(netLiq)}</dd></div>
          <div><dt>Cash</dt><dd>{compactMoney(cash)}</dd></div>
          <div><dt>Gross exp</dt><dd>{rows.length ? compactMoney(gross) : "—"}</dd></div>
          <div><dt>Unrealized</dt><dd className={`t-num--${direction(unrealized)}`}>{rows.length ? `${arrow(unrealized)} ${compactMoney(unrealized)}` : "—"}</dd></div>
          <div><dt>Positions</dt><dd>{positions.data?.snapshot_time ? rows.length : "—"}</dd></div>
        </dl>
        <div className="ck-portfolio__charts">
          <div>
            <h3 className="ck-sub">Net liquidation · 3M</h3>
            {equity.error && !equity.data ? <p className="ck-state">Equity history unavailable</p>
              : <AreaChart points={points} label="Net liquidation, three months" height={170}
                format={(value) => compactMoney(value)} formatTime={(t) => formatDate(t)} />}
          </div>
          <div>
            <h3 className="ck-sub">Holdings by market value</h3>
            <Treemap items={tiles} label="Holdings sized by market value, coloured by unrealized return" height={170}
              empty={positions.data?.snapshot_time ? "No open positions" : "Positions not reported yet"} />
          </div>
        </div>
        {summary.data?.balances.snapshot_time && (
          <p className={`ck-foot${summary.data.balances.freshness === "stale" ? " is-stale" : ""}`}>
            Snapshot {formatTime(summary.data.balances.snapshot_time)}{summary.data.balances.freshness === "stale" ? " · stale" : ""}
          </p>
        )}
      </>}
    </Panel>
  );
}

/* ── Political flow ──────────────────────────────────────────────────────── */

function PoliticalPanel() {
  const today = iso(new Date());
  const metrics = new URLSearchParams({ metric: "trades", date_basis: "transaction", group_by: "month", date_from: daysAgo(365), date_to: today });
  const series = useRemote<PoliticalMetricsSeries>(`/hub/api/v1/political/metrics/timeseries?${metrics}`, 300_000);
  const trades = useRemote<PoliticalTradesPage>("/hub/api/v1/political/trades?limit=8", 300_000);
  const values = series.data?.points.map((point) => point.value) ?? [];
  return (
    <Panel code="POL" title="Congress trades" href="/political" className="ck-political"
      tools={values.length ? <Sparkline values={values} label="Reported trades per month, 12 months" width={110} height={18} tone="accent" /> : undefined}>
      {!trades.data?.items.length ? <PanelState remote={trades} empty="No disclosures yet" /> : (
        <table className="ck-table" aria-label="Latest congressional trades">
          <tbody>
            {trades.data.items.map((trade) => (
              <tr key={trade.trade_key}>
                <td className="ck-date">{formatDate(trade.transaction_date).replace(/ \d{4}$/, "")}</td>
                <td><b>{trade.ticker ?? "—"}</b></td>
                <td><span className={`ck-side ck-side--${trade.transaction_type.toLowerCase().startsWith("s") ? "sell" : "buy"}`}>{trade.transaction_type}</span></td>
                <td className="ck-ellipsis" title={trade.legislator_name}>{trade.legislator_name}</td>
                <td className="numeric">{trade.amount_str}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/* ── System health ───────────────────────────────────────────────────────── */

const VERDICT: Record<StatusSummary["verdict"], string> = { ok: "All systems working", attention: "Needs attention", critical: "Critical", unknown: "Unknown" };

function SystemPanel() {
  const status = useRemote<StatusSummary>("/hub/api/v1/monitoring/summary", 30_000);
  const { data: snapshot } = useOverview(60_000);
  const overview = snapshot?.summary && snapshot.india && snapshot.political ? snapshot : null;
  const problems = (status.data?.checks ?? []).filter((check) => check.severity === "critical" || check.severity === "warning").slice(0, 4);
  return (
    <Panel code="SYS" title="Systems" href="/monitoring" className="ck-system"
      tools={status.data ? <span className={`t-chip t-chip--${status.data.verdict}`}>{VERDICT[status.data.verdict]}</span> : undefined}>
      <div className="ck-system__row">
        <Gauge value={overview?.india.coverage_percent ?? null} target={95} label="India 1-min" size={84} />
        <dl className="ck-kpis ck-kpis--stack">
          <div><dt>Stored rows</dt><dd>{overview ? formatCompact(overview.summary.stored_rows) : "—"}</dd></div>
          <div><dt>Tables needing attention</dt><dd className={overview?.summary.attention_tables ? "t-num--warn" : ""}>{overview?.summary.attention_tables ?? "—"}</dd></div>
          <div><dt>Political fresh</dt><dd>{overview ? `${overview.political.fresh_datasets}/${overview.political.datasets_with_data}` : "—"}</dd></div>
        </dl>
      </div>
      {!status.data ? <PanelState remote={status} /> : problems.length ? (
        <ul className="ck-checks">
          {problems.map((check) => (
            <li key={check.id} className={`ck-checks__item ck-checks__item--${check.severity}`}>
              <i aria-hidden="true" />
              <a href={check.href}>{check.title}</a>
            </li>
          ))}
        </ul>
      ) : <p className="ck-state ck-state--ok">No open problems</p>}
    </Panel>
  );
}

/** The landing cockpit: every panel loads, fails and refreshes on its own. */
export function Cockpit() {
  const performance = useRemote<PerformanceResponse>("/hub/api/v1/us/sectors/performance?weighting=cw", 300_000);
  return (
    <main className="cockpit" aria-label="Cockpit">
      <h1 className="sr-only">FactorLab cockpit</h1>
      <div className="ck-grid">
        <IndexPanel />
        <SectorPanel performance={performance} />
        <FuturesPanel />
        <PortfolioPanel />
        <MoversPanel performance={performance} />
        <PoliticalPanel />
        <SystemPanel />
      </div>
    </main>
  );
}
