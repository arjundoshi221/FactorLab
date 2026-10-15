import { useState, type FormEvent } from "react";

import type { BrokerAccount, CatalogEntry, CatalogKind, ItemsPage, OrderAssignment, OrdersPage, PnlGroup, Profile, TaggedOrder } from "../portfolioTypes";
import { errorMessage, fetchJson, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
import "./Portfolio.css";

const API = "/hub/api/v1/portfolio";
const kinds: CatalogKind[] = ["themes", "strategies", "trade-names"];
const labels = { themes: "Themes", strategies: "Strategies", "trade-names": "Trade names" };
const fields = { themes: "theme_id", strategies: "strategy_id", "trade-names": "trade_name_id" } as const;

export function PortfolioTabs({ active, userId = "" }: { active: "accounts" | CatalogKind; userId?: string }) {
  const query = userId && userId !== "unassigned" ? `?user_id=${encodeURIComponent(userId)}` : "";
  return <nav className="portfolio-tabs" aria-label="Portfolio screens">
    <a href={`/portfolio${query}`} aria-current={active === "accounts" ? "page" : undefined}>Accounts & orders</a>
    {kinds.map(kind => <a key={kind} href={`/portfolio/${kind}${query}`} aria-current={active === kind ? "page" : undefined}>{labels[kind]}</a>)}
  </nav>;
}

function OrderEditor({ order, userId, base, catalogs, refresh }: {
  order: TaggedOrder; userId: string; base: string; catalogs: Record<CatalogKind, CatalogEntry[]>; refresh: () => void;
}) {
  const [tags, setTags] = useState<OrderAssignment>({ theme_id: order.theme_id, strategy_id: order.strategy_id, trade_name_id: order.trade_name_id });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function save(clear = false) {
    setBusy(true); setError(""); setSaved(false);
    const value = clear ? { theme_id: null, strategy_id: null, trade_name_id: null } : tags;
    try {
      await fetchJson(`${base}/orders/${encodeURIComponent(order.perm_id)}/assignment?user_id=${encodeURIComponent(userId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
      });
      setTags(value); setSaved(true); refresh();
    } catch (caught) { setError(errorMessage(caught, "Could not save order tags.")); }
    finally { setBusy(false); }
  }
  const complete = Object.values(tags).every(Boolean);
  return <article className="portfolio-order">
    <h4>{order.trading_symbol} · {order.side} · Order {order.perm_id}</h4>
    <p>{order.status} · {order.filled_quantity} filled · {order.currency}</p>
    {order.taggable ? <>
      <div className="portfolio-tag-selectors">{kinds.map(kind => <label key={kind}>{labels[kind]}
        <select aria-label={`${labels[kind]} for order ${order.perm_id}`} value={tags[fields[kind]] ?? ""} disabled={busy}
          onChange={event => { setTags({ ...tags, [fields[kind]]: event.target.value || null }); setSaved(false); }}>
          <option value="">Select {labels[kind].toLowerCase()}</option>
          {catalogs[kind].filter(item => item.active || item.id === tags[fields[kind]]).map(item =>
            <option key={item.id} value={item.id} disabled={!item.active}>{item.name}{!item.active ? " (archived)" : ""}</option>)}
        </select>
      </label>)}</div>
      <div className="portfolio-actions"><button disabled={busy || !complete} onClick={() => void save()}>{busy ? "Saving…" : "Save tags"}</button>
        <button disabled={busy || !Object.values(tags).some(Boolean)} onClick={() => void save(true)}>Clear tags</button>
        {saved && <span role="status">Tags saved. PnL recalculates with this assignment.</span>}</div>
    </> : <p>A registered profile and durable broker order ID are required for tagging.</p>}
    {error && <p role="alert">{error}</p>}
    {order.fills.length > 0 && <details><summary>{order.fills.length} fills · inherit these order tags</summary>
      <div className="table-scroll"><table aria-label={`Fills for order ${order.perm_id}`}><thead><tr><th>Execution ID</th><th>Executed at</th><th>Quantity</th><th>Price</th><th>Commission</th></tr></thead>
        <tbody>{order.fills.map(fill => <tr key={String(fill.exec_id)}><td>{fill.exec_id}</td><td>{String(fill.exec_time)}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.commission ?? "Unavailable"} {fill.commission_ccy}</td></tr>)}</tbody></table></div>
    </details>}
  </article>;
}

export function OrderTagging({ account }: { account: BrokerAccount }) {
  const userId = account.user_id;
  const [offset, setOffset] = useState(0);
  const base = `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`;
  const suffix = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  const themes = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/themes${suffix}` : null, 60_000);
  const strategies = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/strategies${suffix}` : null, 60_000);
  const names = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/trade-names${suffix}` : null, 60_000);
  const orders = useRemote<OrdersPage>(userId ? `${base}/orders${suffix}&offset=${offset}&limit=50` : null, 60_000);
  const catalogs = { themes: themes.data?.items ?? [], strategies: strategies.data?.items ?? [], "trade-names": names.data?.items ?? [] };
  const pending = [themes, strategies, names, orders];
  return <section className="portfolio-section"><div className="portfolio-heading"><h3>Order tagging</h3>
    <button onClick={() => pending.forEach(remote => remote.refresh())}>Refresh orders and tags</button></div>
    <p>Assign one theme, strategy and trade name independently. All current and future fills inherit the order’s tags. Closing orders match FIFO within the same three tags.</p>
    {!userId ? <p>Register this account to a profile before tagging orders.</p> : <>
      {pending.some(remote => remote.error) && <p role="alert">Order tagging is unavailable or stale. Check the portfolio migration and refresh.</p>}
      {pending.some(remote => remote.loading) ? <p>Loading order tags…</p> : <>
        {kinds.some(kind => !catalogs[kind].some(item => item.active)) && <p>Create active entries in <a href={`/portfolio/themes${suffix}`}>Themes</a>, <a href={`/portfolio/strategies${suffix}`}>Strategies</a> and <a href={`/portfolio/trade-names${suffix}`}>Trade names</a> to tag orders.</p>}
        {(orders.data?.items ?? []).map(order => <OrderEditor key={`${order.key}/${order.theme_id}/${order.strategy_id}/${order.trade_name_id}`} order={order} userId={userId} base={base} catalogs={catalogs} refresh={orders.refresh} />)}
        {orders.data?.items.length === 0 && <p>No working or executed orders recorded.</p>}
        <div className="portfolio-pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous orders</button>
          <span>Page {Math.floor(offset / 50) + 1}</span><button disabled={!orders.data?.has_more} onClick={() => setOffset(offset + 50)}>Next orders</button></div>
      </>}
    </>}
  </section>;
}

function CatalogManager({ kind, userId }: { kind: CatalogKind; userId: string }) {
  const remote = useRemote<ItemsPage<CatalogEntry>>(`${API}/catalogs/${kind}?user_id=${userId}`, 60_000);
  const [editing, setEditing] = useState<CatalogEntry | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function reset() { setEditing(null); setName(""); setDescription(""); }
  async function write(entry: CatalogEntry | null, active: boolean, values = { name, description }) {
    setBusy(true); setError("");
    try {
      await fetchJson(`${API}/catalogs/${kind}${entry ? `/${entry.id}` : ""}?user_id=${userId}`, {
        method: entry ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, active }),
      });
      reset(); remote.refresh();
    } catch (caught) { setError(errorMessage(caught, "Could not save catalog entry.")); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void write(editing, editing?.active ?? true); }
  return <section className="portfolio-section"><h2>Manage {labels[kind].toLowerCase()}</h2>
    <form className="portfolio-catalog-form" onSubmit={submit}>
      <label>Name<input required maxLength={200} value={name} onChange={event => setName(event.target.value)} /></label>
      <label>Description<textarea maxLength={10000} value={description} onChange={event => setDescription(event.target.value)} /></label>
      <div className="portfolio-actions"><button disabled={busy || !name.trim()}>{editing ? "Save changes" : `Create ${kind === "trade-names" ? "trade name" : kind.slice(0, -1)}`}</button>
        {editing && <button type="button" onClick={reset} disabled={busy}>Cancel edit</button>}</div>
    </form>
    {Boolean(error || remote.error) && <p role="alert">{error || errorMessage(remote.error)}</p>}
    {remote.loading ? <p>Loading catalog…</p> : <ul className="portfolio-catalog">{(remote.data?.items ?? []).map(entry => <li key={entry.id}>
      <div><strong>{entry.name}</strong>{!entry.active && " · Archived"}<p>{entry.description}</p></div>
      <div className="portfolio-actions"><button disabled={busy} onClick={() => { setEditing(entry); setName(entry.name); setDescription(entry.description); }}>Edit</button>
        <button disabled={busy} onClick={() => void write(entry, !entry.active, { name: entry.name, description: entry.description })}>{entry.active ? "Archive" : "Restore"}</button></div>
    </li>)}</ul>}
  </section>;
}

const issueLabels: Record<string, string> = {
  incomplete_history: "Opening history does not reconcile with the broker snapshot",
  missing_price: "Price unavailable", stale_price: "Stale price", unassigned: "Unassigned orders",
  missing_commission: "Commission not yet reported", missing_commission_currency: "Commission currency unavailable",
  unsupported_instrument: "PnL unsupported for this instrument", invalid_fill: "Invalid fill data",
  missing_instrument: "Instrument identity unavailable", history_unverified: "No snapshot available to check recorded history",
  missing_currency: "Trade currency unavailable",
};

function Amount({ value }: { value: string | null }) { return <>{value === null ? "Unavailable" : value}</>; }

function PnlReport({ kind, userId }: { kind: CatalogKind; userId: string }) {
  const accounts = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${userId}`, 60_000);
  const [selected, setSelected] = useState("");
  const account = accounts.data?.items.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected);
  const filter = account ? `&broker_code=${encodeURIComponent(account.broker_code)}&account_id=${encodeURIComponent(account.account_id)}` : "";
  return <section className="portfolio-section"><h2>PnL by {labels[kind].toLowerCase()}</h2>
    <p>Current cumulative PnL from available recorded fills. Realized and unrealized exclude commissions; total subtracts recorded commissions once. Currencies, paper/live accounts and simulations are shown separately. Dividends, financing and FX conversion are excluded.</p>
    <label className="portfolio-selector">PnL account<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">All profile accounts</option>
      {(accounts.data?.items ?? []).map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>{item.broker_code} / {item.account_id} · {item.account_mode}{item.simulated ? " · Simulated" : ""}</option>)}
    </select></label>
    {Boolean(accounts.error) && <p role="alert">{errorMessage(accounts.error)}</p>}
    <PnlValues key={filter} kind={kind} userId={userId} filter={filter} />
  </section>;
}

function PnlValues({ kind, userId, filter }: { kind: CatalogKind; userId: string; filter: string }) {
  const remote = useRemote<ItemsPage<PnlGroup>>(`${API}/pnl/${kind}?user_id=${userId}${filter}`, 60_000);
  return <div><div className="portfolio-actions"><button onClick={remote.refresh}>Refresh PnL</button></div>
    {Boolean(remote.error) && <p role="alert">{errorMessage(remote.error, "PnL refresh failed.")} Any visible report is from the last successful refresh.</p>}
    {remote.loading ? <p>Calculating PnL…</p> : remote.data?.items.length === 0 ? <p>No recorded fills or holdings for this selection.</p> : <div className="portfolio-pnl-groups">{(remote.data?.items ?? []).map(group =>
      <article key={JSON.stringify([group.id, group.account_mode, group.simulated, group.currency])} className="portfolio-order"><h3>{group.name} · {group.currency}</h3><p>{group.account_mode}{group.simulated && " · Simulated"}</p>
        <dl className="portfolio-metrics">{(["realized", "unrealized", "commissions", "total"] as const).map(field => <div key={field}><dt>{field === "total" ? "Total PnL" : field[0].toUpperCase() + field.slice(1)}</dt><dd><Amount value={group[field]} /></dd></div>)}</dl>
        {group.issues.length > 0 && <p className="portfolio-stale">{group.issues.map(issue => issueLabels[issue] ?? issue).join(" · ")}</p>}
        <details><summary>Holdings and contributing orders</summary><div className="table-scroll"><table aria-label={`${group.name} holdings`}><thead><tr><th>Account</th><th>Instrument</th><th>Quantity</th><th>Realized</th><th>Unrealized</th><th>Costs</th><th>Total</th><th>Valuation / history</th><th>Orders</th></tr></thead>
          <tbody>{group.details.map((detail, index) => <tr key={index}><td>{detail.broker_code} / {detail.account_id}</td><td>{detail.trading_symbol}</td><td>{detail.quantity}</td><td><Amount value={detail.realized} /></td><td><Amount value={detail.unrealized} /></td><td><Amount value={detail.commissions} /></td><td><Amount value={detail.total} /></td>
            <td>{detail.price_time ? formatTime(detail.price_time) : "No open-position valuation"}<br />Recorded since {formatTime(detail.history_start)}</td>
            <td><a href={`/portfolio?user_id=${userId}&account=${encodeURIComponent(JSON.stringify([detail.broker_code, detail.account_id]))}`}>{detail.orders.join(", ") || "No recorded orders"}</a></td></tr>)}</tbody></table></div></details>
      </article>)}</div>}
  </div>;
}

export function PortfolioGroups({ kind }: { kind: CatalogKind }) {
  const profiles = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState(() => new URLSearchParams(window.location.search).get("user_id") ?? "");
  const selected = profiles.data?.items.find(profile => profile.user_id === userId)?.user_id ?? profiles.data?.items[0]?.user_id ?? "";
  return <main className="portfolio-page"><section className="hero"><div><span className="eyebrow">Portfolio</span><h1>{labels[kind]}</h1><p>Independent tags owned by each profile. Everyone in this shared workspace can view the profiles.</p></div></section>
    <PortfolioTabs active={kind} userId={selected} />
    <label className="portfolio-selector">Test profile<select value={selected} onChange={event => setUserId(event.target.value)}>{(profiles.data?.items ?? []).map(profile => <option key={profile.user_id} value={profile.user_id}>{profile.display_name}{!profile.active ? " (inactive)" : ""}</option>)}</select></label>
    {Boolean(profiles.error) && <p role="alert">{errorMessage(profiles.error)}</p>}
    {selected ? <div key={`${kind}/${selected}`} className="portfolio-details"><PnlReport kind={kind} userId={selected} /><CatalogManager kind={kind} userId={selected} /></div> : <p>No profile available.</p>}
  </main>;
}
