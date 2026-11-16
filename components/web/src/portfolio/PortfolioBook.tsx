import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import type { AccountFee, AttrValue, BrokerAccount, CatalogEntry, CatalogKind, Dimension, IbkrValues, ItemsPage, OrderAssignment, OrdersPage, PnlDetail, PnlGroup, PnlReport as PnlReportPayload, Profile, ReconciliationRow, StatementInfo, TaggedOrder } from "./types";
import { errorMessage, fetchJson, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
import { toNumber } from "../shared/market";
import { Amount, EmptyState, GroupByChips, PortfolioHeader, PortfolioIcon, ProfileSelect, setPortfolioQuery, usePortfolioRefresh } from "./PortfolioUI";
import { sumDecimals, treeKeys, useDimensions, useGroupBy, type Level } from "./pivot";
import { DISPLAY_CURRENCIES, FX_UNAVAILABLE, inCurrency } from "./fx";
import { attributeLevel, pnlSections, tagLevel, TAG_LEVELS, type PnlNode, type PnlSection, type PnlTotals } from "./pnlPivot";
import "./Portfolio.css";

const API = "/hub/api/v1/portfolio";
export const kinds: CatalogKind[] = ["themes", "strategies", "trade-names"];
export const labels = { themes: "Themes", strategies: "Strategies", "trade-names": "Trade names" };
export const singular = { themes: "theme", strategies: "strategy", "trade-names": "trade name" };
export const fields = { themes: "theme_id", strategies: "strategy_id", "trade-names": "trade_name_id" } as const;
export const hasTags = (order: OrderAssignment) => kinds.every(kind => Boolean(order[fields[kind]]));

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

export const issueLabels: Record<string, string> = {
  incomplete_history: "Opening history does not reconcile with the broker snapshot",
  [FX_UNAVAILABLE]: "No recent broker exchange rate into the display currency",
  missing_price: "Price unavailable", stale_price: "Stale price", unassigned: "Unassigned orders",
  missing_commission: "Commission not yet reported", missing_commission_currency: "Commission currency unavailable",
  unsupported_instrument: "PnL unsupported for this instrument", invalid_fill: "Invalid fill data",
  missing_instrument: "Instrument identity unavailable", history_unverified: "No snapshot available to check recorded history",
  missing_currency: "Trade currency unavailable",
  ibkr_no_statement: "No IBKR Flex statement recorded for this account",
  ibkr_awaiting_statement: "Traded or changed since the latest IBKR Flex statement; run a Flex sync",
  ibkr_missing_realized: "IBKR has not reported realized PnL for a fill yet",
  ibkr_missing_basis: "IBKR statement has no cost basis for this position",
  ibkr_split_position: "Open position is held under several tags; IBKR reports one cost basis for it (enable lot detail in the Flex query to split it)",
};

function PnlReport({ kind, userId, selected, onSelect }: { kind: CatalogKind; userId: string; selected: string; onSelect: (value: string) => void }) {
  const accounts = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${userId}`, 60_000);
  const account = accounts.data?.items.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected);
  const filter = account ? `&broker_code=${encodeURIComponent(account.broker_code)}&account_id=${encodeURIComponent(account.account_id)}` : "";
  const unavailable = Boolean(selected && accounts.data && !account);
  const [period, setPeriod] = useState<Period>(() => new URLSearchParams(window.location.search).get("period") === "ytd" ? "ytd" : "all");
  const choosePeriod = (value: Period) => {
    setPeriod(value);
    const url = new URL(window.location.href);
    if (value === "ytd") url.searchParams.set("period", "ytd"); else url.searchParams.delete("period");
    window.history.replaceState(null, "", url);
  };
  const [currency, setCurrency] = useState(() => new URLSearchParams(window.location.search).get("ccy")?.toUpperCase() || "USD");
  const chooseCurrency = (value: string) => {
    setCurrency(value);
    const url = new URL(window.location.href);
    if (value === "USD") url.searchParams.delete("ccy"); else url.searchParams.set("ccy", value);
    window.history.replaceState(null, "", url);
  };
  const currencies = [...new Set([...DISPLAY_CURRENCIES, currency])];
  return <section className="portfolio-section portfolio-performance"><div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Recorded performance</span><h2>PnL by {singular[kind]}</h2></div>
    <div className="portfolio-period" role="group" aria-label="PnL period">{(["all", "ytd"] as const).map(value => <button key={value} type="button" aria-pressed={period === value} onClick={() => choosePeriod(value)}>{value === "ytd" ? `YTD ${ytdStart().slice(0, 4)}` : "All time"}</button>)}</div>
    <label className="portfolio-selector"><span className="sr-only">PnL account</span><select value={selected} onChange={event => onSelect(event.target.value)} disabled={accounts.loading && !accounts.data}><option value="">All profile accounts</option>
      {selected && !account && <option value={selected} disabled>{accounts.loading ? "Loading account…" : "Account unavailable"}</option>}
      {(accounts.data?.items ?? []).map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>{item.broker_code.toUpperCase()} · {item.account_id}</option>)}
    </select></label>
    <label className="portfolio-selector"><span className="sr-only">Display currency</span><select aria-label="Display currency" value={currency} onChange={event => chooseCurrency(event.target.value)}>
      {currencies.map(code => <option key={code} value={code}>{code}</option>)}</select></label></div>
    {Boolean(accounts.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(accounts.error)}</p>}
    {selected && !account ? <p className="portfolio-empty-inline" role={unavailable ? "alert" : "status"}>{accounts.loading ? "Loading account…" : "The selected account is unavailable. Choose an account to view its PnL."}</p> : <PnlValues key={`${filter}/${period}`} kind={kind} userId={userId} filter={filter} period={period} currency={currency} />}
    <details className="portfolio-calculation"><summary>How PnL is calculated</summary><p>All time: cumulative PnL from recorded stock and ETF fills. Total = realized + unrealized − commissions. YTD: realized PnL from closing fills and commissions on fills since 1 January (New York trading day); lots opened earlier keep their original cost. Unrealized is the current mark of open lots, so the YTD figure is net realized = realized − fees. An instrument that cannot be valued (for example, shares bought before the recorded history) is left out of its group's PnL and named under the group, but its fees still count. Matching uses FIFO within the same account, instrument, currency and three tags; use the same tags on closing orders. Retagging recalculates the recorded history.</p>
      <p>Values are shown in the selected currency, converted at each account's latest broker exchange rate (from the statement or Gateway snapshot); a holding with no recent rate is excluded and flagged, never guessed. Paper/live accounts and simulations stay separate. Missing data is unavailable, never zero. Dividends, financing and FX conversion are excluded. Currency conversions are not positions: they are left out of the table, and their commissions are listed with the account fees (data subscriptions, broker fees, commission adjustments from Flex statements) below it, outside the totals. Snapshot quantity reconciliation cannot prove complete inception history.</p>
      <p>IBKR column: IBKR’s own figures from Flex statements, already net of commissions, so IBKR = realized + unrealized with no fees subtracted. Realized is each fill’s FIFO realized PnL, shown under the tags of the order that closed it. IBKR matches lots across the whole account, not per tag, so for an instrument held under several tags its realized PnL can land on a different tag than ours. Unrealized is IBKR’s open-position cost basis valued at the same price as ours, shown under the tag that holds the position. The reconciliation compares each instrument’s all-time total, which does not depend on lot matching, so the two should agree.</p></details>
  </section>;
}

/** Names the instruments a row's totals leave out (they cannot be valued; their fees still count). */
function ExcludedNote({ rows, total }: { rows: PnlGroup["excluded"]; total: string | null }) {
  if (!rows?.length) return null;
  return <small className="portfolio-cell-note" title={rows.map(row => `${row.trading_symbol}: ${row.issues.filter(issue => issue !== "unassigned").map(issue => issueLabels[issue] ?? issue).join(", ")}`).join("\n")}>Excludes {symbolList(rows)}{total !== null ? " (fees included)" : ""}</small>;
}

/** A few symbols by name; a long list as a count (the full list is in the cell's title). */
function symbolList(rows: { trading_symbol: string }[]): string {
  const symbols = [...new Set(rows.map(row => row.trading_symbol))];
  return symbols.length <= 3 ? symbols.join(", ") : `${symbols.length} instruments`;
}

const ownLevel = { themes: "theme", strategies: "strategy", "trade-names": "trade_name" } as const;
type SortKey = "realized" | "unrealized" | "commissions" | "total" | "ibkr_total";

function PnlValues({ kind, userId, filter, period, currency }: { kind: CatalogKind; userId: string; filter: string; period: Period; currency: string }) {
  const ytd = period === "ytd";
  const dimensions = useDimensions();
  const own = ownLevel[kind];
  const [groupBy, setGroupBy] = useGroupBy([own]);
  const requested = dimensions.filter(dimension => groupBy.includes(dimension.id));
  const dims = requested.map(dimension => dimension.id).join(",");
  const remote = useRemote<PnlReportPayload>(`${API}/pnl/${kind}?user_id=${userId}${filter}${ytd ? `&since=${ytdStart()}` : ""}${dims ? `&dims=${encodeURIComponent(dims)}` : ""}`, 60_000);
  const catalog = (tag: CatalogKind) => `${API}/catalogs/${tag}?user_id=${encodeURIComponent(userId)}`;
  const themes = useRemote<ItemsPage<CatalogEntry>>(catalog("themes"), 60_000);
  const strategies = useRemote<ItemsPage<CatalogEntry>>(catalog("strategies"), 60_000);
  const names = useRemote<ItemsPage<CatalogEntry>>(catalog("trade-names"), 60_000);
  const catalogs = { themes, strategies, "trade-names": names };
  const metric = ytd ? "Net realized YTD" : "Total PnL";
  // Every holding in the display currency at the broker's rates; same-tag groups merge across currencies.
  const groups = remote.data ? inCurrency(remote.data.items, currency, remote.data.fx_rates ?? []) : [];
  const unavailable = remote.data?.unavailable_dimensions ?? [];
  // Group by an attribute only once the API has labelled every holding, so an unlabelled one is never shown as Unclassified.
  const failed = requested.filter(dimension => unavailable.includes(dimension.id));
  const labelled = (dimension: Dimension) => !failed.includes(dimension) && remote.data !== null && groups.every(group => group.details.every(detail => detail.attributes?.[dimension.id]));
  const pending = requested.filter(dimension => !failed.includes(dimension) && !labelled(dimension));
  const levels = groupBy.flatMap((id): Level<PnlDetail>[] => {
    const tag = TAG_LEVELS.find(item => item.id === id);
    if (tag) {
      const entries = catalogs[tag.kind].data?.items;
      const known = new Map((entries ?? []).map(entry => [entry.id, entry.name]));
      if (tag.id === own) for (const group of groups) if (group.id !== null) known.set(group.id, group.name);
      return [tagLevel(tag, entries || tag.id === own ? known : null)];
    }
    const dimension = requested.find(item => item.id === id);
    return dimension && labelled(dimension) ? [attributeLevel(dimension)] : [];
  });
  const shown = levels.length ? levels : [tagLevel<PnlDetail>(TAG_LEVELS.find(item => item.id === own)!, new Map(groups.map(group => [group.id ?? "", group.name])))];
  const sections = remote.data ? pnlSections(groups, shown, own, ytd) : [];
  const available = [...TAG_LEVELS.map(tag => ({ id: tag.id, label: tag.label })), ...dimensions.map(dimension => ({ id: dimension.id, label: dimension.label }))];
  return <><div className="portfolio-report-tools">
    <GroupByChips available={available} value={groupBy} onChange={setGroupBy} />
    <button className="portfolio-button portfolio-button--ghost" onClick={remote.refresh} disabled={remote.refreshing}><PortfolioIcon name="refresh" />{remote.refreshing ? "Refreshing…" : "Refresh PnL"}</button></div>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "PnL refresh failed.")} Any visible report is from the last successful refresh.</p>}
    {failed.map(dimension => <p key={dimension.id} className="portfolio-notice portfolio-notice--error" role="alert">{dimension.label} classification is unavailable right now, so the report is shown without that level.</p>)}
    {pending.length > 0 && remote.data && <p className="portfolio-empty-inline" role="status">Labelling holdings by {pending.map(dimension => dimension.label.toLowerCase()).join(" and ")}…</p>}
    {remote.loading && !remote.data ? <p className="portfolio-empty-inline" role="status">Calculating PnL…</p> : remote.data?.items.length === 0 ? <EmptyState title="No performance to show yet">PnL appears here when this profile has recorded fills or holdings.</EmptyState>
      : remote.data && <PnlTree key={shown.map(level => level.id).join(",")} sections={sections} levels={shown} ytd={ytd} userId={userId} metric={metric} />}
    {remote.data?.reconciliation && <Reconciliation rows={remote.data.reconciliation} statements={remote.data.statements ?? []} />}
    {remote.data && <AccountFees rows={remote.data.account_fees ?? []} ytd={ytd} />}
  </>;
}

const sortLast = (node: { value: AttrValue }) => node.value.id === null ? 1 : 0;

/** The PnL report as one tree table: a section per currency, the same columns at every depth. */
function PnlTree({ sections, levels, ytd, userId, metric }: { sections: PnlSection[]; levels: Level<PnlDetail>[]; ytd: boolean; userId: string; metric: string }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(levels.length > 1 ? sections.flatMap(section => section.nodes.map(node => node.key)) : []));
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "total", desc: true });
  const suffix = ytd ? " YTD" : "";
  const allKeys = sections.flatMap(section => treeKeys(section.nodes));
  const toggle = (key: string) => setOpen(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const compare = (a: number | null, b: number | null) => a === null ? (b === null ? 0 : 1) : b === null ? -1 : sort.desc ? b - a : a - b;
  const ordered = (nodes: PnlNode[]) => [...nodes].sort((a, b) => sortLast(a) - sortLast(b) || compare(toNumber(a.totals[sort.key]), toNumber(b.totals[sort.key])));
  const orderedRows = (rows: PnlDetail[]) => [...rows].sort((a, b) => compare(toNumber(a[sort.key]), toNumber(b[sort.key])) || a.trading_symbol.localeCompare(b.trading_symbol));
  const columns: [SortKey, string, string][] = [
    ["realized", `Realized${suffix}`, ""], ["unrealized", ytd ? "Unrealized (open)" : "Unrealized", ""], ["commissions", `Fees${suffix}`, ""],
    ["total", metric, " portfolio-total"], ["ibkr_total", `IBKR${suffix}`, " portfolio-ibkr"],
  ];
  return <>
    <div className="portfolio-tree-tools"><span className="portfolio-footnote">Values in {sections[0]?.currency ?? "the display currency"} at the latest broker exchange rates{sections.length > 1 ? "; paper, live and simulated accounts stay separate" : ""} · — means unavailable</span>
      <span className="portfolio-tree-tools__buttons"><button type="button" className="portfolio-button portfolio-button--ghost" onClick={() => setOpen(new Set(allKeys))}>Expand all</button>
        <button type="button" className="portfolio-button portfolio-button--ghost" disabled={open.size === 0} onClick={() => setOpen(new Set())}>Collapse all</button></span></div>
    <div className="portfolio-table-scroll" role="region" aria-label="Performance table" tabIndex={0}><table className="portfolio-pnl-table portfolio-tree" role="treegrid" aria-label={`PnL by ${levels[0].label.toLowerCase()}`}>
      <thead><tr><th scope="col">{levels.map(level => level.label).join(" › ")}</th>{columns.map(([key, label, extra]) => <th key={key} scope="col" className={`portfolio-numeric${extra}`} aria-sort={sort.key === key ? sort.desc ? "descending" : "ascending" : undefined}
        title={key === "ibkr_total" ? "IBKR-reported, net of commissions" : undefined}>
        <button type="button" className="portfolio-sort" onClick={() => setSort(sort.key === key ? { key, desc: !sort.desc } : { key, desc: true })}>{label}<span aria-hidden="true">{sort.key === key ? sort.desc ? " ↓" : " ↑" : ""}</span></button></th>)}</tr></thead>
      {sections.map(section => {
        const where = [section.currency, section.account_mode, ...(section.simulated ? ["simulated"] : [])].join(", ");
        const scale = Math.max(0, ...treeValues(section.nodes).map(value => Math.abs(value)));
        const cells = (values: PnlTotals | PnlDetail, excluded?: PnlTotals["ibkr_excluded"]) => <>
          <td className="portfolio-numeric"><Amount value={values.realized} signed /></td><td className="portfolio-numeric"><Amount value={values.unrealized} signed /></td>
          <td className="portfolio-numeric"><Amount value={values.commissions} /></td>
          <td className="portfolio-numeric portfolio-total"><Amount value={values.total} signed /><DataBar value={toNumber(values.total)} scale={scale} /></td>
          <IbkrCell values={values} excluded={excluded} ytd={ytd} ours={values.total} /></>;
        const holding = (detail: PnlDetail, depth: number, key: string) => <tr key={key} aria-level={depth + 1} className="portfolio-tree-leaf">
          <th scope="row" style={{ paddingLeft: `${12 + depth * 20}px` }}><div className="portfolio-tree-name"><span className="portfolio-tree-spacer" />
            <div><strong>{detail.trading_symbol}</strong><div className="portfolio-group-meta"><span>Qty <Amount value={detail.quantity} quantity /></span>
              {detail.issues.some(issue => issue !== "unassigned") && <span className="portfolio-stale" title={detail.issues.map(issue => issueLabels[issue] ?? issue).join(" · ")}>{badgeFor(detail.issues)}</span>}
              <a className="portfolio-text-link" title={[detail.price_time ? `Valued ${formatTime(detail.price_time)}` : "No open-position valuation", detail.history_start ? `Recorded since ${formatTime(detail.history_start)}` : ""].filter(Boolean).join(" · ")}
                href={`/portfolio?user_id=${userId}&account=${encodeURIComponent(JSON.stringify([detail.broker_code, detail.account_id]))}`}>{detail.orders.length ? `${detail.orders.length} contributing ${detail.orders.length === 1 ? "order" : "orders"}` : "View account"}<PortfolioIcon name="arrow" /></a></div></div></div></th>
          {cells(detail)}</tr>;
        const row = (node: PnlNode, path: string[]): ReactNode[] => {
          const isOpen = open.has(node.key);
          const expandable = node.children.length > 0 || node.rows.length > 0;
          const name = [...path, node.value.label];
          const next = levels[node.depth + 1]?.label.toLowerCase();
          const count = node.children.length && next ? `${node.children.length} ${next}${node.children.length === 1 ? "" : "s"}` : `${node.rows.length} ${node.rows.length === 1 ? "holding" : "holdings"}`;
          return [<tr key={node.key} aria-level={node.depth + 1} aria-expanded={expandable ? isOpen : undefined} className={`portfolio-tree-row portfolio-tree-row--d${Math.min(node.depth, 3)}${isOpen ? " is-expanded" : ""}`}>
            <th scope="row" style={{ paddingLeft: `${12 + node.depth * 20}px` }}><div className="portfolio-tree-name">
              {expandable ? <button type="button" className="portfolio-expand" aria-expanded={isOpen} aria-label={`${isOpen ? "Collapse" : "Expand"} ${name.join(" › ")} (${where})`} onClick={() => toggle(node.key)}><PortfolioIcon name="chevron" /></button> : <span className="portfolio-tree-spacer" />}
              <div><strong>{node.value.label}</strong><div className="portfolio-group-meta"><span>{count}</span>
                {node.totals.issues.length > 0 && <span className="portfolio-stale" title={node.totals.issues.map(issue => issueLabels[issue] ?? issue).join(" · ")}>{badgeFor(node.totals.issues)}</span>}</div>
                <ExcludedNote rows={node.totals.excluded} total={node.totals.total} /></div></div></th>
            {cells(node.totals, node.totals.ibkr_excluded)}</tr>,
            ...(!isOpen ? [] : node.children.length ? ordered(node.children).flatMap(child => row(child, name)) : orderedRows(node.rows).map((detail, index) => holding(detail, node.depth + 1, `${node.key}#${index}`)))];
        };
        return <tbody key={section.key} aria-label={where}>
          <tr className="portfolio-tree-section"><th scope="rowgroup"><span className="portfolio-currency">{section.currency}</span><span className="portfolio-tree-section__mode">{section.account_mode}{section.simulated ? " · simulated" : ""}</span></th>
            <td className="portfolio-numeric"><Amount value={section.totals.realized} signed /></td><td className="portfolio-numeric"><Amount value={section.totals.unrealized} signed /></td>
            <td className="portfolio-numeric"><Amount value={section.totals.commissions} /></td><td className="portfolio-numeric portfolio-total"><Amount value={section.totals.total} signed /></td>
            <IbkrCell values={section.totals} ytd={ytd} ours={section.totals.total} /></tr>
          {ordered(section.nodes).flatMap(node => row(node, []))}
        </tbody>;
      })}
    </table></div></>;
}

/** Every total in a tree, nodes and holdings, to scale a section's bars. */
function treeValues(nodes: PnlNode[]): number[] {
  return nodes.flatMap(node => [toNumber(node.totals.total), ...node.rows.map(row => toNumber(row.total)), ...treeValues(node.children)].filter((value): value is number => value !== null));
}

/** Diverging bar from the cell's centre: right for gains, left for losses (sign and colour also on the figure). */
function DataBar({ value, scale }: { value: number | null; scale: number }) {
  if (value === null || !scale) return null;
  const width = Math.min(50, (Math.abs(value) / scale) * 50);
  return <span className="portfolio-databar" aria-hidden="true"><i className={value < 0 ? "is-negative" : "is-positive"} style={value < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} /></span>;
}

type Period = "all" | "ytd";

/** Name the issue that actually blocks or weakens the numbers, worst first. */
const badgePriority: [string, string][] = [
  ["incomplete_history", "History incomplete"], ["unsupported_instrument", "Unsupported instrument"],
  ["missing_commission", "Fees not yet reported"], ["missing_commission_currency", "Fee currency unknown"],
  ["invalid_fill", "Invalid fill"], ["missing_instrument", "Unknown instrument"], ["missing_currency", "Currency unknown"], [FX_UNAVAILABLE, "No FX rate"],
  ["missing_price", "Price unavailable"], ["stale_price", "Stale price"], ["history_unverified", "Unverified history"],
  ["unassigned", "Needs tags"],
];
function badgeFor(issues: string[]): string {
  return badgePriority.find(([issue]) => issues.includes(issue))?.[1] ?? "Review data";
}

/** First New York trading date of the current year, as the API's `since`. */
function ytdStart(): string {
  const year = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric" }).format(new Date());
  return `${year}-01-01`;
}

/** IBKR's figure beside ours; the title carries its realized/unrealized split and why parts are missing. */
function IbkrCell({ values, excluded = [], ytd, ours }: { values: IbkrValues; excluded?: { trading_symbol: string }[]; ytd: boolean; ours?: string | null }) {
  const issues = values.ibkr_issues ?? [];
  const gap = ours == null || values.ibkr_total == null ? null : sumDecimals([values.ibkr_total, [ours, -1]]);
  const differs = gap !== null && Math.abs(Number(gap)) >= 0.005;
  const title = [
    `IBKR realized ${values.ibkr_realized ?? "unavailable"}${ytd ? "" : ` · unrealized ${values.ibkr_unrealized ?? "unavailable"}`} (net of commissions)`,
    ...issues.map(issue => issueLabels[issue] ?? issue),
  ].join("\n");
  return <td className="portfolio-numeric portfolio-ibkr" title={differs ? `${title}\nDiffers from ours by ${gap}` : title}><Amount value={values.ibkr_total} signed />
    {differs && <span className="portfolio-ibkr-gap" aria-label={`Differs from ours by ${gap}`}>≠</span>}
    {excluded.length > 0 && <small className="portfolio-cell-note" title={excluded.map(row => row.trading_symbol).join(", ")}>Excludes {symbolList(excluded)}</small>}
    {excluded.length === 0 && values.ibkr_total == null && issues.length > 0 && <small className="portfolio-cell-note">{issueLabels[issues[0]]?.split(";")[0] ?? issues[0]}</small>}
  </td>;
}

const reconciliationLabels: Record<ReconciliationRow["status"], string> = {
  match: "Matches", mismatch: "Differs", ours_unavailable: "Ours unavailable",
  ibkr_unavailable: "IBKR unavailable", not_comparable: "Fees in another currency",
};

/** All-time instrument totals, ours vs IBKR; problems first, matches collapsed. */
function Reconciliation({ rows, statements }: { rows: ReconciliationRow[]; statements: StatementInfo[] }) {
  const [showAll, setShowAll] = useState(false);
  const matched = rows.filter(row => row.status === "match").length;
  const differing = rows.filter(row => row.status === "mismatch").length;
  const visible = showAll ? rows : rows.filter(row => row.status !== "match");
  const latest = statements.map(row => row.generated_at).filter((value): value is string => Boolean(value)).sort().at(-1);
  const label = "Reconciliation with IBKR";
  return <details className="portfolio-fold portfolio-reconciliation"><summary><PortfolioIcon name="chevron" /><h3>{label}</h3>
      <span className="portfolio-footnote">All time, per instrument · {matched} of {rows.length} match{differing > 0 && <strong className="portfolio-stale"> · {differing} differ</strong>}{latest ? <> · IBKR statement {formatTime(latest)}</> : " · no IBKR statement recorded"}</span></summary>
    {rows.length > 0 && matched > 0 && <div className="portfolio-fold__tools"><button type="button" className="portfolio-button portfolio-button--ghost" aria-pressed={showAll} onClick={() => setShowAll(!showAll)}>{showAll ? "Hide matches" : "Show matches"}</button></div>}
    {rows.length === 0 ? <p className="portfolio-footnote">No stock or ETF positions or fills to reconcile.</p> : visible.length === 0 ? <p className="portfolio-footnote">Every instrument matches IBKR.</p> :
      <div className="portfolio-table-scroll" role="region" aria-label={label} tabIndex={0}><table aria-label={label}><thead><tr><th scope="col">Instrument / Account</th><th scope="col">Currency</th><th scope="col" className="portfolio-numeric">Ours</th><th scope="col" className="portfolio-numeric">IBKR</th><th scope="col" className="portfolio-numeric">Difference</th><th scope="col">Status</th></tr></thead>
        <tbody>{visible.map(row => <tr key={JSON.stringify([row.broker_code, row.account_id, row.trading_symbol, row.currency])}>
          <th scope="row"><strong>{row.trading_symbol || "Unknown"}</strong><small className="portfolio-cell-note">{row.broker_code.toUpperCase()} · {row.account_id}</small></th>
          <td><span className="portfolio-currency">{row.currency}</span></td>
          <td className="portfolio-numeric" title={`Realized ${row.ours.realized ?? "unavailable"} + unrealized ${row.ours.unrealized ?? "unavailable"} − fees ${row.ours.commissions ?? "unavailable"}`}><Amount value={row.ours.total} signed /></td>
          <td className="portfolio-numeric" title={`Realized ${row.ibkr.realized ?? "unavailable"} + unrealized ${row.ibkr.unrealized ?? "unavailable"} (net of commissions)`}><Amount value={row.ibkr.total} signed /></td>
          <td className="portfolio-numeric"><Amount value={row.difference} signed /></td>
          <td><span className={row.status === "match" ? "" : "portfolio-stale"}>{reconciliationLabels[row.status] ?? row.status}</span>
            {row.reasons.length > 0 && <small className="portfolio-cell-note">{row.reasons.map(reason => issueLabels[reason] ?? reason).join(" · ")}</small>}</td>
        </tr>)}</tbody></table></div>}
    {statements.length > 0 && !statements.some(row => row.lot_detail) && <p className="portfolio-footnote">IBKR open positions are reported per instrument, so an instrument open under several tags shows no IBKR figure per tag. Add “Lot” level of detail to the Open Positions section of the Flex query to split it by opening order.</p>}
  </details>;
}

/** Account-level Flex costs, kept apart because they cannot be attributed to a tag. */
function AccountFees({ rows, ytd }: { rows: AccountFee[]; ytd: boolean }) {
  const label = `Account fees${ytd ? " YTD" : ""}`;
  return <details className="portfolio-fold portfolio-account-fees" open={rows.length > 0}><summary><PortfolioIcon name="chevron" /><h3>{label}</h3><span className="portfolio-footnote">From IBKR · not included in the totals above{rows.length === 0 ? " · none reported" : ""}</span></summary>
    {rows.length === 0 ? <p className="portfolio-footnote">No account-level fees, commission adjustments or FX conversion commissions reported {ytd ? "this year" : "in recorded statements"}. Run a Flex sync if the latest statement is missing.</p> :
      <div className="portfolio-table-scroll" role="region" aria-label={label} tabIndex={0}><table aria-label={label}><thead><tr><th scope="col">Currency</th><th scope="col" className="portfolio-numeric">Fees</th><th scope="col" className="portfolio-numeric">Commission adjustments</th><th scope="col" className="portfolio-numeric">FX conversions</th><th scope="col" className="portfolio-numeric portfolio-total">Total</th></tr></thead>
        <tbody>{rows.map(row => <tr key={JSON.stringify([row.broker_code, row.account_id, row.currency])}><th scope="row"><span className="portfolio-currency">{row.currency}</span>{row.simulated && <div className="portfolio-group-meta"><span>Simulated</span></div>}</th><td className="portfolio-numeric"><Amount value={row.fees} /></td><td className="portfolio-numeric"><Amount value={row.commission_adjustments} signed /></td><td className="portfolio-numeric"><Amount value={row.fx_conversions ?? "0"} /></td><td className="portfolio-numeric portfolio-total"><Amount value={row.total} /></td></tr>)}</tbody></table></div>}
  </details>;
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
    {Boolean(profiles.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(profiles.error)}</p>}
    {selected ? <div key={`${kind}/${selected}`} className="portfolio-details"><PnlReport kind={kind} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} /><CatalogManager kind={kind} userId={selected} /></div> : profiles.loading ? <p className="portfolio-empty-inline" role="status">Loading profiles…</p> : <EmptyState title="No profile available">Register a profile to create tags and track performance.</EmptyState>}
    <p className="portfolio-workspace-note">Shared workspace · Catalogs belong to the selected profile. Archived entries remain in historical assignments.</p>
  </main>;
}
