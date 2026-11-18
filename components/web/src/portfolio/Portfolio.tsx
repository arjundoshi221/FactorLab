import { Fragment, useEffect, useRef, useState } from "react";

import type { AccountSummary, Attributed, BrokerAccount, BrokerRow, Dimension, ExecutionPage, ItemsPage, PnlReport, Profile, SnapshotPage } from "./types";
import { realizedYtd, splitShares, ytdStart, type RealizedYtd } from "./pnlSplit";
import { Segmented } from "../components/ui";
import { ApiError, errorMessage, fetchJson, useRemote } from "../shared/api";
import { AreaChart, Treemap } from "../shared/charts";
import { formatDate, formatTime } from "../shared/format";
import { compactMoney, signedPct, toNumber } from "../shared/market";
import { OrderTagging } from "./PortfolioBook";
import { AccountTrades } from "./TradeHistory";
import { Amount, EmptyState, PivotControl, PortfolioHeader, PortfolioIcon, ProfileSelect, setPortfolioQuery, usePortfolioRefresh } from "./PortfolioUI";
import { exposureBy, type ExposureBucket } from "./exposurePivot";
import { attributeOf, pivotRows, useDimensions, usePivotParams } from "./pivot";
import "./Portfolio.css";

const API = "/hub/api/v1/portfolio";
type Column = [string, string, ("money" | "quantity" | "pnl" | "time")?];

/** `groups` puts rows under a header row per bucket of the dimension, in its display order. */
function Records({ title, rows, columns, empty, groups }: { title: string; rows: BrokerRow[]; columns: Column[]; empty: string; groups?: Dimension }) {
  const line = (row: BrokerRow, index: number | string) => <tr key={String(row.exec_id ?? row.vendor_id ?? index)}>{columns.map(([key, label, format]) => <td key={key} data-label={label} className={format && format !== "time" ? "portfolio-numeric" : ""}>
    {format === "time" ? formatTime(String(row[key] ?? ""), "—") : format ? <Amount value={row[key]} quantity={format === "quantity"} signed={format === "pnl"} /> : row[key] ?? "—"}
  </td>)}</tr>;
  const buckets = groups ? pivotRows(rows, row => attributeOf(row as Attributed, groups.id), groups.buckets) : null;
  return rows.length ? <div className="portfolio-table-scroll" tabIndex={0} role="region" aria-label={`${title} table`}><table aria-label={groups ? `${title} by ${groups.label.toLowerCase()}` : title}>
    <thead><tr>{columns.map(([key, label, format]) => <th scope="col" key={key} className={format && format !== "time" ? "portfolio-numeric" : ""}>{label}</th>)}</tr></thead>
    {buckets ? buckets.map(bucket => <tbody key={bucket.key} aria-label={bucket.value.label}>
      <tr className="portfolio-record-group"><th scope="rowgroup" colSpan={columns.length}>{bucket.value.label}<span>{bucket.rows.length} {bucket.rows.length === 1 ? "position" : "positions"}</span></th></tr>
      {bucket.rows.map((row, index) => line(row, `${bucket.key}#${index}`))}
    </tbody>) : <tbody>{rows.map(line)}</tbody>}
  </table></div> : <p className="portfolio-empty-inline">{empty}</p>;
}

/** The chosen dimension once every row carries its label (an unlabelled row is never shown as Unclassified). */
function labelledBy(dimension: Dimension | undefined, page: SnapshotPage | null): Dimension | undefined {
  if (!dimension || !page || page.unavailable_dimensions?.includes(dimension.id)) return undefined;
  return page.items.every(row => (row as Attributed).attributes?.[dimension.id]) ? dimension : undefined;
}

function SnapshotTime({ data }: { data: SnapshotPage }) {
  if (!data.snapshot_time) return <p className="portfolio-footnote">No successful snapshot reported yet.</p>;
  return <p className={`portfolio-footnote${data.freshness === "stale" ? " portfolio-stale" : ""}`}>
    <span className={`portfolio-dot${data.freshness === "stale" ? " is-stale" : ""}`} />
    {data.freshness === "stale" ? "Last snapshot · " : "Updated "}{formatTime(data.snapshot_time)}
    {data.freshness === "stale" && " · Stale snapshot"}
  </p>;
}

function AccountMetrics({ account, summary, positions, orders, realized }: {
  account: BrokerAccount; summary: AccountSummary | null; positions: SnapshotPage | null; orders: SnapshotPage | null;
  realized: RealizedState;
}) {
  const currency = account.base_currency;
  function metric(name: string) {
    // Only show an account-wide value in its stated base currency; never add currencies or segments.
    if (!currency) return null;
    const find = (tag: string, unit: string) => summary?.balances.items.find(row => row.metric === tag && !row.segment && row.currency === unit)?.value_num;
    // The Gateway reports account-wide PnL only as a ledger tag in "BASE" (already converted to the base
    // currency). The ledger row labelled with the base currency itself covers only that currency's positions.
    return find(name, currency) ?? (name === "UnrealizedPnL" ? find("$LEDGER-UnrealizedPnL", "BASE") : undefined);
  }
  // Prefer the broker's account-wide figure, already converted to the base currency. Otherwise sum
  // base-currency positions only, so currencies are never added together; with no such positions
  // the value is unknown (an em dash), not zero.
  const base = positions?.snapshot_time && currency ? positions.items.filter(row => row.currency === currency) : null;
  const baseRows = base?.length ? base : null;
  const brokerNumber = (name: string) => toNumber(metric(name) ?? null);
  const unrealized = brokerNumber("UnrealizedPnL") ?? baseRows?.reduce((total, row) => total + (toNumber(row.unrealized_pnl) ?? 0), 0) ?? null;
  const gross = brokerNumber("GrossPositionValue") ?? baseRows?.reduce((total, row) => total + Math.abs(toNumber(row.market_value) ?? 0), 0) ?? null;
  const fromBroker = (name: string) => brokerNumber(name) !== null;
  return <dl className="portfolio-overview" aria-label="Account overview">
    <div className="portfolio-overview__hero"><dt>Net liquidation <span>{currency}</span></dt><dd><Amount value={metric("NetLiquidation")} /></dd><small>Broker account value</small></div>
    <div><dt>Cash balance <span>{currency}</span></dt><dd><Amount value={metric("TotalCashValue")} /></dd><small>Total broker-reported cash</small></div>
    <div><dt>Gross exposure <span>{currency}</span></dt><dd>{gross === null ? "—" : compactMoney(gross)}</dd><small>{fromBroker("GrossPositionValue") ? "Broker-reported, all positions" : `Sum of |market value|, ${currency || "base"} positions`}</small></div>
    <div><dt>Open positions</dt><dd>{positions?.snapshot_time ? positions.items.length : "—"}</dd><small>In the latest snapshot</small></div>
    <div><dt>Working orders</dt><dd>{orders?.snapshot_time ? orders.items.length : "—"}</dd><small>In the latest snapshot</small></div>
    <PnlSplit currency={currency} unrealized={unrealized} unrealizedNote={fromBroker("UnrealizedPnL") ? "Broker-reported, all positions" : `Broker-reported, ${currency || "base"} positions`} realized={realized} />
  </dl>;
}

interface RealizedState { value: RealizedYtd | null; loading: boolean; failed: boolean; owned: boolean; href: string }

/** Account PnL split into the open mark (unrealized) and what closing fills locked in this year (realized). */
function PnlSplit({ currency, unrealized, unrealizedNote, realized }: { currency: string | null | undefined; unrealized: number | null; unrealizedNote: string; realized: RealizedState }) {
  const year = ytdStart().slice(0, 4);
  const value = realized.value;
  const net = toNumber(value?.net ?? null);
  const shares = splitShares(unrealized, net);
  const combined = unrealized === null || net === null ? null : unrealized + net;
  const tone = (number: number | null) => number === null ? "is-unknown" : number < 0 ? "is-negative" : "is-positive";
  const realizedNote = !realized.owned ? "Assign this account to a profile to record fills"
    : realized.failed && !value ? "Recorded PnL is unavailable right now"
    : realized.loading && !value ? "Calculating from recorded fills…"
    : !value ? "No recorded fills for this account yet"
    : value.excluded.length ? `Excludes ${value.excluded.length <= 3 ? value.excluded.join(", ") : `${value.excluded.length} instruments`} (cannot be valued)`
    : "Recorded fills, FIFO, net of fees";
  return <div className="portfolio-overview__pnl" aria-label="PnL split">
    <dt className="is-unrealized"><span className="portfolio-pnl-key is-unrealized" aria-hidden="true" />Unrealized <span>{currency}</span></dt>
    <dd className="is-unrealized"><Amount value={unrealized === null ? null : unrealized.toFixed(2)} signed /></dd>
    <small className="is-unrealized">{unrealizedNote}</small>
    <dt className="is-realized"><span className="portfolio-pnl-key is-realized" aria-hidden="true" />Realized · YTD {year} <span>{currency}</span></dt>
    <dd className="is-realized"><Amount value={value?.net ?? null} signed />{value?.partial && value.net !== null && <span className="portfolio-pnl-partial" title="Some instruments are left out">partial</span>}</dd>
    <small className="is-realized">{value?.realized != null && value.fees != null ? <><Amount value={value.realized} signed /> realized − <Amount value={value.fees} /> fees · </> : null}<span className={value?.excluded.length || (realized.failed && !value) ? "portfolio-stale" : ""}>{realizedNote}</span></small>
    <dt className="is-split">Open + realized YTD <span>{currency}</span></dt>
    <dd className="is-split"><Amount value={combined === null ? null : combined.toFixed(2)} signed /></dd>
    <dd className="is-split-bar">
      {shares ? <span className="portfolio-pnl-bar" role="img" aria-label={`Unrealized ${Math.round(shares.unrealized * 100)}%, realized ${Math.round(shares.realized * 100)}% of absolute PnL`}>
        <i className={`is-unrealized ${tone(unrealized)}`} style={{ width: `${shares.unrealized * 100}%` }} /><i className={`is-realized ${tone(net)}`} style={{ width: `${shares.realized * 100}%` }} />
      </span> : <span className="portfolio-pnl-bar is-empty" aria-hidden="true" />}
      <span className="portfolio-pnl-legend">{shares ? <><span>Unrealized {Math.round(shares.unrealized * 100)}%</span><span>Realized {Math.round(shares.realized * 100)}%</span></> : <span>Split unavailable until both sides are known</span>}
        {realized.href && <a className="portfolio-text-link" href={realized.href}>By theme<PortfolioIcon name="arrow" /></a>}</span>
    </dd>
  </div>;
}

/** The account's recorded realized PnL since 1 January, in its base currency. */
function useRealizedYtd(account: BrokerAccount, userId: string, revision: number): RealizedState {
  const owner = account.user_id ?? (userId && userId !== "unassigned" ? userId : "");
  const scope = owner ? new URLSearchParams({ user_id: owner, broker_code: account.broker_code, account_id: account.account_id, since: ytdStart() }) : null;
  const remote = useRemote<PnlReport>(scope ? `${API}/pnl/themes?${scope}` : null, 60_000);
  usePortfolioRefresh(revision, remote.refresh);
  // A payload whose items are not PnL groups is not a report: show it as unavailable, never guess.
  const report = remote.data && Array.isArray(remote.data.items) && remote.data.items.every(group => Array.isArray(group?.details) && Array.isArray(group?.issues)) ? remote.data : null;
  const href = owner ? `/portfolio/themes?${new URLSearchParams({ user_id: owner, account: JSON.stringify([account.broker_code, account.account_id]), period: "ytd", ...(account.base_currency ? { ccy: account.base_currency } : {}) })}` : "";
  return { value: realizedYtd(report, account.base_currency), loading: remote.loading, failed: Boolean(remote.error) || Boolean(remote.data && !report), owned: Boolean(owner), href };
}

type EquityRange = "1m" | "3m" | "1y" | "max";
interface EquityHistory { currency: string | null; points: { t: string; value: string }[]; basis: string }

function Performance({ base, filter, positions, balances, currency, dimension }: {
  base: string; filter: string; positions: SnapshotPage | null; balances: BrokerRow[]; currency: string; dimension: Dimension | undefined;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const failed = Boolean(dimension && positions?.unavailable_dimensions?.includes(dimension.id));
  const active = labelledBy(dimension, positions);
  const buckets = active && positions ? exposureBy(positions.items, balances, currency, active.id, active.buckets) : null;
  const [range, setRange] = useState<EquityRange>("3m");
  const equity = useRemote<EquityHistory>(`${base}/equity?${filter}&range=${range}`, 300_000);
  const points = (equity.data?.points ?? []).map(point => ({ t: point.t, value: Number(point.value) })).filter(point => Number.isFinite(point.value));
  const first = points[0]?.value, last = points[points.length - 1]?.value;
  const change = first && last !== undefined ? last / first - 1 : null;
  const tiles = (positions?.items ?? []).map((row, index) => {
    const value = toNumber(row.market_value) ?? 0;
    const pnl = toNumber(row.unrealized_pnl);
    const cost = value - (pnl ?? 0);
    const ret = pnl !== null && cost ? pnl / Math.abs(cost) : null;
    const symbol = String(row.trading_symbol ?? "—");
    return { key: `${symbol}-${index}`, label: symbol, size: Math.abs(value), change: ret, scale: 0.15, sublabel: signedPct(ret, 1),
      title: `${symbol}: ${compactMoney(value)} ${row.currency ?? ""} · unrealized ${compactMoney(pnl)} (${signedPct(ret, 1)})` };
  });
  const bucketTiles = (buckets ?? []).map(bucket => ({
    key: bucket.key, label: bucket.value.label, size: bucket.size, change: bucket.ret, scale: 0.15,
    sublabel: bucket.share === null ? signedPct(bucket.ret, 1) : `${(bucket.share * 100).toFixed(1)}%`,
    title: `${bucket.value.label}: ${bucket.holdings.length} ${bucket.holdings.length === 1 ? "position" : "positions"} · gross ${bucket.gross === null ? "unavailable" : compactMoney(bucket.gross)} ${currency} · unrealized ${compactMoney(bucket.unrealized)} (${signedPct(bucket.ret, 1)})${bucket.unconverted.length ? ` · ${bucket.unconverted.join(", ")} not converted` : ""}`,
    onSelect: () => setSelected(selected === bucket.key ? null : bucket.key), selected: selected === bucket.key,
  }));
  return <><section className="portfolio-performance-band" aria-label="Performance">
    <div className="portfolio-section portfolio-equity">
      <div className="portfolio-section-heading"><h3>Net liquidation{equity.data?.currency ? ` · ${equity.data.currency}` : ""}
        {change !== null && <span className={`portfolio-change portfolio-amount--${change >= 0 ? "positive" : "negative"}`}>{change >= 0 ? "▲" : "▼"} {signedPct(change)}</span>}</h3>
        <Segmented label="Equity range" value={range} onChange={setRange} options={[{ id: "1m", label: "1M" }, { id: "3m", label: "3M" }, { id: "1y", label: "1Y" }, { id: "max", label: "MAX" }]} />
      </div>
      {equity.error && !equity.data ? <p className="portfolio-empty-inline" role="alert">{errorMessage(equity.error, "Equity history is unavailable.")}</p>
        : equity.loading && !equity.data ? <p className="portfolio-empty-inline" role="status">Loading equity history…</p>
        : <AreaChart points={points} label="Net liquidation history" height={200} format={value => compactMoney(value)} formatTime={t => formatDate(t)} />}
      {equity.data && <p className="portfolio-footnote">{equity.data.basis}</p>}
    </div>
    <div className="portfolio-section portfolio-allocation">
      <div className="portfolio-section-heading"><h3>Allocation{active ? ` by ${active.label.toLowerCase()}` : ""}</h3></div>
      {failed && dimension && <p className="portfolio-notice portfolio-notice--error" role="alert">{dimension.label} classification is unavailable right now; showing holdings.</p>}
      {dimension && !active && !failed && positions && <p className="portfolio-empty-inline" role="status">Labelling holdings by {dimension.label.toLowerCase()}…</p>}
      <Treemap items={buckets ? bucketTiles : tiles} label={buckets && active ? `Holdings by ${active.label.toLowerCase()}` : "Holdings by market value"} height={200} empty={positions?.snapshot_time ? "No open positions" : "Positions not reported yet"} />
      <p className="portfolio-footnote">Size = |market value|{buckets ? `, ${currency || "base"}` : ""} · colour = unrealized return{buckets ? " · label = share of gross" : ""}</p>
    </div>
  </section>
  {buckets && active && buckets.length > 0 && <Exposure buckets={buckets} label={active.label} currency={currency} selected={selected} onSelect={setSelected} />}
  </>;
}

/** Gross, net and unrealized per pivot bucket in the base currency; a row expands to its positions. */
function Exposure({ buckets, label, currency, selected, onSelect }: {
  buckets: ExposureBucket[]; label: string; currency: string; selected: string | null; onSelect: (key: string | null) => void;
}) {
  const money = (value: number | null) => <Amount value={value === null ? null : value.toFixed(2)} />;
  const pnl = (value: number | null) => <Amount value={value === null ? null : value.toFixed(2)} signed />;
  return <section className="portfolio-section portfolio-exposure" aria-label={`Exposure by ${label.toLowerCase()}`}>
    <div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Allocation</span><h3>Exposure by {label.toLowerCase()}</h3></div><span className="portfolio-footnote">{currency || "Base currency"} · converted at the broker's rates</span></div>
    <div className="portfolio-table-scroll" role="region" aria-label={`Exposure by ${label.toLowerCase()} table`} tabIndex={0}><table aria-label={`Exposure by ${label.toLowerCase()}`}>
      <thead><tr><th scope="col">{label}</th><th scope="col" className="portfolio-numeric">Positions</th><th scope="col" className="portfolio-numeric">Gross</th><th scope="col" className="portfolio-numeric">Share of gross</th><th scope="col" className="portfolio-numeric">Net</th><th scope="col" className="portfolio-numeric">Unrealized</th></tr></thead>
      <tbody>{buckets.map(bucket => {
        const open = selected === bucket.key;
        return <Fragment key={bucket.key}><tr className={open ? "is-expanded" : ""}><th scope="row"><button type="button" className="portfolio-exposure-toggle" aria-expanded={open} onClick={() => onSelect(open ? null : bucket.key)}><PortfolioIcon name="chevron" />{bucket.value.label}</button>
          {bucket.unconverted.length > 0 && <small className="portfolio-cell-note">{bucket.unconverted.join(", ")}: no FX rate to {currency || "base"}</small>}</th>
          <td className="portfolio-numeric">{bucket.holdings.length}</td><td className="portfolio-numeric">{money(bucket.gross)}</td><td className="portfolio-numeric">{bucket.share === null ? "—" : `${(bucket.share * 100).toFixed(1)}%`}</td><td className="portfolio-numeric">{pnl(bucket.net)}</td><td className="portfolio-numeric">{pnl(bucket.unrealized)}</td></tr>
          {open && bucket.holdings.map((holding, index) => <tr key={index} className="portfolio-exposure-holding"><td>{holding.symbol}<small className="portfolio-cell-note">{String(holding.row.currency ?? "")}</small></td><td className="portfolio-numeric"><Amount value={holding.row.position} quantity /></td><td className="portfolio-numeric">{money(holding.value === null ? null : Math.abs(holding.value))}</td><td /><td className="portfolio-numeric">{pnl(holding.value)}</td><td className="portfolio-numeric">{pnl(holding.unrealized)}</td></tr>)}
        </Fragment>;
      })}</tbody></table></div>
  </section>;
}

type FlexState = "idle" | "running" | "success" | "partial" | "failed";
interface FlexJob { state: FlexState; run_id?: string | null; rows_written?: number | null; error?: string | null; finished_at?: string | null }
const FLEX_POLL_MS = 5_000;

/** Start the VPS-side IBKR Flex history sync (api -> ibkr-flex-runner) and poll it to completion. */
function FlexSync({ onSynced }: { onSynced: () => void }) {
  const [job, setJob] = useState<FlexJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const synced = useRef(onSynced);
  synced.current = onSynced;
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => {
      fetchJson<FlexJob>(`${API}/flex/runs/latest`).then(next => {
        setJob(next);
        if (next.state === "success" || next.state === "partial") synced.current();
      }, (reason: unknown) => { setJob(null); setError(errorMessage(reason, "Flex sync status is unavailable.")); });
    }, FLEX_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [job, running]);
  async function start() {
    setError(null);
    try {
      setJob(await fetchJson<FlexJob>(`${API}/flex/runs`, { method: "POST" }));
    } catch (reason) {
      // 409: a sync is already running, so follow it instead of failing.
      if (reason instanceof ApiError && reason.status === 409) setJob({ state: "running" });
      else setError(errorMessage(reason, "Could not start the IBKR history sync."));
    }
  }
  const status = error ?? (job?.state === "running" ? "Syncing IBKR history…"
    : job?.state === "success" ? `History synced · ${job.rows_written ?? 0} rows${job.finished_at ? ` · ${formatTime(job.finished_at)}` : ""}`
    : job?.state === "partial" || job?.state === "failed" ? `History sync ${job.state}${job.error ? `: ${job.error}` : ""}` : null);
  return <>
    <button className="portfolio-button portfolio-button--secondary" type="button" onClick={() => void start()} disabled={running} title="Pull executions, orders and cash from the IBKR Flex statement"><PortfolioIcon name="refresh" />{running ? "Syncing history…" : "Sync IBKR history"}</button>
    {status && <span className={`portfolio-footnote${error || job?.state === "failed" || job?.state === "partial" ? " portfolio-stale" : ""}`} role="status">{status}</span>}
  </>;
}

function AccountDetails({ account, userId, dimension }: { account: BrokerAccount; userId: string; dimension: Dimension | undefined }) {
  const [offset, setOffset] = useState(0);
  const [tradeRevision, setTradeRevision] = useState(0);
  const base = `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`;
  const filter = `user_id=${encodeURIComponent(userId)}`;
  const summary = useRemote<AccountSummary>(`${base}/summary?${filter}`, 60_000);
  const dims = dimension ? `&dims=${encodeURIComponent(dimension.id)}` : "";
  const positions = useRemote<SnapshotPage>(`${base}/positions?${filter}${dims}`, 60_000);
  const grouped = labelledBy(dimension, positions.data);
  const orders = useRemote<SnapshotPage>(`${base}/open-orders?${filter}`, 60_000);
  const realized = useRealizedYtd(account, userId, tradeRevision);
  const refreshAccount = () => { summary.refresh(); positions.refresh(); orders.refresh(); setOffset(0); setTradeRevision(value => value + 1); };
  return <section aria-label="Selected account" className="portfolio-details">
    <header className="portfolio-account-heading"><div className="portfolio-account-identity"><span className="portfolio-broker-mark" aria-hidden="true">{account.broker_code.slice(0, 2).toUpperCase()}</span><div>
      <h2>{account.account_id}</h2><p>{account.broker_code.toUpperCase()}<span>·</span>{account.account_mode === "paper" ? "Paper account" : account.account_mode}{account.base_currency && <><span>·</span>{account.base_currency}</>}</p>
    </div><strong className={`portfolio-badge${account.simulated ? " portfolio-badge--amber" : ""}`}>{account.simulated ? "Simulated" : account.account_mode}</strong></div>
      <div className="portfolio-account-update">{summary.data && <SnapshotTime data={summary.data.balances} />}<button className="portfolio-button portfolio-button--secondary" type="button" onClick={refreshAccount} disabled={summary.refreshing || positions.refreshing || orders.refreshing}><PortfolioIcon name="refresh" />Refresh account</button>{account.broker_code === "ibkr" && !account.simulated && <FlexSync onSynced={refreshAccount} />}<a className="portfolio-button portfolio-button--ghost" href={`/portfolio/trades?${new URLSearchParams({ user_id: userId, account: JSON.stringify([account.broker_code, account.account_id]) })}`}>View all trades</a></div>
    </header>
    {[summary, positions, orders].some(remote => remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Account refresh failed. Showing any last successful snapshots for this account.</p>}
    <AccountMetrics account={account} summary={summary.data} positions={positions.data} orders={orders.data} realized={realized} />
    <Performance base={base} filter={filter} positions={positions.data} balances={summary.data?.balances.items ?? []} currency={account.base_currency ?? ""} dimension={dimension} />
    <div className="portfolio-workspace">
      <OrderTagging account={account} revision={tradeRevision} />
      <section className="portfolio-section portfolio-positions"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Holdings</span><h3>Open positions</h3></div><span className="portfolio-count">{positions.data?.items.length ?? "—"}</span></div>
        {positions.loading && !positions.data ? <p className="portfolio-empty-inline" role="status">Loading positions…</p> : positions.data ? <>
          <Records title="Positions" rows={positions.data.items} groups={grouped} columns={[["trading_symbol", "Instrument"], ["position", "Qty", "quantity"], ["market_price", "Price", "money"], ["unrealized_pnl", "Unrealized", "pnl"], ["currency", "CCY"]]} empty={positions.data.snapshot_time ? "No open positions in this snapshot." : "Positions have not been reported yet."} />
          <SnapshotTime data={positions.data} />
          {positions.data.items.length > 0 && <p className="portfolio-footnote">Broker-reported positions and unrealized PnL.</p>}
        </> : <p className="portfolio-empty-inline">Positions unavailable.</p>}
      </section>
    </div>
    <AccountTrades account={account} revision={tradeRevision} dimension={dimension} />
    <details className="portfolio-section portfolio-disclosure"><summary><span>Account activity<small>Working orders, executions and balance details</small></span><PortfolioIcon name="chevron" /></summary>
      <div className="portfolio-activity">
        <section><div className="portfolio-section-heading"><h3>Open orders</h3>{orders.data && <SnapshotTime data={orders.data} />}</div>
          {orders.loading && !orders.data ? <p role="status">Loading orders…</p> : orders.data ? <Records title="Open orders" rows={orders.data.items} columns={[["trading_symbol", "Instrument"], ["side", "Side"], ["quantity", "Quantity", "quantity"], ["remaining_quantity", "Remaining", "quantity"], ["order_type", "Type"], ["limit_price", "Limit price", "money"], ["status", "Status"], ["currency", "CCY"]]} empty={orders.data.snapshot_time ? "No open orders in this snapshot." : "Open orders have not been reported yet."} /> : <p>Orders unavailable.</p>}
        </section>
        <Executions key={offset} base={base} filter={filter} offset={offset} setOffset={setOffset} revision={tradeRevision} />
        <section><div className="portfolio-section-heading"><h3>Balances</h3>{summary.data && <SnapshotTime data={summary.data.balances} />}</div>
          {summary.loading && !summary.data ? <p role="status">Loading balances…</p> : summary.data ? <Records title="Balances" rows={summary.data.balances.items} columns={[["metric", "Metric"], ["segment", "Segment"], ["value_num", "Value", "money"], ["value_str", "Text"], ["currency", "CCY"]]} empty="No balances in this snapshot." /> : <p>Balances unavailable.</p>}
        </section>
        {positions.data && <section><h3>Position details</h3><Records title="Position details" rows={positions.data.items} columns={[["trading_symbol", "Instrument"], ["position", "Quantity", "quantity"], ["avg_cost", "Average cost", "money"], ["market_value", "Market value", "money"], ["currency", "CCY"]]} empty="No open positions in this snapshot." /></section>}
      </div>
    </details>
  </section>;
}

function Executions({ base, filter, offset, setOffset, revision }: { base: string; filter: string; offset: number; setOffset: (value: number) => void; revision: number }) {
  const remote = useRemote<ExecutionPage>(`${base}/executions?${filter}&limit=50&offset=${offset}`, 60_000);
  usePortfolioRefresh(revision, remote.refresh);
  return <section><div className="portfolio-section-heading"><h3>Executed trades</h3><button className="portfolio-button portfolio-button--ghost" type="button" onClick={remote.refresh} disabled={remote.refreshing}><PortfolioIcon name="refresh" />Refresh trades</button></div>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Trades refresh failed. Showing any last successful results.</p>}
    {remote.loading && !remote.data ? <p role="status">Loading executed trades…</p> : remote.data ? <>
      <Records title="Executed trades" rows={remote.data.items} columns={[["exec_time", "Executed at (IST)", "time"], ["trading_symbol", "Instrument"], ["side", "Side"], ["quantity", "Quantity", "quantity"], ["price", "Price", "money"], ["commission", "Fees", "money"], ["realized_pnl", "Realized PnL", "pnl"], ["currency", "CCY"]]} empty="No executed trades reported." />
      {(offset > 0 || remote.data.has_more) && <div className="portfolio-pagination"><button className="portfolio-button portfolio-button--secondary" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous trades</button>
        <span>Page {Math.floor(offset / 50) + 1}</span><button className="portfolio-button portfolio-button--secondary" type="button" disabled={!remote.data.has_more} onClick={() => setOffset(offset + 50)}>Next trades</button></div>}
    </> : <p>Trades unavailable.</p>}
  </section>;
}

function Accounts({ userId, selected, onSelect }: { userId: string; selected: string; onSelect: (value: string) => void }) {
  const remote = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${encodeURIComponent(userId)}`, 60_000);
  // One pivot for the whole page (`pivot=sector`): allocation, exposure, open positions and the account's trades.
  const dimensions = useDimensions();
  const { pivot, setPivot } = usePivotParams();
  const dimension = dimensions.find(item => item.id === pivot);
  const accounts = remote.data?.items ?? [];
  const account = accounts.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected) ?? accounts[0];
  return <>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Account list is unavailable.")}</p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Loading accounts…</p> : <div className="portfolio-account-toolbar"><label className="portfolio-selector"><span>Broker account</span>
      <select value={account ? JSON.stringify([account.broker_code, account.account_id]) : ""} onChange={event => onSelect(event.target.value)} disabled={!accounts.length}>
        {!accounts.length && <option value="">No accounts</option>}
        {accounts.map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>
          {item.broker_code.toUpperCase()} · {item.account_id}{!userId ? ` · ${item.display_name}` : ""}
        </option>)}
      </select>
    </label><PivotControl dimensions={dimensions} value={pivot} onChange={setPivot} /><span className="portfolio-footnote">Snapshots refresh every minute</span></div>}
    {account && <AccountDetails key={JSON.stringify([userId, account.broker_code, account.account_id])} account={account} userId={userId} dimension={dimension} />}
    {!remote.loading && !Boolean(remote.error) && !account && <EmptyState title="No accounts registered for this profile.">Choose another profile to view its accounts.</EmptyState>}
  </>;
}

export function Portfolio() {
  const remote = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("user_id"));
  const [account, setAccount] = useState(() => new URLSearchParams(window.location.search).get("account") ?? "");
  const profiles = remote.data?.items ?? [];
  const selected = userId ?? profiles.find(profile => profile.active)?.user_id ?? profiles[0]?.user_id ?? "";
  return <main className="portfolio-page">
    <PortfolioHeader title="Overview" eyebrow="Portfolio" description="Net worth, holdings and working orders for each broker account.">
      <ProfileSelect profiles={profiles} value={selected} disabled={remote.loading && !remote.data} all onChange={value => { setUserId(value); setAccount(""); setPortfolioQuery(value); }} />
    </PortfolioHeader>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Profiles are unavailable.")} <button className="portfolio-button portfolio-button--ghost" onClick={remote.refresh}>Retry</button></p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Loading your portfolio…</p> : <Accounts key={selected} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} />}
    <p className="portfolio-workspace-note">Shared workspace · Profiles organize accounts and tags; everyone with workspace access can view and edit them.</p>
  </main>;
}
