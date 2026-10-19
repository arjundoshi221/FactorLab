import { errorMessage, useRemote } from "../shared/api";
import type { Instrument } from "../instrumentTypes";
import type { CellState, MatrixCell, MatrixColumn, MatrixPage } from "../matrixTypes";
import "./DataMatrix.css";

export const MATRIX_API = "/hub/api/v1/market-data/matrix";
const PAGE = 50;

export const stateLabel: Record<CellState, string> = {
  ok: "Up to date", stale: "Behind", missing: "Missing", stored: "Stored", none: "None",
  not_configured: "Not configured", needs_tags: "Needs tags", unavailable: "Unavailable",
};
const groupLabel: Record<MatrixColumn["group"], string> = {
  market: "Market data", fundamentals: "Fundamentals", alternative: "Alternative", broker: "Broker", tags: "Tags",
};
const short = (value: string | null) => value ? value.slice(0, 10) : "";

const ledgerColumn = (column: MatrixColumn) => column.key === "trades" || column.key === "tags";

/** Trades and tags open the ledger; every other cell's evidence is the ticker's dataset panel. */
export function cellHref(column: MatrixColumn, instrumentId: string) {
  return ledgerColumn(column)
    ? `/portfolio/trades?listing_id=${instrumentId}${column.key === "tags" ? "&tag=untagged" : ""}`
    : `#dataset-${column.dataset}`;
}

export function CellPill({ cell, column }: { cell: MatrixCell | undefined; column: MatrixColumn }) {
  if (!cell) return <span className="matrix-cell matrix-cell--unavailable" title="Not returned">?</span>;
  const date = short(cell.latest);
  const text = cell.state === "ok" || cell.state === "stale" || cell.state === "stored"
    ? date || stateLabel[cell.state]
    : cell.state === "needs_tags" ? `${cell.count ?? ""} untagged`
    : cell.state === "missing" ? "Missing"
    : cell.state === "unavailable" ? "?"
    : column.key === "trades" || column.key === "positions" || column.key === "political" || column.key === "fundamentals"
      ? (cell.count ? String(cell.count) : "—") : "—";
  const counted = cell.state === "ok" && cell.count && ["positions", "trades", "political", "fundamentals", "futures"].includes(column.key);
  return <span className={`matrix-cell matrix-cell--${cell.state}`} title={`${column.label}: ${stateLabel[cell.state]}${cell.detail ? ` · ${cell.detail}` : ""}${date ? ` · latest ${date}` : ""}`}>
    {counted ? `${cell.count} · ${date || "stored"}` : text}
  </span>;
}

export function DataMatrix({ params, update, select }: {
  params: URLSearchParams; update: (values: Record<string, string>) => void; select: (i: Instrument) => void;
}) {
  const query = new URLSearchParams();
  for (const key of ["search", "country", "scope", "attention"]) if (params.get(key)) query.set(key, params.get(key)!);
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  query.set("offset", String(offset)); query.set("limit", String(PAGE));
  const remote = useRemote<MatrixPage>(`${MATRIX_API}?${query}`);
  const data = remote.data;
  const groups = data ? data.columns.reduce<{ group: MatrixColumn["group"]; span: number }[]>((all, column) => {
    const last = all[all.length - 1];
    if (last && last.group === column.group) last.span += 1; else all.push({ group: column.group, span: 1 });
    return all;
  }, []) : [];
  const filter = (key: string, value: string) => update({ [key]: value, offset: "" });
  return <section className="data-matrix" aria-label="Data matrix">
    <p>Each FactorLab ticker against each dataset. Bars are judged against the exchange's latest completed session; holdings, trades and tags follow the account owner's profile. Select a ticker to see everything linked to it.</p>
    <div className="instrument-filters">
      <label>Search<input type="search" placeholder="Symbol, company or alias" value={params.get("search") ?? ""} onChange={e => filter("search", e.target.value)} /></label>
      <label>Country<select value={params.get("country") ?? ""} onChange={e => filter("country", e.target.value)}><option value="">All</option><option value="US">US</option><option value="IN">India</option></select></label>
      <label>Tickers<select value={params.get("scope") ?? "relevant"} onChange={e => filter("scope", e.target.value === "relevant" ? "" : e.target.value)}>
        <option value="relevant">Configured, traded or held</option><option value="configured">Configured for collection</option><option value="traded">Traded</option><option value="held">Held recently</option><option value="all">All listings</option></select></label>
      <label>Needs attention<select value={params.get("attention") ?? ""} onChange={e => filter("attention", e.target.value)}>
        <option value="">Show all</option><option value="bars">Missing latest session bars</option><option value="untagged">Trades without tags</option></select></label>
    </div>
    <ul className="matrix-legend" aria-label="Legend">
      {(["ok", "stale", "missing", "needs_tags", "stored", "none", "unavailable"] as CellState[]).map(state =>
        <li key={state}><span className={`matrix-cell matrix-cell--${state}`}>{stateLabel[state]}</span></li>)}
    </ul>
    {Boolean(remote.error) && <p role="alert" className="notice notice--error">{errorMessage(remote.error, "The data matrix could not be loaded.")}</p>}
    {remote.loading && !data && <p role="status">Loading the data matrix…</p>}
    {data && <>
      <p className="matrix-summary">{data.total.toLocaleString()} tickers{Object.entries(data.expected_sessions).length ? ` · latest sessions ${Object.entries(data.expected_sessions).map(([c, d]) => `${c} ${d}`).join(", ")}` : ""}</p>
      {data.unavailable.length > 0 && <p role="status" className="notice">Could not read: {data.unavailable.join(", ")}. Those cells show “?” and are not counted as healthy.</p>}
      <div className="matrix-scroll"><table className="matrix-table">
        <thead>
          <tr><th rowSpan={2} className="matrix-sticky">Ticker</th>{groups.map((g, i) => <th key={`${g.group}-${i}`} colSpan={g.span} className={`matrix-group matrix-group--${g.group}`}>{groupLabel[g.group]}</th>)}</tr>
          <tr>{data.columns.map(c => <th key={c.key} scope="col" title={`${c.dataset}${c.level === "issuer" ? " · issuer-level" : ""}`}>{c.label}{c.level === "issuer" && <small> issuer</small>}</th>)}</tr>
        </thead>
        <tbody>{data.items.map(row => <tr key={row.instrument.id}>
          <th scope="row" className="matrix-sticky"><button type="button" onClick={() => select(row.instrument)} aria-label={`Open ${row.instrument.symbol}`}>{row.instrument.symbol}</button><small>{row.instrument.exchange} · {row.instrument.name}</small></th>
          {data.columns.map(c => {
            const cell = row.cells[c.key];
            const pill = <CellPill cell={cell} column={c} />;
            if (!cell || ["none", "not_configured"].includes(cell.state)) return <td key={c.key}>{pill}</td>;
            return <td key={c.key}>{ledgerColumn(c)
              ? <a href={cellHref(c, row.instrument.id)}>{pill}</a>
              : <button type="button" className="matrix-cell-button" onClick={() => select(row.instrument)} aria-label={`${row.instrument.symbol} ${c.label}: ${stateLabel[cell.state]}`}>{pill}</button>}</td>;
          })}
        </tr>)}</tbody>
      </table></div>
      {!data.items.length && <p>No tickers match these filters.</p>}
      <div className="matrix-pager"><span>{data.total ? `${offset + 1}–${Math.min(offset + PAGE, data.total)} of ${data.total.toLocaleString()}` : "0 results"}</span>
        <button type="button" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE)) })}>Previous</button>
        <button type="button" disabled={offset + PAGE >= data.total} onClick={() => update({ offset: String(offset + PAGE) })}>Next</button></div>
    </>}
  </section>;
}

/** One ticker's row of the matrix, plus its holdings per account, at the top of its detail page. */
export function AtAGlance({ listingId }: { listingId: string }) {
  const remote = useRemote<MatrixPage>(`${MATRIX_API}?listing_id=${listingId}&scope=all&detail=true&limit=1`);
  const page = remote.data && Array.isArray(remote.data.items) && Array.isArray(remote.data.columns) ? remote.data : null;
  const row = page?.items[0];
  if (remote.error || (remote.data && !page)) return <p role="status" className="notice">The at-a-glance summary is unavailable; dataset panels below still load.</p>;
  if (!page) return <p role="status">Loading what is stored for this ticker…</p>;
  if (!row) return null;
  const columns = page.columns.filter(c => row.cells[c.key] && row.cells[c.key].state !== "not_configured");
  const holdings = row.cells.positions?.items ?? [];
  return <section className="matrix-glance" aria-label="At a glance">
    <h3>At a glance</h3>
    <ul>{columns.map(c => <li key={c.key}><span>{c.label}</span>
      <a href={cellHref(c, listingId)}><CellPill cell={row.cells[c.key]} column={c} /></a>
      <small>{row.cells[c.key].detail}</small></li>)}</ul>
    {holdings.length > 0 && <div className="instrument-grid"><table><thead><tr><th>Account</th><th>Currency</th><th>Position</th><th>Market value</th><th>Snapshot</th></tr></thead>
      <tbody>{holdings.map((h, i) => <tr key={i}><td>{String(h.broker_code).toUpperCase()} {String(h.account_id)} · {String(h.account_mode)}</td><td>{String(h.currency)}</td><td>{String(h.position)}</td><td>{h.market_value === null ? "—" : String(h.market_value)}</td><td>{short(String(h.snapshot_time))}</td></tr>)}</tbody></table></div>}
    <a className="instrument-link" href={`/portfolio/trades?listing_id=${listingId}`}>All trades for this ticker →</a>
  </section>;
}
