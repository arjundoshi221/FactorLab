import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";

import type { BrokerAccount, CatalogEntry, CatalogKind, ItemsPage, OrderAssignment, OrdersPage, PnlGroup, Profile, TaggedOrder } from "../portfolioTypes";
import { errorMessage, fetchJson, useRemote } from "../shared/api";
import { SignedBars } from "../shared/charts";
import { formatTime } from "../shared/format";
import { compactMoney, toNumber } from "../shared/market";
import { Amount, EmptyState, PortfolioHeader, PortfolioIcon, ProfileSelect, setPortfolioQuery, usePortfolioRefresh } from "./PortfolioUI";
import "./Portfolio.css";

const API = "/hub/api/v1/portfolio";
export const kinds: CatalogKind[] = ["themes", "strategies", "trade-names"];
export const labels = { themes: "Themes", strategies: "Strategies", "trade-names": "Trade names" };
export const singular = { themes: "theme", strategies: "strategy", "trade-names": "trade name" };
export const fields = { themes: "theme_id", strategies: "strategy_id", "trade-names": "trade_name_id" } as const;
export const hasTags = (order: OrderAssignment) => kinds.every(kind => Boolean(order[fields[kind]]));

export function PortfolioTabs({ active, userId = "", account = "" }: { active: "accounts" | "trades" | CatalogKind; userId?: string; account?: string }) {
  const query = new URLSearchParams({ user_id: userId });
  if (account) query.set("account", account);
  return <nav className="portfolio-tabs" aria-label="Portfolio screens">
    <a href={`/portfolio?${query}`} aria-current={active === "accounts" ? "page" : undefined}>Accounts & orders</a>
    <a href={`/portfolio/trades?${query}`} aria-current={active === "trades" ? "page" : undefined}>Trades</a>
    {kinds.map(kind => <a key={kind} href={`/portfolio/${kind}?${query}`} aria-current={active === kind ? "page" : undefined}>{labels[kind]}</a>)}
  </nav>;
}

const NEW_ENTRY = "__new__";

/** One tag select; with a ``userId`` its last option creates a catalog entry in place and selects it. */
function TagSelect({ kind, value, onChange, entries, disabled, name, userId, onCreated }: {
  kind: CatalogKind; value: string | null; onChange: (value: string | null) => void; entries: CatalogEntry[];
  disabled: boolean; name: string; userId?: string; onCreated?: (kind: CatalogKind) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Entries created here stay selectable until the catalog refresh brings them back.
  const [created, setCreated] = useState<CatalogEntry[]>([]);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (adding) input.current?.focus(); }, [adding]);
  const options = [...entries, ...created.filter(entry => !entries.some(item => item.id === entry.id))];
  function cancel() { setAdding(false); setDraft(""); setError(""); }
  async function create() {
    const label = draft.trim();
    if (!label || !userId) return;
    setBusy(true); setError("");
    try {
      const entry = await fetchJson<{ id: string }>(`${API}/catalogs/${kind}?user_id=${encodeURIComponent(userId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: label, description: "", active: true }),
      });
      setCreated(list => [...list, { id: entry.id, user_id: userId, name: label, description: "", active: true }]);
      onChange(entry.id); setAdding(false); setDraft(""); onCreated?.(kind);
    } catch (caught) { setError(errorMessage(caught, `Could not create ${singular[kind]}.`)); }
    finally { setBusy(false); }
  }
  return <div className="portfolio-selector"><span>{singular[kind]}</span>
    {adding ? <div className="portfolio-new-entry">
      <input ref={input} aria-label={`New ${singular[kind]} name for ${name}`} maxLength={200} placeholder={`New ${singular[kind]} name`} value={draft} disabled={busy || disabled}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void create(); } else if (event.key === "Escape") { event.preventDefault(); cancel(); } }} />
      <button type="button" className="portfolio-button portfolio-button--primary" disabled={busy || disabled || !draft.trim()} onClick={() => void create()}>{busy ? "Adding…" : "Add"}</button>
      <button type="button" className="portfolio-button portfolio-button--ghost" disabled={busy} onClick={cancel}>Cancel</button>
    </div> : <select aria-label={`${labels[kind]} for ${name}`} value={value ?? ""} disabled={disabled}
      onChange={event => { if (event.target.value === NEW_ENTRY) setAdding(true); else onChange(event.target.value || null); }}>
      <option value="">Select {singular[kind]}</option>
      {options.filter(item => item.active || item.id === value).map(item =>
        <option key={item.id} value={item.id} disabled={!item.active}>{item.name}{!item.active ? " (archived)" : ""}</option>)}
      {userId && <option value={NEW_ENTRY}>+ New {singular[kind]}…</option>}
    </select>}
    {error && <small className="portfolio-field-error" role="alert">{error}</small>}
  </div>;
}

/** The theme / strategy / trade-name selects shared by every tag editor. */
export function TagSelectors({ tags, onChange, catalogs, disabled = false, name, userId, onCreated }: {
  tags: OrderAssignment; onChange: (tags: OrderAssignment) => void; catalogs: Record<CatalogKind, CatalogEntry[]>;
  disabled?: boolean; name: string; userId?: string; onCreated?: (kind: CatalogKind) => void;
}) {
  return <div className="portfolio-tag-selectors">{kinds.map(kind => <TagSelect key={kind} kind={kind} value={tags[fields[kind]]} entries={catalogs[kind]}
    disabled={disabled} name={name} userId={userId} onCreated={onCreated} onChange={value => onChange({ ...tags, [fields[kind]]: value })} />)}</div>;
}

function OrderEditor({ order, userId, base, catalogs, refresh, onCreated }: {
  order: TaggedOrder; userId: string; base: string; catalogs: Record<CatalogKind, CatalogEntry[]>; refresh: (message: string) => void;
  onCreated: (kind: CatalogKind) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState<OrderAssignment>({ theme_id: order.theme_id, strategy_id: order.strategy_id, trade_name_id: order.trade_name_id });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!editing) setTags({ theme_id: order.theme_id, strategy_id: order.strategy_id, trade_name_id: order.trade_name_id });
  }, [order.theme_id, order.strategy_id, order.trade_name_id, editing]);
  async function save(clear = false) {
    setBusy(true); setError("");
    const value = clear ? { theme_id: null, strategy_id: null, trade_name_id: null } : tags;
    try {
      await fetchJson(`${base}/orders/${encodeURIComponent(order.perm_id)}/assignment?user_id=${encodeURIComponent(userId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
      });
      setTags(value); setEditing(false); refresh(`${clear ? "Tags cleared" : "Tags saved"} for ${order.trading_symbol}.`);
    } catch (caught) { setError(errorMessage(caught, "Could not save order tags.")); }
    finally { setBusy(false); }
  }
  const complete = hasTags(tags);
  const assigned = hasTags(order);
  const dirty = kinds.some(kind => tags[fields[kind]] !== order[fields[kind]]);
  return <article className={`portfolio-order${editing ? " is-editing" : ""}`} aria-label={`${order.trading_symbol} order ${order.perm_id}`}>
    <div className="portfolio-order-heading"><div><div className="portfolio-order-title"><h4>{order.trading_symbol}</h4><span className={`portfolio-side${order.side === "BUY" ? " portfolio-side--buy" : ""}`}>{order.side}</span>
      <span className={`portfolio-badge${assigned ? " portfolio-badge--green" : " portfolio-badge--amber"}`}>{assigned ? "Tagged" : "Needs tags"}</span></div>
      <p><Amount value={order.filled_quantity} quantity /> filled<span>·</span>{order.currency}<span>·</span>{order.status}</p></div>
      {order.taggable && !editing && <button className={`portfolio-button ${assigned ? "portfolio-button--secondary" : "portfolio-button--primary"}`} onClick={() => { setEditing(true); setError(""); }}>{assigned ? "Edit tags" : "Assign tags"}</button>}
    </div>
    {!editing && assigned && <dl className="portfolio-tag-summary">{kinds.map(kind => <div key={kind}><dt>{singular[kind]}</dt><dd>{catalogs[kind].find(entry => entry.id === order[fields[kind]])?.name ?? "Archived entry"}</dd></div>)}</dl>}
    {editing && <form className="portfolio-tag-form" onSubmit={event => { event.preventDefault(); void save(); }}>
      <TagSelectors tags={tags} onChange={setTags} catalogs={catalogs} disabled={busy} name={`order ${order.perm_id}`} userId={userId} onCreated={onCreated} />
      <div className="portfolio-actions"><button className="portfolio-button portfolio-button--primary" disabled={busy || !complete || !dirty}>{busy ? "Saving…" : "Save tags"}</button>
        <button className="portfolio-button portfolio-button--secondary" type="button" disabled={busy} onClick={() => { setEditing(false); setError(""); }}>Cancel</button>
        {assigned && <button className="portfolio-button portfolio-button--ghost portfolio-clear" type="button" disabled={busy} onClick={() => void save(true)}>Clear tags</button>}</div>
      <p className="portfolio-footnote">All existing and future fills use these tags. Select all three to save, or pick “+ New” to create one.</p>
    </form>}
    {!order.taggable && <p className="portfolio-footnote">A registered profile and durable broker order ID are required for tagging.</p>}
    {error && <p className="portfolio-notice portfolio-notice--error" role="alert">{error}</p>}
    <details className="portfolio-order-details"><summary>{order.fills.length} {order.fills.length === 1 ? "fill" : "fills"}<span>Order details</span><PortfolioIcon name="chevron" /></summary>
      <p className="portfolio-footnote">Order ID <span className="portfolio-order-id">{order.perm_id}</span> · All fills inherit this order’s tags.</p>
      {order.fills.length > 0 ? <div className="portfolio-table-scroll" tabIndex={0} role="region" aria-label={`Fills table for ${order.perm_id}`}><table aria-label={`Fills for order ${order.perm_id}`}><thead><tr><th scope="col">Execution</th><th scope="col">Time (IST)</th><th scope="col" className="portfolio-numeric">Qty</th><th scope="col" className="portfolio-numeric">Price</th><th scope="col" className="portfolio-numeric">Fees</th></tr></thead>
        <tbody>{order.fills.map(fill => <tr key={String(fill.exec_id)}><td className="portfolio-order-id">{fill.exec_id}</td><td>{formatTime(String(fill.exec_time ?? ""), "—")}</td><td className="portfolio-numeric"><Amount value={fill.quantity} quantity /></td><td className="portfolio-numeric"><Amount value={fill.price} /></td><td className="portfolio-numeric"><Amount value={fill.commission} /> {fill.commission_ccy}</td></tr>)}</tbody></table></div> : <p className="portfolio-footnote">No fills reported yet.</p>}
    </details>
  </article>;
}

export function OrderTagging({ account, revision = 0 }: { account: BrokerAccount; revision?: number }) {
  const userId = account.user_id;
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [notice, setNotice] = useState("");
  const base = `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`;
  const suffix = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  const themes = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/themes${suffix}` : null, 60_000);
  const strategies = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/strategies${suffix}` : null, 60_000);
  const names = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/trade-names${suffix}` : null, 60_000);
  const orders = useRemote<OrdersPage>(userId ? `${base}/orders${suffix}&offset=${offset}&limit=50` : null, 60_000);
  usePortfolioRefresh(revision, themes.refresh, strategies.refresh, names.refresh, orders.refresh);
  const catalogs = { themes: themes.data?.items ?? [], strategies: strategies.data?.items ?? [], "trade-names": names.data?.items ?? [] };
  const refreshCatalog = (kind: CatalogKind) => ({ themes, strategies, "trade-names": names })[kind].refresh();
  const pending = [themes, strategies, names, orders];
  const items = orders.data?.items ?? [];
  const filtered = items.filter(order => `${order.trading_symbol} ${order.perm_id}`.toLowerCase().includes(search.trim().toLowerCase()) && (filter === "all" || hasTags(order) === (filter === "tagged")));
  const missing = kinds.filter(kind => !catalogs[kind].some(item => item.active));
  return <section className="portfolio-section portfolio-orders"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Organize your trades</span><h3>Orders & tags</h3></div>
    <button className="portfolio-button portfolio-button--ghost" aria-label="Refresh orders and tags" disabled={pending.some(remote => remote.refreshing)} onClick={() => pending.forEach(remote => remote.refresh())}><PortfolioIcon name="refresh" />Refresh</button></div>
    <p className="portfolio-section-copy">Connect each order to a theme, strategy and trade name.</p>
    {!userId ? <EmptyState title="Assign this account to a profile">Register a profile before tagging orders.</EmptyState> : <>
      {pending.some(remote => remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Orders or tag catalogs could not refresh. Visible data may be out of date; try refreshing again.</p>}
      {notice && <p className="portfolio-notice portfolio-notice--success" role="status">{notice}</p>}
      {pending.some(remote => remote.loading && !remote.data) ? <p className="portfolio-empty-inline" role="status">Loading order tags…</p> : <>
        {missing.length > 0 && !pending.some(remote => remote.error) && <div className="portfolio-setup"><strong>Set up your tags</strong><p>Create an entry in {missing.map((kind, index) => <Fragment key={kind}>{index > 0 && (index === missing.length - 1 ? " and " : ", ")}<a href={`/portfolio/${kind}${suffix}`}>{labels[kind]}</a></Fragment>)} to start organizing orders.</p></div>}
        {items.length > 0 && <div className="portfolio-order-tools"><label className="portfolio-search"><PortfolioIcon name="search" /><input aria-label="Search orders on this page" placeholder="Search symbol or order ID" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <select aria-label="Order tag status" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All orders</option><option value="unassigned">Needs tags</option><option value="tagged">Tagged</option></select></div>}
        {items.length > 0 && <p className="portfolio-list-meta">{filtered.length} of {items.length} orders on this page</p>}
        <div className="portfolio-order-list">{filtered.map(order => <OrderEditor key={order.key} order={order} userId={userId} base={base} catalogs={catalogs} onCreated={refreshCatalog} refresh={message => { setNotice(message); orders.refresh(); }} />)}</div>
        {orders.data && !filtered.length && <EmptyState title={items.length ? "No matching orders" : "No orders yet"}>{items.length ? "Try another symbol, order ID or tag status." : "Working and executed orders will appear here as they arrive."}</EmptyState>}
        {(offset > 0 || orders.data?.has_more) && <div className="portfolio-pagination"><button className="portfolio-button portfolio-button--secondary" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 50)); setSearch(""); }}>Previous orders</button>
          <span>Page {Math.floor(offset / 50) + 1}</span><button className="portfolio-button portfolio-button--secondary" disabled={!orders.data?.has_more} onClick={() => { setOffset(offset + 50); setSearch(""); }}>Next orders</button></div>}
      </>}
    </>}
  </section>;
}

function CatalogManager({ kind, userId }: { kind: CatalogKind; userId: string }) {
  const remote = useRemote<ItemsPage<CatalogEntry>>(`${API}/catalogs/${kind}?user_id=${userId}`, 60_000);
  const [editing, setEditing] = useState<CatalogEntry | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => { if (showForm) nameInput.current?.focus(); }, [showForm, editing?.id]);
  function reset() { setEditing(null); setName(""); setDescription(""); setShowForm(false); }
  async function write(entry: CatalogEntry | null, active: boolean, values = { name, description }) {
    setBusy(true); setError(""); setNotice("");
    try {
      await fetchJson(`${API}/catalogs/${kind}${entry ? `/${entry.id}` : ""}?user_id=${userId}`, {
        method: entry ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, active }),
      });
      reset(); setNotice(`${values.name} ${entry && active !== entry.active ? active ? "restored" : "archived" : entry ? "updated" : "created"}.`); remote.refresh();
    } catch (caught) { setError(errorMessage(caught, "Could not save catalog entry.")); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void write(editing, editing?.active ?? true); }
  const entries = remote.data?.items ?? [];
  const visible = entries.filter(entry => showArchived || entry.active);
  const archived = entries.filter(entry => !entry.active).length;
  return <section className="portfolio-section portfolio-library"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Your library</span><h2>{labels[kind]}</h2></div>
    <button className="portfolio-button portfolio-button--primary" disabled={busy || showForm} onClick={() => { reset(); setError(""); setShowForm(true); }}><PortfolioIcon name="plus" />New {singular[kind]}</button></div>
    <p className="portfolio-section-copy">Create reusable {labels[kind].toLowerCase()} for this profile.</p>
    {showForm && <form className="portfolio-catalog-form" onSubmit={submit}><h3>{editing ? `Edit ${singular[kind]}` : `New ${singular[kind]}`}</h3>
      <label className="portfolio-selector"><span>Name</span><input ref={nameInput} required maxLength={200} value={name} disabled={busy} placeholder={kind === "themes" ? "e.g. Energy transition" : kind === "strategies" ? "e.g. Momentum" : "e.g. Semiconductor basket"} onChange={event => setName(event.target.value)} /></label>
      <label className="portfolio-selector"><span>Description <small>Optional</small></span><textarea aria-label="Description" maxLength={10000} value={description} disabled={busy} placeholder="Add a short thesis or a note for later." onChange={event => setDescription(event.target.value)} /></label>
      <div className="portfolio-actions"><button className="portfolio-button portfolio-button--primary" disabled={busy || !name.trim()}>{busy ? "Saving…" : editing ? "Save changes" : `Create ${singular[kind]}`}</button>
        <button className="portfolio-button portfolio-button--secondary" type="button" onClick={reset} disabled={busy}>Cancel</button></div>
    </form>}
    {Boolean(error || remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{error || errorMessage(remote.error)} {Boolean(remote.error) && <button className="portfolio-button portfolio-button--ghost" disabled={remote.refreshing} onClick={remote.refresh}>Retry library</button>}</p>}
    {notice && <p className="portfolio-notice portfolio-notice--success" role="status">{notice}</p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Loading library…</p> : remote.data && <>
      <div className="portfolio-library-tools"><span>{entries.filter(entry => entry.active).length} active {labels[kind].toLowerCase()}</span>{archived > 0 && <label><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />Show archived ({archived})</label>}</div>
      <ul className="portfolio-catalog">{visible.map(entry => <li key={entry.id} className={!entry.active ? "is-archived" : ""}>
        <div><strong>{entry.name}</strong>{!entry.active && <span className="portfolio-badge">Archived</span>}{entry.description && <p>{entry.description}</p>}</div>
        <div className="portfolio-actions"><button className="portfolio-button portfolio-button--secondary" disabled={busy} onClick={() => { setEditing(entry); setName(entry.name); setDescription(entry.description); setShowForm(true); setError(""); }}>Edit</button>
          <button className="portfolio-button portfolio-button--ghost" disabled={busy} onClick={() => void write(entry, !entry.active, { name: entry.name, description: entry.description })}>{entry.active ? "Archive" : "Restore"}</button></div>
      </li>)}</ul>
      {visible.length === 0 && <EmptyState title={`No ${labels[kind].toLowerCase()} yet`}>Add your first {singular[kind]} to start tagging orders.</EmptyState>}
    </>}
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

function PnlReport({ kind, userId, selected, onSelect }: { kind: CatalogKind; userId: string; selected: string; onSelect: (value: string) => void }) {
  const accounts = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${userId}`, 60_000);
  const account = accounts.data?.items.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected);
  const filter = account ? `&broker_code=${encodeURIComponent(account.broker_code)}&account_id=${encodeURIComponent(account.account_id)}` : "";
  const unavailable = Boolean(selected && accounts.data && !account);
  return <section className="portfolio-section portfolio-performance"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Recorded performance</span><h2>PnL by {singular[kind]}</h2></div>
    <label className="portfolio-selector"><span className="sr-only">PnL account</span><select value={selected} onChange={event => onSelect(event.target.value)} disabled={accounts.loading && !accounts.data}><option value="">All profile accounts</option>
      {selected && !account && <option value={selected} disabled>{accounts.loading ? "Loading account…" : "Account unavailable"}</option>}
      {(accounts.data?.items ?? []).map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>{item.broker_code.toUpperCase()} · {item.account_id}</option>)}
    </select></label></div>
    {Boolean(accounts.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(accounts.error)}</p>}
    {selected && !account ? <p className="portfolio-empty-inline" role={unavailable ? "alert" : "status"}>{accounts.loading ? "Loading account…" : "The selected account is unavailable. Choose an account to view its PnL."}</p> : <PnlValues key={filter} kind={kind} userId={userId} filter={filter} />}
    <details className="portfolio-calculation"><summary>How PnL is calculated</summary><p>Current cumulative PnL from recorded stock and ETF fills. Total = realized + unrealized − commissions. Matching uses FIFO within the same account, instrument, currency and three tags; use the same tags on closing orders. Retagging recalculates the recorded history.</p>
      <p>Currencies, paper/live accounts and simulations stay separate. Missing data is unavailable, never zero. Dividends, financing and FX conversion are excluded. Snapshot quantity reconciliation cannot prove complete inception history.</p></details>
  </section>;
}

/** Total PnL per group as diverging bars, one chart per currency so values are never compared across currencies. */
function PnlBars({ groups, kind }: { groups: PnlGroup[]; kind: CatalogKind }) {
  const currencies = Array.from(new Set(groups.map(group => group.currency)));
  return <div className="portfolio-pnl-bars">{currencies.map(currency => {
    const bars = groups.filter(group => group.currency === currency).map(group => ({
      key: JSON.stringify([group.id, group.account_mode, group.simulated]), label: group.name, value: toNumber(group.total),
      display: `${compactMoney(toNumber(group.total))}`, title: `${group.name} · ${group.account_mode}${group.simulated ? " · simulated" : ""}: total ${group.total ?? "unavailable"} ${currency}`,
    })).sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return <figure key={currency}><figcaption>Total PnL by {singular[kind]} · {currency}</figcaption><SignedBars bars={bars} label={`Total PnL by ${singular[kind]}, ${currency}`} /></figure>;
  })}</div>;
}

function PnlValues({ kind, userId, filter }: { kind: CatalogKind; userId: string; filter: string }) {
  const remote = useRemote<ItemsPage<PnlGroup>>(`${API}/pnl/${kind}?user_id=${userId}${filter}`, 60_000);
  const [expanded, setExpanded] = useState<string | null>(null);
  return <><div className="portfolio-report-tools"><p>Realized + unrealized − fees <span>·</span> Values shown in each row’s currency</p><button className="portfolio-button portfolio-button--ghost" onClick={remote.refresh} disabled={remote.refreshing}><PortfolioIcon name="refresh" />{remote.refreshing ? "Refreshing…" : "Refresh PnL"}</button></div>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "PnL refresh failed.")} Any visible report is from the last successful refresh.</p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Calculating PnL…</p> : remote.data?.items.length === 0 ? <EmptyState title="No performance to show yet">PnL appears here when this profile has recorded fills or holdings.</EmptyState> : remote.data && <><PnlBars groups={remote.data.items} kind={kind} /><div className="portfolio-table-scroll" role="region" aria-label="Performance table" tabIndex={0}><table className="portfolio-pnl-table" aria-label={`PnL by ${singular[kind]}`}><thead><tr><th scope="col">{singular[kind]}</th><th scope="col">Currency</th><th scope="col" className="portfolio-numeric">Realized</th><th scope="col" className="portfolio-numeric">Unrealized</th><th scope="col" className="portfolio-numeric">Fees</th><th scope="col" className="portfolio-numeric portfolio-total">Total PnL</th><th scope="col"><span className="sr-only">Details</span></th></tr></thead><tbody>{remote.data.items.map(group => {
      const key = JSON.stringify([group.id, group.account_mode, group.simulated, group.currency]);
      const open = expanded === key;
      return <Fragment key={key}><tr className={open ? "is-expanded" : ""}><th scope="row"><strong>{group.name}</strong><div className="portfolio-group-meta"><span>{group.account_mode}</span>{group.simulated && <span>Simulated</span>}
        {group.issues.length > 0 && <span className="portfolio-stale" title={group.issues.map(issue => issueLabels[issue] ?? issue).join(" · ")}>{group.issues.includes("stale_price") ? "Stale price" : group.issues.every(issue => issue === "unassigned") ? "Needs tags" : "Review data"}</span>}</div></th>
        <td><span className="portfolio-currency">{group.currency}</span></td><td className="portfolio-numeric"><Amount value={group.realized} signed /></td><td className="portfolio-numeric"><Amount value={group.unrealized} signed /></td><td className="portfolio-numeric"><Amount value={group.commissions} /></td><td className="portfolio-numeric portfolio-total"><Amount value={group.total} signed /></td>
        <td><button className="portfolio-expand" aria-label={`${open ? "Hide" : "View"} holdings for ${group.name}, ${group.currency}, ${group.account_mode}${group.simulated ? ", simulated" : ""}`} aria-expanded={open} onClick={() => setExpanded(open ? null : key)}><PortfolioIcon name="chevron" /></button></td></tr>
        {open && <tr className="portfolio-expanded-row"><td colSpan={7}><div className="portfolio-holdings"><div className="portfolio-section-heading"><h3>{group.name} · Holdings & orders</h3><span className="portfolio-footnote">— means unavailable</span></div>
          {group.issues.length > 0 && <ul className="portfolio-issue-list">{group.issues.map(issue => <li key={issue}>{issueLabels[issue] ?? issue}</li>)}</ul>}
          {group.details.length === 0 ? <p className="portfolio-footnote">No contributing holdings reported.</p> : <table aria-label={`${group.name} holdings`}><thead><tr><th scope="col">Instrument / Account</th><th scope="col" className="portfolio-numeric">Quantity</th><th scope="col" className="portfolio-numeric">Realized</th><th scope="col" className="portfolio-numeric">Unrealized</th><th scope="col" className="portfolio-numeric">Fees</th><th scope="col" className="portfolio-numeric">Total</th><th scope="col">Valuation & orders</th></tr></thead>
            <tbody>{group.details.map((detail, index) => <tr key={index}><td><strong>{detail.trading_symbol}</strong><small className="portfolio-cell-note">{detail.broker_code.toUpperCase()} · {detail.account_id}</small></td><td className="portfolio-numeric"><Amount value={detail.quantity} quantity /></td><td className="portfolio-numeric"><Amount value={detail.realized} signed /></td><td className="portfolio-numeric"><Amount value={detail.unrealized} signed /></td><td className="portfolio-numeric"><Amount value={detail.commissions} /></td><td className="portfolio-numeric"><Amount value={detail.total} signed /></td>
              <td><span className="portfolio-cell-note">{detail.price_time ? formatTime(detail.price_time) : "No open-position valuation"}</span><small className="portfolio-cell-note">Recorded since {formatTime(detail.history_start)}</small><a className="portfolio-text-link" href={`/portfolio?user_id=${userId}&account=${encodeURIComponent(JSON.stringify([detail.broker_code, detail.account_id]))}`}>{detail.orders.length ? `${detail.orders.length} contributing ${detail.orders.length === 1 ? "order" : "orders"}` : "View account"}<PortfolioIcon name="arrow" /></a></td></tr>)}</tbody></table>}
        </div></td></tr>}
      </Fragment>;
    })}</tbody></table></div></>}
  </>;
}

export function PortfolioGroups({ kind }: { kind: CatalogKind }) {
  const profiles = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState(() => new URLSearchParams(window.location.search).get("user_id") ?? "");
  const [account, setAccount] = useState(() => new URLSearchParams(window.location.search).get("account") ?? "");
  const selected = profiles.data?.items.find(profile => profile.user_id === userId)?.user_id ?? profiles.data?.items.find(profile => profile.active)?.user_id ?? profiles.data?.items[0]?.user_id ?? "";
  const descriptions = { themes: "Track the investment ideas behind your portfolio.", strategies: "See how each approach contributes to your results.", "trade-names": "Follow each named trade from entry to exit." };
  return <main className="portfolio-page"><PortfolioHeader title={labels[kind]} description={descriptions[kind]}>
      <ProfileSelect profiles={profiles.data?.items ?? []} value={selected} disabled={profiles.loading && !profiles.data} onChange={value => { setUserId(value); setAccount(""); setPortfolioQuery(value); }} />
    </PortfolioHeader>
    <PortfolioTabs active={kind} userId={selected} account={account} />
    {Boolean(profiles.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(profiles.error)}</p>}
    {selected ? <div key={`${kind}/${selected}`} className="portfolio-details"><PnlReport kind={kind} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} /><CatalogManager kind={kind} userId={selected} /></div> : profiles.loading ? <p className="portfolio-empty-inline" role="status">Loading profiles…</p> : <EmptyState title="No profile available">Register a profile to create tags and track performance.</EmptyState>}
    <p className="portfolio-workspace-note">Shared workspace · Catalogs belong to the selected profile. Archived entries remain in historical assignments.</p>
  </main>;
}
