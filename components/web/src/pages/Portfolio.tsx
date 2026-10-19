import { useState } from "react";

import type { AccountSummary, BrokerAccount, BrokerRow, ExecutionPage, ItemsPage, Profile, SnapshotPage } from "../portfolioTypes";
import { errorMessage, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
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
  return <dl className="portfolio-overview" aria-label="Account overview">
    <div><dt>Net liquidation <span>{currency}</span></dt><dd><Amount value={metric("NetLiquidation")} /></dd><small>Broker account value</small></div>
    <div><dt>Cash balance <span>{currency}</span></dt><dd><Amount value={metric("TotalCashValue")} /></dd><small>Total broker-reported cash</small></div>
    <div><dt>Open positions</dt><dd>{positions?.snapshot_time ? positions.items.length : "—"}</dd><small>In the latest snapshot</small></div>
    <div><dt>Working orders</dt><dd>{orders?.snapshot_time ? orders.items.length : "—"}</dd><small>In the latest snapshot</small></div>
  </dl>;
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
