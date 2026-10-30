import { Fragment, useState } from "react";

import type { CatalogEntry, CatalogKind, ItemsPage, LedgerPage, LedgerRow, LedgerTagStatus, OrderAssignment, Profile } from "../portfolioTypes";
import { errorMessage, fetchJson, useRemote } from "../shared/api";
import { SignedBars } from "../shared/charts";
import { formatTime } from "../shared/format";
import { compactMoney, toNumber } from "../shared/market";
import { tickerHref, useUrlParams } from "../shared/urlState";
import { PortfolioTabs, TagSelectors, hasTags, kinds, singular } from "./PortfolioBook";
import { Amount, EmptyState, PortfolioHeader, PortfolioIcon, ProfileSelect } from "./PortfolioUI";
import "./Portfolio.css";
import "./TradeLedger.css";

const API = "/hub/api/v1/portfolio";
const PAGE = 50;
const MAX_SELECTION = 200;
const EMPTY: OrderAssignment = { theme_id: null, strategy_id: null, trade_name_id: null };
const statusLabel: Record<LedgerTagStatus, string> = { tagged: "Tagged", untagged: "Needs tags", not_taggable: "Not taggable" };
const filterKeys = ["account", "listing_id", "symbol", "theme_id", "strategy_id", "trade_name_id", "tag", "start", "end"] as const;

type Result = { key: string; ok: boolean; message: string };

/** Run ``task`` over ``items`` with at most ``limit`` in flight; each failure is reported, not thrown. */
export async function runPool<T>(items: T[], limit: number, task: (item: T) => Promise<void>) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) await task(items[next++]);
  }));
}

function assign(row: LedgerRow, userId: string, tags: OrderAssignment) {
  return fetchJson(`${API}/accounts/${encodeURIComponent(row.broker_code)}/${encodeURIComponent(row.account_id)}/orders/${encodeURIComponent(row.perm_id)}/assignment?user_id=${encodeURIComponent(userId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(tags),
  });
}

function InlineEditor({ row, userId, catalogs, done, onCreated }: {
  row: LedgerRow; userId: string; catalogs: Record<CatalogKind, CatalogEntry[]>; done: (message: string) => void; onCreated: (kind: CatalogKind) => void;
}) {
  const [tags, setTags] = useState<OrderAssignment>({ theme_id: row.theme_id, strategy_id: row.strategy_id, trade_name_id: row.trade_name_id });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(value: OrderAssignment, verb: string) {
    setBusy(true); setError("");
    try { await assign(row, userId, value); done(`${verb} for ${row.trading_symbol} order ${row.perm_id}.`); }
    catch (caught) { setError(errorMessage(caught, "Could not save order tags.")); }
    finally { setBusy(false); }
  }
  return <form className="ledger-editor" onSubmit={event => { event.preventDefault(); void save(tags, "Tags saved"); }}>
    <TagSelectors tags={tags} onChange={setTags} catalogs={catalogs} disabled={busy} name={`order ${row.perm_id}`} userId={userId} onCreated={onCreated} />
    <div className="portfolio-actions">
      <button className="portfolio-button portfolio-button--primary" disabled={busy || !hasTags(tags)}>{busy ? "Saving…" : "Save tags"}</button>
      {row.tag_status === "tagged" && <button type="button" className="portfolio-button portfolio-button--ghost" disabled={busy} onClick={() => void save(EMPTY, "Tags cleared")}>Clear tags</button>}
    </div>
    {error && <p className="portfolio-notice portfolio-notice--error" role="alert">{error}</p>}
  </form>;
}

/** Tag coverage across full history (API counts) and broker-realized PnL by fill day for the loaded page. */
function LedgerPulse({ counts, rows }: { counts: LedgerPage["counts"]; rows: LedgerRow[] }) {
  const total = counts.tagged + counts.untagged + counts.not_taggable;
  const realized = new Map<string, number>();
  const currency = rows.find(row => toNumber(row.broker_realized_pnl) !== null)?.currency;
  for (const row of rows) {
    const value = toNumber(row.broker_realized_pnl);
    if (value === null || row.currency !== currency) continue;
    const day = row.last_fill_at.slice(0, 10);
    realized.set(day, (realized.get(day) ?? 0) + value);
  }
  const days = [...realized.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-10);
  return <div className="ledger-pulse">
    <figure>
      <figcaption>Tag coverage · all matching history</figcaption>
      <div className="ledger-meter" role="img" aria-label={`${counts.tagged} tagged, ${counts.untagged} need tags, ${counts.not_taggable} not taggable`}>
        {total > 0 && (["tagged", "untagged", "not_taggable"] as const).map(status => counts[status] > 0 &&
          <i key={status} className={`ledger-meter__${status}`} style={{ flexGrow: counts[status] }} title={`${statusLabel[status]}: ${counts[status]}`} />)}
      </div>
      <p>{total ? `${Math.round((counts.tagged / total) * 100)}% tagged` : "No orders"} · {counts.untagged.toLocaleString()} need tags</p>
    </figure>
    {days.length > 0 && <figure>
      <figcaption>Broker-realized PnL by fill day · this page · {currency}</figcaption>
      <SignedBars label={`Broker-realized PnL by fill day, ${currency}`} bars={days.map(([day, value]) => ({ key: day, label: day.slice(5), value, display: compactMoney(value) }))} />
    </figure>}
  </div>;
}

export function TradeLedger() {
  const [params, update] = useUrlParams();
  const profiles = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const requested = params.get("user_id") ?? "";
  const userId = profiles.data?.items.find(p => p.user_id === requested)?.user_id
    ?? profiles.data?.items.find(p => p.active)?.user_id ?? profiles.data?.items[0]?.user_id ?? "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const query = new URLSearchParams({ user_id: userId, limit: String(PAGE), offset: String(offset) });
  const account = params.get("account");
  if (account) {
    try { const [broker, id] = JSON.parse(account) as [string, string]; query.set("broker_code", broker); query.set("account_id", id); } catch { /* ignore a malformed bookmark */ }
  }
  for (const key of ["listing_id", "symbol", "theme_id", "strategy_id", "trade_name_id", "tag"] as const) if (params.get(key)) query.set(key, params.get(key)!);
  if (params.get("start")) query.set("start", `${params.get("start")}T00:00:00Z`);
  if (params.get("end")) query.set("end", `${params.get("end")}T23:59:59Z`);
  const suffix = `?user_id=${encodeURIComponent(userId)}`;
  const ledger = useRemote<LedgerPage>(userId ? `${API}/ledger?${query}` : null, 60_000);
  const themes = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/themes${suffix}` : null, 60_000);
  const strategies = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/strategies${suffix}` : null, 60_000);
  const names = useRemote<ItemsPage<CatalogEntry>>(userId ? `${API}/catalogs/trade-names${suffix}` : null, 60_000);
  const catalogs: Record<CatalogKind, CatalogEntry[]> = { themes: themes.data?.items ?? [], strategies: strategies.data?.items ?? [], "trade-names": names.data?.items ?? [] };
  const refreshCatalog = (kind: CatalogKind) => ({ themes, strategies, "trade-names": names })[kind].refresh();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState("");
  const [expanded, setExpanded] = useState("");
  const [bulk, setBulk] = useState<OrderAssignment>(EMPTY);
  const [running, setRunning] = useState<{ done: number; total: number } | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [notice, setNotice] = useState("");
  const rows = ledger.data?.items ?? [];
  const byKey = new Map(rows.map(row => [row.key, row]));
  const chosen = [...selected].map(key => byKey.get(key)).filter((row): row is LedgerRow => Boolean(row));
  const set = (values: Record<string, string>) => update(p => {
    for (const [k, v] of Object.entries(values)) { if (v) p.set(k, v); else p.delete(k); }
    if (!("offset" in values)) p.delete("offset");
  }, true);
  const refresh = (message: string) => { setNotice(message); setEditing(""); ledger.refresh(); };

  async function applyBulk(tags: OrderAssignment) {
    const targets = chosen.filter(row => row.taggable).slice(0, MAX_SELECTION);
    setResults([]); setNotice(""); setRunning({ done: 0, total: targets.length });
    const outcome: Result[] = [];
    await runPool(targets, 2, async row => {
      try { await assign(row, userId, tags); outcome.push({ key: row.key, ok: true, message: "" }); }
      catch (caught) { outcome.push({ key: row.key, ok: false, message: `${row.trading_symbol} ${row.perm_id}: ${errorMessage(caught, "could not save")}` }); }
      setRunning(state => state && { ...state, done: state.done + 1 });
    });
    setRunning(null);
    const failed = outcome.filter(r => !r.ok);
    setResults(failed);
    setSelected(new Set(failed.map(r => r.key)));
    setNotice(`${outcome.length - failed.length} of ${outcome.length} orders ${hasTags(tags) ? "tagged" : "cleared"}${failed.length ? `; ${failed.length} failed` : ""}.`);
    ledger.refresh();
  }

  const taggableOnPage = rows.filter(row => row.taggable);
  const allOnPage = taggableOnPage.length > 0 && taggableOnPage.every(row => selected.has(row.key));
  const counts = ledger.data?.counts;
  const filtering = filterKeys.some(key => params.get(key));
  return <main className="portfolio-page trade-ledger">
    <PortfolioHeader title="Trades" description="Every executed order across your accounts, with the theme, strategy and trade name it belongs to.">
      <ProfileSelect profiles={profiles.data?.items ?? []} value={userId} disabled={profiles.loading && !profiles.data} onChange={value => { setSelected(new Set()); update(p => { p.set("user_id", value); p.delete("account"); p.delete("offset"); }, true); }} />
    </PortfolioHeader>
    <PortfolioTabs active="trades" userId={userId} account={account ?? ""} />
    {Boolean(profiles.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(profiles.error)}</p>}
    {!userId ? (profiles.loading ? <p className="portfolio-empty-inline" role="status">Loading profiles…</p> : <EmptyState title="No profile available">Register a profile to see and tag its trades.</EmptyState>) : <section className="portfolio-section">
      <div className="ledger-chips" role="group" aria-label="Tag status">
        {(["", "untagged", "tagged", "not_taggable"] as const).map(status => <button key={status || "all"} type="button" aria-pressed={(params.get("tag") ?? "") === status}
          onClick={() => set({ tag: status })}>{status ? statusLabel[status] : "All"}{counts && <span>{status ? counts[status] : counts.tagged + counts.untagged + counts.not_taggable}</span>}</button>)}
      </div>
      {counts && <LedgerPulse counts={counts} rows={rows} />}
      <div className="ledger-filters">
        <label className="portfolio-selector"><span>Account</span><select value={account ?? ""} onChange={e => set({ account: e.target.value })}>
          <option value="">All accounts</option>
          {(ledger.data?.accounts ?? []).map(a => { const value = JSON.stringify([a.broker_code, a.account_id]); return <option key={value} value={value}>{a.broker_code.toUpperCase()} {a.account_id}{a.simulated ? " (simulated)" : a.account_mode ? ` · ${a.account_mode}` : ""}</option>; })}
        </select></label>
        <label className="portfolio-selector"><span>Ticker</span><input type="search" placeholder="Symbol" value={params.get("symbol") ?? ""} onChange={e => set({ symbol: e.target.value })} /></label>
        {kinds.map(kind => { const field = kind === "themes" ? "theme_id" : kind === "strategies" ? "strategy_id" : "trade_name_id"; return <label key={kind} className="portfolio-selector"><span>{singular[kind]}</span>
          <select value={params.get(field) ?? ""} onChange={e => set({ [field]: e.target.value })}><option value="">Any {singular[kind]}</option>{catalogs[kind].map(entry => <option key={entry.id} value={entry.id}>{entry.name}{entry.active ? "" : " (archived)"}</option>)}</select></label>; })}
        <label className="portfolio-selector"><span>From</span><input type="date" value={params.get("start") ?? ""} onChange={e => set({ start: e.target.value })} /></label>
        <label className="portfolio-selector"><span>Through</span><input type="date" value={params.get("end") ?? ""} onChange={e => set({ end: e.target.value })} /></label>
        {filtering && <button type="button" className="portfolio-button portfolio-button--ghost" onClick={() => set(Object.fromEntries(filterKeys.map(k => [k, ""])))}>Clear filters</button>}
      </div>
      {params.get("listing_id") && <p className="portfolio-footnote">Showing trades for one ticker, including security-level fills and derivatives on it. <a href={tickerHref(params.get("listing_id")!)}>Open the ticker →</a></p>}
      {chosen.length > 0 && <div className="ledger-bulk" role="region" aria-label="Tag selected orders">
        <strong>{chosen.length} selected</strong>
        <TagSelectors tags={bulk} onChange={setBulk} catalogs={catalogs} disabled={Boolean(running)} name="selected orders" userId={userId} onCreated={refreshCatalog} />
        <div className="portfolio-actions">
          <button type="button" className="portfolio-button portfolio-button--primary" disabled={Boolean(running) || !hasTags(bulk)} onClick={() => void applyBulk(bulk)}>Apply to {chosen.length} orders</button>
          <button type="button" className="portfolio-button portfolio-button--secondary" disabled={Boolean(running)} onClick={() => void applyBulk(EMPTY)}>Clear tags</button>
          <button type="button" className="portfolio-button portfolio-button--ghost" disabled={Boolean(running)} onClick={() => setSelected(new Set())}>Deselect</button>
        </div>
        {running && <p role="status">Saving {running.done} of {running.total}…</p>}
      </div>}
      {notice && <p className="portfolio-notice portfolio-notice--success" role="status">{notice}</p>}
      {results.length > 0 && <ul className="portfolio-notice portfolio-notice--error" role="alert" aria-label="Orders that failed">{results.map(r => <li key={r.key}>{r.message}</li>)}</ul>}
      {Boolean(ledger.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(ledger.error, "Trades could not be loaded.")}</p>}
      {ledger.loading && !ledger.data && <p className="portfolio-empty-inline" role="status">Loading trades…</p>}
      {ledger.data && <>
        <p className="portfolio-list-meta">{ledger.data.total.toLocaleString()} orders{filtering ? " match these filters" : ""} · newest first</p>
        <div className="portfolio-table-scroll ledger-scroll"><table className="ledger-table" aria-label="Trades">
          <thead><tr>
            <th scope="col"><input type="checkbox" aria-label="Select all taggable orders on this page" checked={allOnPage} disabled={!taggableOnPage.length}
              onChange={e => setSelected(e.target.checked ? new Set([...selected, ...taggableOnPage.map(r => r.key)].slice(0, MAX_SELECTION)) : new Set([...selected].filter(k => !taggableOnPage.some(r => r.key === k))))} /></th>
            <th scope="col">Last fill (IST)</th><th scope="col">Ticker</th><th scope="col">Side</th><th scope="col" className="portfolio-numeric">Qty</th><th scope="col" className="portfolio-numeric">Avg price</th>
            <th scope="col">Account</th><th scope="col">Theme</th><th scope="col">Strategy</th><th scope="col">Trade name</th><th scope="col" className="portfolio-numeric">Broker realized</th><th scope="col"><span className="sr-only">Actions</span></th>
          </tr></thead>
          <tbody>{rows.map(row => <Fragment key={row.key}>
            <tr className={`ledger-row ledger-row--${row.tag_status}`}>
              <td><input type="checkbox" aria-label={`Select ${row.trading_symbol} order ${row.perm_id}`} disabled={!row.taggable || (!selected.has(row.key) && selected.size >= MAX_SELECTION)} checked={selected.has(row.key)}
                onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(row.key); else next.delete(row.key); setSelected(next); }} /></td>
              <td>{formatTime(row.last_fill_at, "—")}</td>
              <td>{row.listing_id ? <a href={tickerHref(row.listing_id)}>{row.trading_symbol}</a> : row.trading_symbol}
                {row.contract_id && <small> {row.product_type}</small>}</td>
              <td><span className={`portfolio-side${row.side === "BUY" ? " portfolio-side--buy" : ""}`}>{row.side}</span></td>
              <td className="portfolio-numeric"><Amount value={row.quantity} quantity /></td>
              <td className="portfolio-numeric"><Amount value={row.avg_price} /> <small>{row.currency}</small></td>
              <td>{row.broker_code.toUpperCase()} {row.account_id}<span className={`portfolio-badge${row.simulated ? " portfolio-badge--amber" : ""}`}>{row.simulated ? "Simulated" : row.account_mode}</span></td>
              {row.tag_status === "tagged"
                ? <><td>{row.theme_name ?? "Archived entry"}</td><td>{row.strategy_name ?? "Archived entry"}</td><td>{row.trade_name_name ?? "Archived entry"}</td></>
                : <td colSpan={3}><span className={`portfolio-badge${row.taggable ? " portfolio-badge--amber" : ""}`}>{statusLabel[row.tag_status]}</span></td>}
              <td className="portfolio-numeric">{row.broker_realized_pnl === null ? "—" : <><Amount value={row.broker_realized_pnl} signed /> <small>{row.currency}</small></>}</td>
              <td className="ledger-actions">
                {row.taggable && <button type="button" className="portfolio-button portfolio-button--ghost" aria-expanded={editing === row.key} onClick={() => setEditing(editing === row.key ? "" : row.key)}>{row.tag_status === "tagged" ? "Edit tags" : "Tag"}</button>}
                <button type="button" className="portfolio-button portfolio-button--ghost" aria-expanded={expanded === row.key} aria-label={`${row.fill_count} fills for order ${row.perm_id}`} onClick={() => setExpanded(expanded === row.key ? "" : row.key)}>{row.fill_count} {row.fill_count === 1 ? "fill" : "fills"}<PortfolioIcon name="chevron" /></button>
              </td>
            </tr>
            {editing === row.key && <tr className="ledger-detail"><td colSpan={12}><InlineEditor row={row} userId={userId} catalogs={catalogs} done={refresh} onCreated={refreshCatalog} /></td></tr>}
            {expanded === row.key && <tr className="ledger-detail"><td colSpan={12}>
              <p className="portfolio-footnote">Order {row.perm_id} · first fill {formatTime(row.first_fill_at, "—")} · commissions {row.commissions.length ? row.commissions.map(c => `${c.amount} ${c.currency}`).join(", ") : "not reported"}{row.fills_truncated ? " · showing the first 50 fills" : ""}</p>
              <table aria-label={`Fills for order ${row.perm_id}`}><thead><tr><th scope="col">Execution</th><th scope="col">Time (IST)</th><th scope="col" className="portfolio-numeric">Qty</th><th scope="col" className="portfolio-numeric">Price</th></tr></thead>
                <tbody>{row.fills.map(fill => <tr key={String(fill.exec_id)}><td className="portfolio-order-id">{fill.exec_id}</td><td>{formatTime(String(fill.exec_time ?? ""), "—")}</td><td className="portfolio-numeric"><Amount value={fill.quantity} quantity /></td><td className="portfolio-numeric"><Amount value={fill.price} /></td></tr>)}</tbody></table>
            </td></tr>}
          </Fragment>)}</tbody>
        </table></div>
        {!rows.length && <EmptyState title={filtering ? "No trades match these filters" : "No trades yet"}>{filtering ? "Try another account, ticker, tag or date range." : "Executed orders appear here after the next broker snapshot."}</EmptyState>}
        {ledger.data.total > PAGE && <div className="portfolio-pagination">
          <button className="portfolio-button portfolio-button--secondary" disabled={offset === 0} onClick={() => set({ offset: String(Math.max(0, offset - PAGE)) })}>Previous</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, ledger.data.total)} of {ledger.data.total.toLocaleString()}</span>
          <button className="portfolio-button portfolio-button--secondary" disabled={offset + PAGE >= ledger.data.total} onClick={() => set({ offset: String(offset + PAGE) })}>Next</button>
        </div>}
        <p className="portfolio-footnote">Tags apply to the order, so every current and future fill inherits them. Broker realized PnL is as reported, in the order's currency; tagged FIFO PnL is on the Themes, Strategies and Trade names screens.</p>
      </>}
    </section>}
  </main>;
}
