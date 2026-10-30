import { useState } from "react";

import type { AccountSummary, BrokerAccount, BrokerRow, ExecutionPage, ItemsPage, Profile, SnapshotPage } from "../portfolioTypes";
import { Segmented } from "../components/ui";
import { errorMessage, useRemote } from "../shared/api";
import { AreaChart, Treemap } from "../shared/charts";
import { formatDate, formatTime } from "../shared/format";
import { compactMoney, signedPct, toNumber } from "../shared/market";
import { OrderTagging, PortfolioTabs } from "./PortfolioBook";
import { Amount, EmptyState, PortfolioHeader, PortfolioIcon, ProfileSelect, setPortfolioQuery, usePortfolioRefresh } from "./PortfolioUI";
import "./Portfolio.css";

const API = "/hub/api/v1/portfolio";
type Column = [string, string, ("money" | "quantity" | "pnl" | "time")?];

function Records({ title, rows, columns, empty }: { title: string; rows: BrokerRow[]; columns: Column[]; empty: string }) {
  return rows.length ? <div className="portfolio-table-scroll" tabIndex={0} role="region" aria-label={`${title} table`}><table aria-label={title}>
    <thead><tr>{columns.map(([key, label, format]) => <th scope="col" key={key} className={format && format !== "time" ? "portfolio-numeric" : ""}>{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={String(row.exec_id ?? row.vendor_id ?? index)}>{columns.map(([key, label, format]) => <td key={key} data-label={label} className={format && format !== "time" ? "portfolio-numeric" : ""}>
      {format === "time" ? formatTime(String(row[key] ?? ""), "—") : format ? <Amount value={row[key]} quantity={format === "quantity"} signed={format === "pnl"} /> : row[key] ?? "—"}
    </td>)}</tr>)}</tbody>
  </table></div> : <p className="portfolio-empty-inline">{empty}</p>;
}

function SnapshotTime({ data }: { data: SnapshotPage }) {
  if (!data.snapshot_time) return <p className="portfolio-footnote">No successful snapshot reported yet.</p>;
  return <p className={`portfolio-footnote${data.freshness === "stale" ? " portfolio-stale" : ""}`}>
    <span className={`portfolio-dot${data.freshness === "stale" ? " is-stale" : ""}`} />
    {data.freshness === "stale" ? "Last snapshot · " : "Updated "}{formatTime(data.snapshot_time)}
    {data.freshness === "stale" && " · Stale snapshot"}
  </p>;
}

function AccountMetrics({ account, summary, positions, orders }: {
  account: BrokerAccount; summary: AccountSummary | null; positions: SnapshotPage | null; orders: SnapshotPage | null;
}) {
  const currency = account.base_currency;
  function metric(name: string) {
    // Only show an account-wide value in its stated base currency; never add currencies or segments.
    return currency ? summary?.balances.items.find(row => row.metric === name && !row.segment && row.currency === currency)?.value_num : null;
  }
  // Position aggregates use base-currency rows only, so currencies are never added together.
  const base = positions?.snapshot_time && currency ? positions.items.filter(row => row.currency === currency) : null;
  const sum = (key: string) => base?.reduce((total, row) => total + (toNumber(row[key]) ?? 0), 0) ?? null;
  const gross = base?.reduce((total, row) => total + Math.abs(toNumber(row.market_value) ?? 0), 0) ?? null;
  const unrealized = sum("unrealized_pnl");
  return <dl className="portfolio-overview" aria-label="Account overview">
    <div><dt>Net liquidation <span>{currency}</span></dt><dd><Amount value={metric("NetLiquidation")} /></dd><small>Broker account value</small></div>
    <div><dt>Cash balance <span>{currency}</span></dt><dd><Amount value={metric("TotalCashValue")} /></dd><small>Total broker-reported cash</small></div>
    <div><dt>Gross exposure <span>{currency}</span></dt><dd>{gross === null ? "—" : compactMoney(gross)}</dd><small>Sum of |market value|, {currency || "base"} positions</small></div>
    <div><dt>Unrealized <span>{currency}</span></dt><dd><Amount value={unrealized === null ? null : unrealized.toFixed(2)} signed /></dd><small>Broker-reported, {currency || "base"} positions</small></div>
    <div><dt>Open positions</dt><dd>{positions?.snapshot_time ? positions.items.length : "—"}</dd><small>In the latest snapshot</small></div>
    <div><dt>Working orders</dt><dd>{orders?.snapshot_time ? orders.items.length : "—"}</dd><small>In the latest snapshot</small></div>
  </dl>;
}

type EquityRange = "1m" | "3m" | "1y" | "max";
interface EquityHistory { currency: string | null; points: { t: string; value: string }[]; basis: string }

function Performance({ base, filter, positions }: { base: string; filter: string; positions: SnapshotPage | null }) {
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
  return <section className="portfolio-performance-band" aria-label="Performance">
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
      <div className="portfolio-section-heading"><h3>Allocation</h3><span className="portfolio-footnote">Size = |market value| · colour = unrealized return</span></div>
      <Treemap items={tiles} label="Holdings by market value" height={200} empty={positions?.snapshot_time ? "No open positions" : "Positions not reported yet"} />
    </div>
  </section>;
}

function AccountDetails({ account, userId }: { account: BrokerAccount; userId: string }) {
  const [offset, setOffset] = useState(0);
  const [tradeRevision, setTradeRevision] = useState(0);
  const base = `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`;
  const filter = `user_id=${encodeURIComponent(userId)}`;
  const summary = useRemote<AccountSummary>(`${base}/summary?${filter}`, 60_000);
  const positions = useRemote<SnapshotPage>(`${base}/positions?${filter}`, 60_000);
  const orders = useRemote<SnapshotPage>(`${base}/open-orders?${filter}`, 60_000);
  return <section aria-label="Selected account" className="portfolio-details">
    <header className="portfolio-account-heading"><div className="portfolio-account-identity"><span className="portfolio-broker-mark" aria-hidden="true">{account.broker_code.slice(0, 2).toUpperCase()}</span><div>
      <h2>{account.account_id}</h2><p>{account.broker_code.toUpperCase()}<span>·</span>{account.account_mode === "paper" ? "Paper account" : account.account_mode}{account.base_currency && <><span>·</span>{account.base_currency}</>}</p>
    </div><strong className={`portfolio-badge${account.simulated ? " portfolio-badge--amber" : ""}`}>{account.simulated ? "Simulated" : account.account_mode}</strong></div>
      <div className="portfolio-account-update">{summary.data && <SnapshotTime data={summary.data.balances} />}<button className="portfolio-button portfolio-button--secondary" type="button" onClick={() => { summary.refresh(); positions.refresh(); orders.refresh(); setOffset(0); setTradeRevision(value => value + 1); }} disabled={summary.refreshing || positions.refreshing || orders.refreshing}><PortfolioIcon name="refresh" />Refresh account</button><a className="portfolio-button portfolio-button--ghost" href={`/portfolio/trades?${new URLSearchParams({ user_id: userId, account: JSON.stringify([account.broker_code, account.account_id]) })}`}>View all trades</a></div>
    </header>
    {[summary, positions, orders].some(remote => remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Account refresh failed. Showing any last successful snapshots for this account.</p>}
    <AccountMetrics account={account} summary={summary.data} positions={positions.data} orders={orders.data} />
    <Performance base={base} filter={filter} positions={positions.data} />
    <div className="portfolio-workspace">
      <OrderTagging account={account} revision={tradeRevision} />
      <section className="portfolio-section portfolio-positions"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Holdings</span><h3>Open positions</h3></div><span className="portfolio-count">{positions.data?.items.length ?? "—"}</span></div>
        {positions.loading && !positions.data ? <p className="portfolio-empty-inline" role="status">Loading positions…</p> : positions.data ? <>
          <Records title="Positions" rows={positions.data.items} columns={[["trading_symbol", "Instrument"], ["position", "Qty", "quantity"], ["market_price", "Price", "money"], ["unrealized_pnl", "Unrealized", "pnl"], ["currency", "CCY"]]} empty={positions.data.snapshot_time ? "No open positions in this snapshot." : "Positions have not been reported yet."} />
          <SnapshotTime data={positions.data} />
          {positions.data.items.length > 0 && <p className="portfolio-footnote">Broker-reported positions and unrealized PnL.</p>}
        </> : <p className="portfolio-empty-inline">Positions unavailable.</p>}
      </section>
    </div>
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
    </label><span className="portfolio-footnote">Snapshots refresh every minute</span></div>}
    {account && <AccountDetails key={JSON.stringify([userId, account.broker_code, account.account_id])} account={account} userId={userId} />}
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
    <PortfolioHeader title="Portfolio" description="Your accounts, orders and investment ideas. All in one place.">
      <ProfileSelect profiles={profiles} value={selected} disabled={remote.loading && !remote.data} all onChange={value => { setUserId(value); setAccount(""); setPortfolioQuery(value); }} />
    </PortfolioHeader>
    <PortfolioTabs active="accounts" userId={selected} account={account} />
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Profiles are unavailable.")} <button className="portfolio-button portfolio-button--ghost" onClick={remote.refresh}>Retry</button></p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Loading your portfolio…</p> : <Accounts key={selected} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} />}
    <p className="portfolio-workspace-note">Shared workspace · Profiles organize accounts and tags; everyone with workspace access can view and edit them.</p>
  </main>;
}
