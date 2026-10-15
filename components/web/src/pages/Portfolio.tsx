import { useState } from "react";

import type { AccountSummary, BrokerAccount, BrokerRow, ExecutionPage, ItemsPage, Profile, SnapshotPage } from "../portfolioTypes";
import { errorMessage, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
import "./Portfolio.css";
import { OrderTagging, PortfolioTabs } from "./PortfolioBook";

const API = "/hub/api/v1/portfolio";
type Column = [string, string];

function Records({ title, rows, columns, empty }: { title: string; rows: BrokerRow[]; columns: Column[]; empty: string }) {
  return rows.length ? <div className="table-scroll"><table aria-label={title}>
    <thead><tr>{columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{columns.map(([key, label]) => <td key={key} data-label={label}>{row[key] ?? "—"}</td>)}</tr>)}</tbody>
  </table></div> : <p className="no-results">{empty}</p>;
}

function SnapshotTime({ data }: { data: SnapshotPage }) {
  if (!data.snapshot_time) return <p>No successful snapshot reported yet.</p>;
  return <p className={data.freshness === "stale" ? "portfolio-stale" : "muted"}>
    Snapshot: {formatTime(data.snapshot_time)} · {Math.floor((data.age_seconds ?? 0) / 60)} minutes ago
    {data.freshness === "stale" && " · Stale: showing the last successful snapshot"}
  </p>;
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
    <header className="portfolio-heading"><div><h2>{account.account_id}</h2><p>{account.display_name} · {account.broker_code.toUpperCase()} · {account.account_mode}
      {account.base_currency && ` · ${account.base_currency}`} {account.simulated && <strong className="portfolio-simulated">Simulated</strong>}</p></div>
      <button type="button" onClick={() => { summary.refresh(); positions.refresh(); orders.refresh(); setOffset(0); setTradeRevision(value => value + 1); }} disabled={summary.refreshing || positions.refreshing || orders.refreshing}>Refresh account</button>
    </header>
    {[summary, positions, orders].some(remote => remote.error) && <p role="alert">Account refresh failed. Showing any last successful snapshots for this account.</p>}
    <section className="portfolio-section"><h3>Balances</h3>
      {summary.loading && !summary.data ? <p>Loading balances…</p> : summary.data ? <>
        <SnapshotTime data={summary.data.balances} />
        <Records title="Balances" rows={summary.data.balances.items} columns={[["metric", "Metric"], ["segment", "Segment"], ["value_num", "Value"], ["value_str", "Text"], ["currency", "Currency"]]} empty="No balances in this snapshot." />
      </> : <p>Balances unavailable.</p>}
    </section>
    <section className="portfolio-section"><h3>Positions</h3>
      {positions.loading && !positions.data ? <p>Loading positions…</p> : positions.data ? <>
        <SnapshotTime data={positions.data} />
        <Records title="Positions" rows={positions.data.items} columns={[["trading_symbol", "Instrument"], ["position", "Quantity"], ["avg_cost", "Average cost"], ["market_price", "Price"], ["market_value", "Value"], ["unrealized_pnl", "Unrealized P&L"], ["currency", "Currency"]]} empty={positions.data.snapshot_time ? "No open positions in this snapshot." : "Positions have not been reported yet."} />
      </> : <p>Positions unavailable.</p>}
    </section>
    <section className="portfolio-section"><h3>Open orders</h3>
      {orders.loading && !orders.data ? <p>Loading orders…</p> : orders.data ? <>
        <SnapshotTime data={orders.data} />
        <Records title="Open orders" rows={orders.data.items} columns={[["trading_symbol", "Instrument"], ["side", "Side"], ["quantity", "Quantity"], ["remaining_quantity", "Remaining"], ["order_type", "Type"], ["limit_price", "Limit price"], ["status", "Status"], ["currency", "Currency"]]} empty={orders.data.snapshot_time ? "No open orders in this snapshot." : "Open orders have not been reported yet."} />
      </> : <p>Orders unavailable.</p>}
    </section>
    <Executions key={`${offset}/${tradeRevision}`} base={base} filter={filter} offset={offset} setOffset={setOffset} />
    <OrderTagging key={JSON.stringify([account.broker_code, account.account_id, tradeRevision])} account={account} />
  </section>;
}

function Executions({ base, filter, offset, setOffset }: { base: string; filter: string; offset: number; setOffset: (value: number) => void }) {
  const remote = useRemote<ExecutionPage>(`${base}/executions?${filter}&limit=50&offset=${offset}`, 60_000);
  return <section className="portfolio-section"><div className="portfolio-heading"><h3>Executed trades</h3><button type="button" onClick={remote.refresh}>Refresh trades</button></div>
    {Boolean(remote.error) && <p role="alert">Trades refresh failed. Showing any last successful results.</p>}
    {remote.loading && !remote.data ? <p>Loading executed trades…</p> : remote.data ? <>
      <Records title="Executed trades" rows={remote.data.items} columns={[["exec_time", "Executed at (UTC)"], ["trading_symbol", "Instrument"], ["side", "Side"], ["quantity", "Quantity"], ["price", "Price"], ["commission", "Commission"], ["realized_pnl", "Realized P&L"], ["currency", "Currency"]]} empty="No executed trades reported." />
      <div className="portfolio-pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous trades</button>
        <span>Page {Math.floor(offset / 50) + 1}</span><button type="button" disabled={!remote.data.has_more} onClick={() => setOffset(offset + 50)}>Next trades</button></div>
    </> : <p>Trades unavailable.</p>}
  </section>;
}

function Accounts({ userId }: { userId: string }) {
  const remote = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${encodeURIComponent(userId)}`, 60_000);
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.search).get("account") ?? "");
  const accounts = remote.data?.items ?? [];
  const account = accounts.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected) ?? accounts[0];
  return <>
    {Boolean(remote.error) && <p role="alert">{errorMessage(remote.error, "Account list is unavailable.")}</p>}
    {remote.loading && !remote.data ? <p>Loading accounts…</p> : <label className="portfolio-selector">Broker account
      <select value={account ? JSON.stringify([account.broker_code, account.account_id]) : ""} onChange={event => setSelected(event.target.value)} disabled={!accounts.length}>
        {!accounts.length && <option value="">No accounts</option>}
        {accounts.map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>
          {item.display_name} · {item.broker_code} / {item.account_id}{item.simulated ? " · Simulated" : ""}
        </option>)}
      </select>
    </label>}
    {account && <AccountDetails key={JSON.stringify([userId, account.broker_code, account.account_id])} account={account} userId={userId} />}
    {!remote.loading && !Boolean(remote.error) && !account && <p>No accounts registered for this profile.</p>}
  </>;
}

export function Portfolio() {
  const remote = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState(() => new URLSearchParams(window.location.search).get("user_id") ?? "");
  return <main className="portfolio-page">
    <section className="hero"><div><span className="eyebrow">Shared testing workspace</span><h1>Portfolios</h1>
      <p>Choose a test profile and broker account. Everyone in this workspace can view every profile. Market data is shared.</p></div></section>
    <PortfolioTabs active="accounts" userId={userId} />
    {Boolean(remote.error) && <p role="alert">{errorMessage(remote.error, "Profiles are unavailable.")}</p>}
    <label className="portfolio-selector">Test profile<select value={userId} onChange={event => setUserId(event.target.value)}>
      <option value="">All profiles</option><option value="unassigned">Unassigned</option>
      {(remote.data?.items ?? []).map(profile => <option key={profile.user_id} value={profile.user_id}>{profile.display_name}{!profile.active ? " (inactive)" : ""}</option>)}
    </select></label>
    <Accounts key={userId} userId={userId} />
  </main>;
}
