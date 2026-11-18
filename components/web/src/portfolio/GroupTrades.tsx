import { forwardRef, useState } from "react";

import type { BrokerAccount, CatalogEntry, CatalogKind, TradesPage } from "./types";
import { Segmented } from "../components/ui";
import { errorMessage, useRemote } from "../shared/api";
import { EmptyState, PortfolioIcon } from "./PortfolioUI";
import { Summary, TradeTable } from "./TradeHistory";
import { ytdStart } from "./pnlSplit";
import "./TradeLedger.css";
import "./TradeHistory.css";

const API = "/hub/api/v1/portfolio";
const PAGE = 20;
const field = { themes: "theme_id", strategies: "strategy_id", "trade-names": "trade_name_id" } as const;
const noun = { themes: "theme", strategies: "strategy", "trade-names": "trade name" } as const;
type Status = "open" | "closed" | "all";

/**
 * The Themes / Strategies / Trade names page's open and closed trades: the same round trips
 * as the Trades page, scoped to the report's account and period and filtered by one tag.
 * The PnL table's "Trades" buttons pick the tag; the list links on to the full Trades view.
 */
export const GroupTrades = forwardRef<HTMLElement, {
  kind: CatalogKind; userId: string; account: BrokerAccount | undefined; ytd: boolean;
  entries: CatalogEntry[]; tag: string; onTag: (id: string) => void;
}>(function GroupTrades({ kind, userId, account, ytd, entries, tag, onTag }, ref) {
  const [status, setStatus] = useState<Status>("all");
  const [limit, setLimit] = useState(PAGE);
  const query = new URLSearchParams({ user_id: userId, limit: String(limit) });
  if (status !== "all") query.set("status", status);
  if (account) { query.set("broker_code", account.broker_code); query.set("account_id", account.account_id); }
  if (tag) query.set(field[kind], tag);
  if (ytd) query.set("start", `${ytdStart()}T00:00:00Z`);
  const trades = useRemote<TradesPage>(`${API}/trades?${query}`, 60_000);
  const data = trades.data?.counts && Array.isArray(trades.data.items) ? trades.data : null;
  const counts = data?.counts;
  const chosen = entries.find(entry => entry.id === tag);
  const scope = chosen ? chosen.name : `every ${noun[kind]}`;
  const full = `/portfolio/trades?${new URLSearchParams({
    user_id: userId, view: "positions", ...(status !== "all" ? { status } : {}), ...(tag ? { [field[kind]]: tag } : {}),
    ...(account ? { account: JSON.stringify([account.broker_code, account.account_id]) } : {}), ...(ytd ? { start: ytdStart() } : {}),
  })}`;
  const pick = (next: Status) => { setStatus(next); setLimit(PAGE); };
  return <section ref={ref} className="portfolio-group-trades trade-ledger" aria-label="Open and closed trades" tabIndex={-1}>
    <div className="portfolio-section-heading"><div><span className="portfolio-eyebrow">Trade history{ytd ? ` · active since 1 Jan ${ytdStart().slice(0, 4)}` : ""}</span>
      <h3>Open & closed trades <span className="portfolio-group-trades__scope">in {scope}</span></h3></div>
      <div className="portfolio-group-trades__tools">
        <label className="portfolio-selector"><span className="sr-only">Filter trades by {noun[kind]}</span>
          <select aria-label={`Filter trades by ${noun[kind]}`} value={tag} onChange={event => { onTag(event.target.value); setLimit(PAGE); }}>
            <option value="">All {noun[kind]}s</option>
            {entries.map(entry => <option key={entry.id} value={entry.id}>{entry.name}{entry.active ? "" : " (archived)"}</option>)}
          </select></label>
        <Segmented<Status> label="Trade status" value={status} onChange={pick} options={[
          { id: "all", label: `All${counts ? ` ${counts.open + counts.closed}` : ""}` },
          { id: "open", label: `Open${counts ? ` ${counts.open}` : ""}` },
          { id: "closed", label: `Closed${counts ? ` ${counts.closed}` : ""}` }]} />
      </div></div>
    {Boolean(trades.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(trades.error, "Trades could not be loaded.")}</p>}
    {trades.loading && !data ? <p className="portfolio-empty-inline" role="status">Loading trades…</p> : !data ? (trades.data && <p className="portfolio-empty-inline">Trades unavailable.</p>) : <>
      {status !== "open" && <Summary rows={data.summary ?? []} />}
      {data.items.length ? <TradeTable rows={data.items} label={`${status === "all" ? "Open and closed" : status === "open" ? "Open" : "Closed"} trades in ${scope}`} showAccount={!account} />
        : <EmptyState title={tag || status !== "all" ? "No trades match" : "No trades yet"}>{tag ? `No ${status === "all" ? "" : `${status} `}trades are tagged ${chosen?.name ?? `with this ${noun[kind]}`}${ytd ? " this year" : ""}.` : `Trades appear here once orders fill${ytd ? " this year" : ""}.`}</EmptyState>}
      <div className="portfolio-group-trades__foot">
        <span className="portfolio-footnote">{data.items.length < data.total ? `Showing ${data.items.length} of ${data.total.toLocaleString()}` : `${data.total.toLocaleString()} ${data.total === 1 ? "trade" : "trades"}`} · latest activity first · tags are the opening order's</span>
        {data.items.length < data.total && limit < 200 && <button type="button" className="portfolio-button portfolio-button--secondary" onClick={() => setLimit(Math.min(200, limit + PAGE * 2))} disabled={trades.loading}>{trades.loading ? "Loading…" : "Show more"}</button>}
        <a className="portfolio-text-link" href={full}>Open in Trades<PortfolioIcon name="arrow" /></a>
      </div>
    </>}
  </section>;
});
