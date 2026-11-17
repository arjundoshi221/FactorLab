import { Fragment, useState, type ReactNode } from "react";

import type { BrokerAccount, CatalogEntry, CatalogKind, Dimension, ItemsPage, Profile, TradeBucket, TradeRow, TradeStatus, TradesPage, TradeSummary } from "./types";
import { Segmented } from "../components/ui";
import { errorMessage, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
import { signedPct, toNumber } from "../shared/market";
import { tickerHref, useUrlParams } from "../shared/urlState";
import { issueLabels, kinds, singular } from "./PortfolioBook";
import { Amount, EmptyState, PortfolioHeader, PortfolioIcon, ProfileSelect, usePortfolioRefresh } from "./PortfolioUI";
import { TradeLedger } from "./TradeLedger";
import { BucketLabel, BucketTable, TradePivotControl, useTradePivot } from "./tradePivot";
import "./Portfolio.css";
import "./TradeLedger.css";
import "./TradeHistory.css";

const API = "/hub/api/v1/portfolio";
const PAGE = 50;
const filterKeys = ["account", "symbol", "theme_id", "strategy_id", "trade_name_id", "start", "end", "bucket"] as const;
const roleLabel = { open: "Open", add: "Add", reduce: "Reduce", close: "Close" };
type View = "orders" | "positions";

/** How long a trade was (or has been) held, e.g. "12d", "5h", "<1h". */
export function holding(opened: string, closed: string | null, now = Date.now()): string {
  const hours = ((closed ? Date.parse(closed) : now) - Date.parse(opened)) / 3_600_000;
  if (!Number.isFinite(hours)) return "—";
  if (hours < 1) return "<1h";
  if (hours < 48) return `${Math.floor(hours)}h`;
  return `${Math.floor(hours / 24)}d`;
}

function ReturnPct({ value }: { value: string | null }) {
  const number = toNumber(value);
  const tone = number === null ? "neutral" : number > 0 ? "positive" : number < 0 ? "negative" : "neutral";
  return <span className={`portfolio-amount portfolio-amount--${tone}`}>{signedPct(number, 1)}</span>;
}

function tagLabel(row: TradeRow) {
  if (!row.theme_id) return <span className="portfolio-badge portfolio-badge--amber">Needs tags</span>;
  return <span title={[row.theme_name, row.strategy_name, row.trade_name_name].map(name => name ?? "Archived entry").join(" · ")}>
    {row.trade_name_name ?? row.theme_name ?? "Archived entry"}{row.tags_mixed && <small> mixed</small>}
  </span>;
}

function TradeDetail({ trade }: { trade: TradeRow }) {
  const open = trade.status === "open";
  const fees = trade.commissions.length ? trade.commissions.map(c => `${c.amount} ${c.currency}`).join(", ") : "not reported";
  return <div className="trade-detail">
    <dl className="trade-detail__facts">
      <div><dt>Opened (IST)</dt><dd>{formatTime(trade.opened_at, "—")}</dd></div>
      <div><dt>{open ? "Last fill (IST)" : "Closed (IST)"}</dt><dd>{formatTime(open ? trade.last_fill_at : trade.closed_at, "—")}</dd></div>
      <div><dt>Held</dt><dd>{holding(trade.opened_at, trade.closed_at)}</dd></div>
      <div><dt>Bought / sold</dt><dd><Amount value={trade.direction === "long" ? trade.entry_quantity : trade.exit_quantity} quantity /> / <Amount value={trade.direction === "long" ? trade.exit_quantity : trade.entry_quantity} quantity /></dd></div>
      <div><dt>Peak size</dt><dd><Amount value={trade.max_quantity} quantity /></dd></div>
      {open && <div><dt>Open cost <span>{trade.currency}</span></dt><dd><Amount value={trade.open_cost} /></dd></div>}
      {open && <div><dt>Mark</dt><dd><Amount value={trade.market_price} />{trade.price_time && <small> {formatTime(trade.price_time)}</small>}</dd></div>}
      <div><dt>Fees</dt><dd>{fees}</dd></div>
      <div><dt>Account</dt><dd>{trade.broker_code.toUpperCase()} {trade.account_id} · {trade.simulated ? "simulated" : trade.account_mode}</dd></div>
      <div><dt>Tags</dt><dd>{trade.theme_id ? `${trade.theme_name ?? "Archived entry"} · ${trade.strategy_name ?? "Archived entry"} · ${trade.trade_name_name ?? "Archived entry"}` : "Opening order not tagged"}{trade.tags_mixed && " (orders in this trade carry different tags)"}</dd></div>
    </dl>
    {trade.issues.length > 0 && <ul className="portfolio-issue-list">{trade.issues.map(issue => <li key={issue}>{issueLabels[issue] ?? issue}</li>)}</ul>}
    <table aria-label={`Fills for ${trade.trading_symbol} trade opened ${formatTime(trade.opened_at, "")}`}>
      <thead><tr><th scope="col">Time (IST)</th><th scope="col">Action</th><th scope="col">Side</th><th scope="col" className="portfolio-numeric">Qty</th><th scope="col" className="portfolio-numeric">Price</th><th scope="col" className="portfolio-numeric">Fees</th><th scope="col">Order</th></tr></thead>
      <tbody>{trade.fills.map(fill => <tr key={`${fill.exec_id}-${fill.role}`}>
        <td>{formatTime(fill.exec_time, "—")}</td><td>{roleLabel[fill.role]}</td>
        <td><span className={`portfolio-side${fill.side === "BUY" ? " portfolio-side--buy" : ""}`}>{fill.side}</span></td>
        <td className="portfolio-numeric"><Amount value={fill.quantity} quantity /></td><td className="portfolio-numeric"><Amount value={fill.price} /></td>
        <td className="portfolio-numeric">{fill.commission === null ? "—" : <><Amount value={fill.commission} /> <small>{fill.commission_ccy}</small></>}</td>
        <td className="portfolio-order-id">{fill.perm_id}</td>
      </tr>)}</tbody>
    </table>
    {trade.fills_truncated && <p className="portfolio-footnote">Showing the first 50 of {trade.fill_count} fills.</p>}
  </div>;
}

/** Open and closed trades with an expandable fill-by-fill detail per row. */
export function TradeTable({ rows, label, showAccount = true, dimension }: { rows: TradeRow[]; label: string; showAccount?: boolean; dimension?: Dimension }) {
  const [expanded, setExpanded] = useState("");
  const columns = showAccount ? 13 : 12;
  return <div className="portfolio-table-scroll ledger-scroll" tabIndex={0} role="region" aria-label={`${label} table`}><table className="ledger-table trade-table" aria-label={label}>
    <thead><tr>
      <th scope="col">Status</th><th scope="col">Ticker</th><th scope="col">Opened (IST)</th><th scope="col">Closed / held</th>
      <th scope="col" className="portfolio-numeric">Qty</th><th scope="col" className="portfolio-numeric">Avg entry</th><th scope="col" className="portfolio-numeric">Exit / mark</th>
      <th scope="col" className="portfolio-numeric">Realized</th><th scope="col" className="portfolio-numeric">Unrealized</th><th scope="col" className="portfolio-numeric">Net</th><th scope="col" className="portfolio-numeric">Return</th>
      {showAccount && <th scope="col">Account</th>}<th scope="col">Trade</th>
    </tr></thead>
    <tbody>{rows.map(trade => {
      const open = trade.status === "open";
      const isExpanded = expanded === trade.key;
      return <Fragment key={trade.key}>
        <tr className={`ledger-row trade-row trade-row--${trade.status}`}>
          <td><button type="button" className="trade-toggle" aria-expanded={isExpanded} aria-label={`${isExpanded ? "Hide" : "Show"} ${trade.trading_symbol} ${trade.status} trade details`} onClick={() => setExpanded(isExpanded ? "" : trade.key)}>
            <PortfolioIcon name="chevron" /><span className={`portfolio-badge${open ? " portfolio-badge--green" : ""}`}>{open ? "Open" : "Closed"}</span></button>
            <small className={`trade-direction trade-direction--${trade.direction}`}>{trade.direction}</small></td>
          <td>{trade.listing_id ? <a href={tickerHref(trade.listing_id)}>{trade.trading_symbol}</a> : trade.trading_symbol}{trade.contract_id && <small> {trade.product_type}</small>}
            {trade.issues.some(issue => issue !== "history_unverified") && <span className="portfolio-stale trade-issue" title={trade.issues.map(issue => issueLabels[issue] ?? issue).join(" · ")}> ⚠</span>}
            <BucketLabel row={trade} dimension={dimension} /></td>
          <td>{formatTime(trade.opened_at, "—")}</td>
          <td>{open ? <span className="portfolio-footnote">Open · {holding(trade.opened_at, null)}</span> : <>{formatTime(trade.closed_at, "—")} <small>{holding(trade.opened_at, trade.closed_at)}</small></>}</td>
          <td className="portfolio-numeric"><Amount value={open ? trade.quantity : trade.max_quantity} quantity /></td>
          <td className="portfolio-numeric"><Amount value={trade.avg_entry_price} /> <small>{trade.currency}</small></td>
          <td className="portfolio-numeric">{open ? <Amount value={trade.market_price} /> : <Amount value={trade.avg_exit_price} />}</td>
          <td className="portfolio-numeric"><Amount value={trade.realized} signed /></td>
          <td className="portfolio-numeric">{open ? <Amount value={trade.unrealized} signed /> : <span className="portfolio-footnote">—</span>}</td>
          <td className="portfolio-numeric portfolio-total"><Amount value={trade.net} signed /></td>
          <td className="portfolio-numeric"><ReturnPct value={trade.return_pct} /></td>
          {showAccount && <td>{trade.broker_code.toUpperCase()} {trade.account_id}{trade.simulated && <span className="portfolio-badge portfolio-badge--amber">Simulated</span>}</td>}
          <td>{tagLabel(trade)}</td>
        </tr>
        {isExpanded && <tr className="ledger-detail"><td colSpan={columns}><TradeDetail trade={trade} /></td></tr>}
      </Fragment>;
    })}</tbody>
  </table></div>;
}

function Summary({ rows }: { rows: TradeSummary[] }) {
  if (!rows.length) return null;
  return <dl className="trade-summary" aria-label="Closed trade results">{rows.map(row => {
    const rate = row.valued ? row.wins / row.valued : null;
    return <div key={JSON.stringify([row.currency, row.account_mode, row.simulated])}>
      <dt>Closed · {row.currency}{row.account_mode && row.account_mode !== "live" ? ` · ${row.account_mode}` : ""}{row.simulated ? " · simulated" : ""}</dt>
      <dd><Amount value={row.net ?? row.realized} signed /></dd>
      <small>{row.closed} trades · {rate === null ? "win rate —" : `${Math.round(rate * 100)}% won`} ({row.wins}W / {row.losses}L){row.net === null ? " · realized, fees unknown" : " · net of fees"}</small>
    </div>;
  })}</dl>;
}

/** Per-currency figures, one line each; never added across currencies. */
function PerCurrency({ items }: { items: { key: string; currency: string; value: string | null; note?: string }[] }) {
  if (!items.length) return <span className="portfolio-footnote">—</span>;
  return <>{items.map(item => <div key={item.key}><Amount value={item.value} signed /> <small>{item.currency}{item.note}</small></div>)}</>;
}

const where = (row: { currency: string; account_mode: string; simulated: boolean }) => ({
  key: JSON.stringify([row.currency, row.account_mode, row.simulated]), currency: row.currency,
  note: `${row.account_mode && row.account_mode !== "live" ? ` · ${row.account_mode}` : ""}${row.simulated ? " · sim" : ""}`,
});

function tradeBucketCells(bucket: TradeBucket) {
  const valued = bucket.summary.reduce((total, row) => total + row.valued, 0);
  const wins = bucket.summary.reduce((total, row) => total + row.wins, 0);
  return <>
    <td className="portfolio-numeric">{bucket.counts.open}</td>
    <td className="portfolio-numeric">{bucket.counts.closed}</td>
    <td className="portfolio-numeric"><PerCurrency items={bucket.open.map(row => ({ ...where(row), value: row.unrealized }))} /></td>
    <td className="portfolio-numeric"><PerCurrency items={bucket.summary.map(row => ({ ...where(row), value: row.net ?? row.realized, note: `${where(row).note}${row.net === null ? " · fees unknown" : ""}` }))} /></td>
    <td className="portfolio-numeric">{valued ? `${Math.round((wins / valued) * 100)}%` : "—"}</td>
  </>;
}

/** The Trades page's open/closed view: every trade across the profile's accounts. */
export function OpenClosedTrades({ views }: { views?: ReactNode }) {
  const [params, update] = useUrlParams();
  const profiles = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const requested = params.get("user_id") ?? "";
  const userId = profiles.data?.items.find(p => p.user_id === requested)?.user_id
    ?? profiles.data?.items.find(p => p.active)?.user_id ?? profiles.data?.items[0]?.user_id ?? "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const status = (["open", "closed"] as const).find(value => value === params.get("status")) ?? "";
  const query = new URLSearchParams({ user_id: userId, limit: String(PAGE), offset: String(offset) });
  if (status) query.set("status", status);
  const account = params.get("account");
  if (account) {
    try { const [broker, id] = JSON.parse(account) as [string, string]; query.set("broker_code", broker); query.set("account_id", id); } catch { /* ignore a malformed bookmark */ }
  }
  for (const key of ["symbol", "theme_id", "strategy_id", "trade_name_id"] as const) if (params.get(key)) query.set(key, params.get(key)!);
  if (params.get("start")) query.set("start", `${params.get("start")}T00:00:00Z`);
  if (params.get("end")) query.set("end", `${params.get("end")}T23:59:59Z`);
  const pivot = useTradePivot();
  for (const [key, value] of Object.entries(pivot.query)) query.set(key, value);
  const suffix = `?user_id=${encodeURIComponent(userId)}`;
  const trades = useRemote<TradesPage>(userId ? `${API}/trades?${query}` : null, 60_000);
  const themes = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/themes${suffix}` : null, 60_000);
  const strategies = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/strategies${suffix}` : null, 60_000);
  const names = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/trade-names${suffix}` : null, 60_000);
  const catalogs: Record<CatalogKind, CatalogEntry[]> = { themes: themes.data?.items ?? [], strategies: strategies.data?.items ?? [], "trade-names": names.data?.items ?? [] };
  const set = (values: Record<string, string>) => update(p => {
    for (const [k, v] of Object.entries(values)) { if (v) p.set(k, v); else p.delete(k); }
    if (!("offset" in values)) p.delete("offset");
  }, true);
  const counts = trades.data?.counts;
  const filtering = filterKeys.some(key => params.get(key));
  const rows = trades.data?.items ?? [];
  return <main className="portfolio-page trade-ledger">
    <PortfolioHeader title="Trades" description="Each position from first fill to flat: entry and exit, holding period, and what it made.">
      {views}
      <ProfileSelect profiles={profiles.data?.items ?? []} value={userId} disabled={profiles.loading && !profiles.data} onChange={value => update(p => { p.set("user_id", value); p.delete("account"); p.delete("offset"); }, true)} />
    </PortfolioHeader>
    {Boolean(profiles.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(profiles.error)}</p>}
    {!userId ? (profiles.loading ? <p className="portfolio-empty-inline" role="status">Loading profiles…</p> : <EmptyState title="No profile available">Register a profile to see its trades.</EmptyState>) : <section className="portfolio-section">
      <div className="ledger-chips" role="group" aria-label="Trade status">
        {(["", "open", "closed"] as const).map(value => <button key={value || "all"} type="button" aria-pressed={status === value}
          onClick={() => set({ status: value })}>{value === "open" ? "Open" : value === "closed" ? "Closed" : "All"}{counts && <span>{value ? counts[value] : counts.open + counts.closed}</span>}</button>)}
      </div>
      {trades.data && <Summary rows={trades.data.summary} />}
      <div className="ledger-filters">
        <label className="portfolio-selector"><span>Account</span><select value={account ?? ""} onChange={e => set({ account: e.target.value })}>
          <option value="">All accounts</option>
          {(trades.data?.accounts ?? []).map(a => { const value = JSON.stringify([a.broker_code, a.account_id]); return <option key={value} value={value}>{a.broker_code.toUpperCase()} {a.account_id}{a.simulated ? " (simulated)" : a.account_mode ? ` · ${a.account_mode}` : ""}</option>; })}
        </select></label>
        <label className="portfolio-selector"><span>Ticker</span><input type="search" placeholder="Symbol" value={params.get("symbol") ?? ""} onChange={e => set({ symbol: e.target.value })} /></label>
        {kinds.map(kind => { const field = kind === "themes" ? "theme_id" : kind === "strategies" ? "strategy_id" : "trade_name_id"; return <label key={kind} className="portfolio-selector"><span>{singular[kind]}</span>
          <select value={params.get(field) ?? ""} onChange={e => set({ [field]: e.target.value })}><option value="">Any {singular[kind]}</option>{catalogs[kind].map(entry => <option key={entry.id} value={entry.id}>{entry.name}{entry.active ? "" : " (archived)"}</option>)}</select></label>; })}
        <label className="portfolio-selector"><span>Active from</span><input type="date" value={params.get("start") ?? ""} onChange={e => set({ start: e.target.value })} /></label>
        <label className="portfolio-selector"><span>Through</span><input type="date" value={params.get("end") ?? ""} onChange={e => set({ end: e.target.value })} /></label>
        <TradePivotControl pivot={pivot} />
        {filtering && <button type="button" className="portfolio-button portfolio-button--ghost" onClick={() => set(Object.fromEntries(filterKeys.map(k => [k, ""])))}>Clear filters</button>}
      </div>
      {trades.data && pivot.dimension && <BucketTable pivot={pivot} caption="Trades" buckets={trades.data.pivots?.[pivot.dimension.id]}
        failed={Boolean(trades.data.unavailable_dimensions?.includes(pivot.dimension.id))}
        headers={["Open", "Closed", "Unrealized (open)", "Closed result", "Win rate"]} cells={tradeBucketCells} />}
      {Boolean(trades.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(trades.error, "Trades could not be loaded.")}</p>}
      {trades.loading && !trades.data && <p className="portfolio-empty-inline" role="status">Loading trades…</p>}
      {trades.data && <>
        <p className="portfolio-list-meta">{trades.data.total.toLocaleString()} {status || ""} trades{pivot.dimension && pivot.bucket !== undefined ? ` in ${trades.data.pivots?.[pivot.dimension.id]?.find(bucket => bucket.id === pivot.bucket)?.label ?? "the chosen " + pivot.dimension.label.toLowerCase()}` : ""}{filtering ? " match these filters" : ""} · latest activity first</p>
        {rows.length > 0 && <TradeTable rows={rows} label="Open and closed trades" dimension={pivot.dimension} />}
        {!rows.length && <EmptyState title={filtering || status ? "No trades match these filters" : "No trades yet"}>{filtering || status ? "Try another status, account, ticker, tag or date range." : "Trades appear here once orders fill."}</EmptyState>}
        {trades.data.total > PAGE && <div className="portfolio-pagination">
          <button className="portfolio-button portfolio-button--secondary" disabled={offset === 0} onClick={() => set({ offset: String(Math.max(0, offset - PAGE)) })}>Previous</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, trades.data.total)} of {trades.data.total.toLocaleString()}</span>
          <button className="portfolio-button portfolio-button--secondary" disabled={offset + PAGE >= trades.data.total} onClick={() => set({ offset: String(offset + PAGE) })}>Next</button>
        </div>}
        <p className="portfolio-footnote">A trade runs from the first fill on a flat position until it is flat again, per account, instrument and currency; a fill that flips the position closes one trade and opens the next. Realized is FIFO within the trade, before fees; net subtracts fees in the trade's currency. Return is realized + unrealized over the cost of everything bought (or sold short). Tags are the opening order's. Missing history, marks or fees show as —, never zero.</p>
      </>}
    </section>}
  </main>;
}

/** The Trades page: the order ledger or open/closed trades, switched by ``?view=``. */
export function TradesPage() {
  const [params, update] = useUrlParams();
  const view: View = params.get("view") === "positions" ? "positions" : "orders";
  const views = <Segmented<View> label="Trades view" value={view} options={[{ id: "orders", label: "Orders" }, { id: "positions", label: "Open & closed" }]}
    onChange={next => update(p => { if (next === "positions") p.set("view", next); else p.delete("view"); p.delete("offset"); }, true)} />;
  return view === "positions" ? <OpenClosedTrades key="positions" views={views} /> : <TradeLedger key="orders" views={views} />;
}

/** Overview panel: the selected account's open or recently closed trades. */
export function AccountTrades({ account, revision = 0, dimension }: { account: BrokerAccount; revision?: number; dimension?: Dimension }) {
  const [status, setStatus] = useState<TradeStatus>("open");
  const userId = account.user_id;
  const query = userId ? new URLSearchParams({ user_id: userId, broker_code: account.broker_code, account_id: account.account_id, status, limit: "10", ...(dimension ? { dims: dimension.id } : {}) }) : null;
  const trades = useRemote<TradesPage>(query ? `${API}/trades?${query}` : null, 60_000);
  usePortfolioRefresh(revision, trades.refresh);
  // A response without status counts is not a trades page: show it as unavailable, never guess.
  const data = trades.data?.counts && Array.isArray(trades.data.items) ? trades.data : null;
  const counts = data?.counts;
  const all = userId ? `/portfolio/trades?${new URLSearchParams({ user_id: userId, view: "positions", status, account: JSON.stringify([account.broker_code, account.account_id]), ...(dimension ? { pivot: dimension.id } : {}) })}` : "";
  return <section className="portfolio-section trade-ledger account-trades" aria-label="Open and closed trades">
    <div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Trade history</span><h3>Open & closed trades</h3></div>
      <Segmented<TradeStatus> label="Trade status" value={status} onChange={setStatus}
        options={[{ id: "open", label: `Open${counts ? ` ${counts.open}` : ""}` }, { id: "closed", label: `Closed${counts ? ` ${counts.closed}` : ""}` }]} /></div>
    {!userId ? <EmptyState title="Assign this account to a profile">Trades are listed per profile.</EmptyState> : <>
      {Boolean(trades.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(trades.error, "Trades could not be loaded.")}</p>}
      {trades.loading && !trades.data ? <p className="portfolio-empty-inline" role="status">Loading trades…</p> : !data ? (trades.data && <p className="portfolio-empty-inline">Trades unavailable.</p>) : <>
        {status === "closed" && <Summary rows={data.summary ?? []} />}
        {data.items.length ? <TradeTable rows={data.items} label={`${status === "open" ? "Open" : "Closed"} trades`} showAccount={false} dimension={dimension} />
          : <p className="portfolio-empty-inline">{status === "open" ? "No open trades in recorded history." : "No closed trades yet."}</p>}
        <a className="portfolio-text-link" href={all}>{data.total > data.items.length ? `View all ${data.total} ${status} trades` : "Open the trades view"}<PortfolioIcon name="arrow" /></a>
      </>}
    </>}
  </section>;
}
