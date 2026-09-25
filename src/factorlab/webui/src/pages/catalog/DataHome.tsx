import { useMemo } from "react";

import { EmptyState, ErrorBanner, MarketSwitch, Skeleton, StatusPill, statusLabels, type HubStatus } from "../../components/ui";
import { MARKETS, marketLabel, marketSlice, tableHref, type CatalogIndex, type CatalogTableSummary, type PipelinesResponse } from "../../dataTypes";
import { errorMessage, useRemote } from "../../shared/api";
import { formatBytes, formatCompact, formatRange, formatRelative, formatTime } from "../../shared/format";
import { useUrlParams } from "../../shared/urlState";

interface Match { table: CatalogTableSummary; score: number; columns: string[] }

function search(tables: CatalogTableSummary[], query: string): Match[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return tables.map((table) => ({ table, score: 0, columns: [] }));
  const matches: Match[] = [];
  for (const table of tables) {
    const heading = `${table.name} ${table.title}`.toLowerCase();
    const summary = table.summary.toLowerCase();
    let score = 0;
    const columns = new Set<string>();
    let every = true;
    for (const term of terms) {
      const columnHits = table.columns.filter((column) =>
        column.toLowerCase().includes(term) || (table.column_notes[column] ?? "").toLowerCase().includes(term));
      if (heading.includes(term)) score += 3;
      else if (summary.includes(term)) score += 1;
      else if (columnHits.length) score += 0.5;
      else every = false;
      columnHits.slice(0, 3).forEach((column) => columns.add(column));
    }
    if (every) matches.push({ table, score, columns: [...columns] });
  }
  return matches.sort((a, b) => b.score - a.score || b.table.stored_rows - a.table.stored_rows);
}

function CollectingNow({ pipelines }: { pipelines: PipelinesResponse | null }) {
  if (!pipelines) return null;
  const scheduled = pipelines.pipelines.filter((item) => item.scheduled);
  const now = new Date(pipelines.generated_at).valueOf();
  return (
    <section className="data-now" aria-labelledby="collecting-now">
      <div className="data-now__heading">
        <h2 id="collecting-now">Collecting now</h2>
        <a href="/data/pipelines">All pipelines →</a>
      </div>
      <ul>
        {scheduled.map((pipeline) => (
          <li key={pipeline.id}>
            <a href={`/data/pipelines#${pipeline.id}`}>
              <StatusPill status={pipeline.status} label={pipeline.label} />
              <small>
                {pipeline.last_started_at ? `Ran ${formatRelative(pipeline.last_started_at, now)}` : "No recent runs"}
                {pipeline.rows_24h > 0 && ` · ${formatCompact(pipeline.rows_24h)} rows today`}
              </small>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TableCard({ match, now, market }: { match: Match; now: number; market: string }) {
  const { table, columns } = match;
  const empty = table.kind === "table" && table.stored_rows === 0;
  const slice = market ? marketSlice(table, market) : null;
  const rows = slice ? slice.stored_rows : table.stored_rows;
  const first = slice ? slice.first_data_at : table.first_data_at;
  const last = slice ? slice.last_data_at : table.last_data_at;
  const updated = slice ? slice.last_ingested_at : table.last_ingested_at;
  const split = !market && (table.markets?.length ?? 0) > 1;
  return (
    <li className={`data-table-card${empty ? " is-empty" : ""}`}>
      <a href={tableHref(table.name, undefined, slice ? market : undefined)}>
        <div className="data-table-card__heading">
          <div>
            <strong>{table.title}{slice && <span className="data-market-tag">{marketLabel(market)}</span>}</strong>
            <code>{table.name}</code>
          </div>
          {table.kind === "view" ? <span className="data-badge">View</span> : <StatusPill status={table.status} />}
        </div>
        <p>{table.summary}</p>
        <dl>
          {table.kind === "table" && <div><dt>{slice ? `${marketLabel(market)} rows` : "Rows"}</dt><dd>{formatCompact(rows)}</dd></div>}
          {table.kind === "table" && !slice && <div><dt>Size</dt><dd>{formatBytes(table.bytes_on_disk)}</dd></div>}
          <div><dt>Covers</dt><dd>{formatRange(first, last)}</dd></div>
          {updated && <div><dt>Updated</dt><dd title={formatTime(updated)}>{formatRelative(updated, now)}</dd></div>}
        </dl>
        {split && (
          <small className="data-table-card__markets">
            {table.markets!.map((item) => <span key={item.country_code}>{item.label} <b>{formatCompact(item.stored_rows)}</b></span>)}
          </small>
        )}
        {columns.length > 0 && (
          <small className="data-table-card__match">
            Matches column{columns.length > 1 ? "s" : ""} {columns.map((column) => <code key={column}>{column}</code>)}
          </small>
        )}
      </a>
    </li>
  );
}

export function DataHome() {
  const catalog = useRemote<CatalogIndex>("/hub/api/v1/catalog", 60_000);
  const pipelines = useRemote<PipelinesResponse>("/hub/api/v1/catalog/pipelines", 60_000);
  const [params, setParams] = useUrlParams();
  const query = params.get("q") ?? "";
  const namespace = params.get("ns") ?? "";
  const showEmpty = params.get("all") === "1";
  const requestedMarket = params.get("market") ?? "";
  const market = MARKETS.some((item) => item.code === requestedMarket) ? requestedMarket : "";
  const data = catalog.data;
  const now = data ? new Date(data.generated_at).valueOf() : Date.now();

  const inArea = useMemo(
    () => (data?.tables ?? []).filter((table) => !namespace || table.namespace === namespace),
    [data, namespace],
  );
  // Offer the market switch wherever the visible tables mix countries, starting with Market data.
  const marketOptions = useMemo(() => MARKETS.map((option) => ({
    ...option,
    rows: inArea.reduce((sum, table) => sum + (marketSlice(table, option.code)?.stored_rows ?? 0), 0),
  })).filter((option) => option.rows > 0), [inArea]);
  const activeMarket = marketOptions.some((option) => option.code === market) ? market : "";

  const { matches, hiddenEmpty, hiddenMarket } = useMemo(() => {
    const found = search(inArea, query);
    const keepEmpty = showEmpty || Boolean(query);
    const populated = keepEmpty ? found : found.filter((item) => item.table.kind === "view" || item.table.stored_rows > 0);
    const visible = activeMarket ? populated.filter((item) => marketSlice(item.table, activeMarket)) : populated;
    return {
      matches: visible,
      hiddenEmpty: found.length - populated.length,
      hiddenMarket: populated.length - visible.length,
    };
  }, [inArea, query, showEmpty, activeMarket]);

  const totals = useMemo(() => {
    const tables = (data?.tables ?? []).filter((table) => table.kind === "table");
    const counts: Partial<Record<HubStatus, number>> = {};
    tables.forEach((table) => { counts[table.status] = (counts[table.status] ?? 0) + 1; });
    return {
      populated: tables.filter((table) => table.stored_rows > 0).length,
      tables: tables.length,
      rows: tables.reduce((sum, table) => sum + table.stored_rows, 0),
      bytes: tables.reduce((sum, table) => sum + table.bytes_on_disk, 0),
      counts,
    };
  }, [data]);

  function setParam(key: string, value: string) {
    setParams((next) => { if (value) next.set(key, value); else next.delete(key); });
  }

  if (catalog.loading && !data) {
    return <main className="data-page"><section className="data-hero"><h1>Data catalog</h1></section><Skeleton rows={6} /></main>;
  }
  if (!data) {
    return (
      <main className="data-page">
        <section className="data-hero"><span className="eyebrow">Data catalog</span><h1>The catalog could not load.</h1></section>
        <ErrorBanner onRetry={catalog.refresh}>{errorMessage(catalog.error)}</ErrorBanner>
      </main>
    );
  }

  const selected = data.namespaces.find((item) => item.id === namespace);
  return (
    <main className="data-page">
      <section className="data-hero">
        <span className="eyebrow">Data catalog</span>
        <h1>Find the data you need.</h1>
        <p>
          Every FactorLab dataset in plain language: what it holds, how far back it goes, how fresh it is,
          and a preview of the rows.
        </p>
        <label className="data-search">
          <span className="sr-only">Search tables and columns</span>
          <input
            type="search"
            value={query}
            placeholder="Search tables and columns, e.g. close price, political trades, listing"
            onChange={(event) => setParam("q", event.target.value)}
          />
        </label>
        <div className="data-hero__facts">
          <span><strong>{totals.populated}</strong> of {totals.tables} tables have data</span>
          <span><strong>{formatCompact(totals.rows)}</strong> rows stored</span>
          <span><strong>{formatBytes(totals.bytes)}</strong> on disk</span>
          {(totals.counts.attention ?? 0) + (totals.counts.missing ?? 0) > 0 && (
            <span className="is-warning"><strong>{(totals.counts.attention ?? 0) + (totals.counts.missing ?? 0)}</strong> need attention</span>
          )}
        </div>
      </section>

      {catalog.error ? <ErrorBanner onRetry={catalog.refresh}>{errorMessage(catalog.error)} Showing the last loaded catalog.</ErrorBanner> : null}

      <CollectingNow pipelines={pipelines.data} />

      <nav className="data-chips" aria-label="Filter by area">
        <button type="button" aria-pressed={!namespace} className={!namespace ? "is-active" : ""} onClick={() => setParam("ns", "")}>
          All areas <small>{data.tables.length}</small>
        </button>
        {data.namespaces.map((item) => (
          <button key={item.id} type="button" aria-pressed={namespace === item.id} className={namespace === item.id ? "is-active" : ""}
            onClick={() => setParam("ns", namespace === item.id ? "" : item.id)}>
            {item.title} <small>{item.populated_tables}/{item.table_count + item.view_count}</small>
          </button>
        ))}
      </nav>

      {namespace && marketOptions.length > 0 && (
        <MarketSwitch
          value={activeMarket}
          onChange={(code) => setParam("market", code)}
          options={marketOptions.map((option) => ({ code: option.code, label: option.label, detail: `${formatCompact(option.rows)} rows` }))}
        />
      )}

      {!query && !namespace && (
        <section className="data-namespaces" aria-label="Data areas">
          {data.namespaces.map((item) => (
            <button key={item.id} type="button" className={`data-namespace${item.stored_rows ? "" : " is-empty"}`} onClick={() => setParam("ns", item.id)}>
              <span className="eyebrow">{item.id}</span>
              <strong>{item.title}</strong>
              <p>{item.summary}</p>
              <span className="data-namespace__facts">
                {item.populated_tables} of {item.table_count} tables with data · {formatCompact(item.stored_rows)} rows · {formatBytes(item.bytes_on_disk)}
              </span>
              {item.id === "market" && (
                <span className="data-namespace__markets">
                  {MARKETS.map((option) => {
                    const rows = data.tables.filter((table) => table.namespace === "market")
                      .reduce((sum, table) => sum + (marketSlice(table, option.code)?.stored_rows ?? 0), 0);
                    return rows ? <span key={option.code}>{option.label} <b>{formatCompact(rows)}</b></span> : null;
                  })}
                </span>
              )}
              <span className="data-namespace__status" aria-label="Table health">
                {(Object.entries(item.status_counts) as [HubStatus, number][]).map(([status, count]) => (
                  <span key={status} className={`status--${status}`} title={`${count} ${statusLabels[status].toLowerCase()}`}>
                    <span className="status__dot" aria-hidden="true" />{count}
                  </span>
                ))}
              </span>
            </button>
          ))}
        </section>
      )}

      <section className="data-results" aria-labelledby="data-results-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">{selected ? selected.id : query ? "Search results" : "All data"}</span>
            <h2 id="data-results-title">{selected ? selected.title : query ? `Results for “${query}”` : "Tables with data"}</h2>
            {selected && <p className="data-results__summary">{selected.summary}</p>}
          </div>
          <span>{matches.length} {matches.length === 1 ? "table" : "tables"}</span>
        </div>
        {matches.length ? (
          <ul className="data-table-grid">{matches.map((match) => <TableCard key={match.table.name} match={match} now={now} market={activeMarket} />)}</ul>
        ) : (
          <EmptyState title={query ? "Nothing matches that search." : "No tables with data here yet."}>
            {query ? "Try a column name such as close, ticker, or listing_id, or clear the search." : "The tables in this area are reserved for data FactorLab will collect later."}
          </EmptyState>
        )}
        {hiddenMarket > 0 && (
          <p className="data-note">
            {hiddenMarket} {hiddenMarket === 1 ? "table has" : "tables have"} no {marketLabel(activeMarket)} data and {hiddenMarket === 1 ? "is" : "are"} hidden.{" "}
            <button type="button" className="data-link-button" onClick={() => setParam("market", "")}>Show all markets</button>
          </p>
        )}
        {hiddenEmpty > 0 && (
          <button type="button" className="button-secondary data-results__toggle" onClick={() => setParam("all", "1")}>
            Show {hiddenEmpty} reserved {hiddenEmpty === 1 ? "table" : "tables"} without data yet
          </button>
        )}
        {showEmpty && !query && (
          <button type="button" className="button-secondary data-results__toggle" onClick={() => setParam("all", "")}>Hide tables without data</button>
        )}
      </section>
      <footer>FactorLab data catalog · Times in IST · Row counts are physical stored rows and can include superseded versions</footer>
    </main>
  );
}
