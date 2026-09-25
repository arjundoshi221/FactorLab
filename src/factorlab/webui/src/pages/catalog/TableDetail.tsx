import { useMemo } from "react";

import { BarChart, EmptyState, ErrorBanner, MarketSwitch, MetricCard, Skeleton, StatusPill, Tabs, type TabItem } from "../../components/ui";
import { marketLabel, marketSlice, tableHref, type CatalogTableDetail, type TableActivity, type TableStats } from "../../dataTypes";
import { errorMessage, useRemote } from "../../shared/api";
import { formatBytes, formatCompact, formatNumber, formatPercent, formatRange, formatRelative, formatTime } from "../../shared/format";
import { useUrlParams } from "../../shared/urlState";
import { RunList } from "./RunList";
import { TablePreview } from "./TablePreview";

type Tab = "overview" | "columns" | "preview" | "activity";
const TABS: Tab[] = ["overview", "columns", "preview", "activity"];

function KeyBadges({ column }: { column: CatalogTableDetail["column_details"][number] }) {
  return (
    <span className="data-key-badges">
      {column.in_sorting_key && <b title="Part of the sorting key: filtering on it is fast">KEY</b>}
      {column.in_partition_key && <b title="Used to split storage into partitions">PART</b>}
      {column.nullable && <b title="Can be empty">OPTIONAL</b>}
      {column.hidden && <b className="is-hidden" title="Not shown in previews">HIDDEN</b>}
    </span>
  );
}

function Overview({ detail, market }: { detail: CatalogTableDetail; market: string }) {
  const outgoing = detail.related.filter((item) => item.direction === "out");
  const incoming = detail.related.filter((item) => item.direction === "in");
  const slice = market ? marketSlice(detail, market) : null;
  const rows = slice ? slice.stored_rows : detail.stored_rows;
  const first = slice ? slice.first_data_at : detail.first_data_at;
  const last = slice ? slice.last_data_at : detail.last_data_at;
  const updated = slice ? slice.last_ingested_at : detail.last_ingested_at;
  return (
    <div className="data-overview">
      <section className="metric-grid" aria-label="Table facts">
        <MetricCard label={detail.kind === "view" ? "Kind" : slice ? `${slice.label} rows` : "Rows stored"}
          value={detail.kind === "view" ? "View" : formatCompact(rows)}
          detail={detail.kind === "view" ? "Computed when queried" : `${formatNumber(rows)} physical rows`} />
        <MetricCard label="Covers" value={<span className="data-metric-range">{formatRange(first, last)}</span>}
          detail={detail.preview.time_column ? `Based on ${detail.preview.time_column}` : "No time column"} />
        <MetricCard label="Last updated" value={updated ? formatRelative(updated) : "—"}
          detail={updated ? formatTime(updated) : "Nothing ingested yet"} />
        <MetricCard label="Size" value={formatBytes(detail.bytes_on_disk)} detail={`${detail.column_count} columns · all markets`} />
      </section>
      {(detail.markets?.length ?? 0) > 1 && (
        <section className="data-panel data-market-breakdown" aria-label="By market">
          <h3>By market</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Market</th><th className="numeric">Rows</th><th>Covers</th><th>Last updated</th></tr></thead>
              <tbody>
                {detail.markets!.map((item) => (
                  <tr key={item.country_code} className={item.country_code === market ? "is-current" : ""}>
                    <td data-label="Market"><strong>{item.label}</strong> <code>{item.country_code}</code></td>
                    <td data-label="Rows" className="numeric">{formatNumber(item.stored_rows)}</td>
                    <td data-label="Covers">{formatRange(item.first_data_at, item.last_data_at)}</td>
                    <td data-label="Last updated" title={formatTime(item.last_ingested_at)}>{formatRelative(item.last_ingested_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <div className="data-overview__grid">
        <div>
          {detail.notes.length > 0 && (
            <section className="data-panel">
              <h3>Good to know</h3>
              <ul className="data-bullets">{detail.notes.map((note) => <li key={note}>{note}</li>)}</ul>
            </section>
          )}
          <section className="data-panel">
            <h3>How rows are kept</h3>
            <ul className="data-bullets">
              {detail.keys.explanation.map((line) => <li key={line}>{line}</li>)}
              {detail.preview.latest_version_only && <li>Previews show only the latest version of each row.</li>}
            </ul>
          </section>
          {detail.design_notes && (
            <details className="data-panel data-design-notes">
              <summary>From the schema design</summary>
              <p>{detail.design_notes}</p>
            </details>
          )}
        </div>
        <div>
          <section className="data-panel">
            <h3>Collected by</h3>
            {detail.writers.length ? (
              <ul className="data-links">
                {detail.writers.map((writer) => (
                  <li key={writer.id}><a href={`/data/pipelines#${writer.id}`}>{writer.label}</a><small>{writer.schedule}</small></li>
                ))}
              </ul>
            ) : <p className="data-muted">No collection pipeline writes this table yet.</p>}
          </section>
          <section className="data-panel">
            <h3>Related tables</h3>
            <p className="data-note">
              <a href={`/schema?table=${encodeURIComponent(detail.name)}&sel=${encodeURIComponent(detail.name)}`}>See how it connects in the schema explorer →</a>
            </p>
            {detail.related.length === 0 && <p className="data-muted">No links to other tables were found.</p>}
            {outgoing.length > 0 && (
              <>
                <h4>Looks up</h4>
                <ul className="data-links">
                  {outgoing.map((item) => (
                    <li key={`o-${item.column}-${item.table}`}>
                      <a href={tableHref(item.table)}>{item.table_title}</a>
                      <small><code>{item.column}</code> → <code>{item.table}.{item.target_column}</code></small>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {incoming.length > 0 && (
              <>
                <h4>Referenced by</h4>
                <ul className="data-links">
                  {incoming.map((item) => (
                    <li key={`i-${item.table}-${item.target_column}`}>
                      <a href={tableHref(item.table)}>{item.table_title}</a>
                      <small><code>{item.table}.{item.target_column}</code></small>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function Columns({ detail, market }: { detail: CatalogTableDetail; market: string }) {
  const profiled = detail.preview.enabled && detail.stored_rows > 0;
  const stats = useRemote<TableStats>(
    profiled ? `/hub/api/v1/catalog/tables/${detail.name}/stats${market ? `?country=${market}` : ""}` : null,
  );
  const byName = useMemo(() => new Map((stats.data?.columns ?? []).map((item) => [item.name, item])), [stats.data]);
  return (
    <section className="data-columns" aria-label="Columns">
      <p className="data-note">
        {profiled
          ? stats.data
            ? `Profile based on the most recent ${formatNumber(stats.data.sample_rows)} stored${market ? ` ${marketLabel(market)}` : ""} rows${stats.data.window_start ? " in the latest window" : ""}.`
            : stats.error ? "" : "Profiling the most recent rows…"
          : "Column profiles appear once the table has data."}
      </p>
      {stats.error ? <ErrorBanner onRetry={stats.refresh}>{errorMessage(stats.error, "The column profile could not be loaded.")}</ErrorBanner> : null}
      <div className="table-wrap">
        <table className="data-columns-table">
          <thead>
            <tr><th>Column</th><th>Type</th><th>Description</th><th className="numeric">Empty</th><th className="numeric">Distinct</th><th>Range or common values</th></tr>
          </thead>
          <tbody>
            {detail.column_details.map((column) => {
              const stat = byName.get(column.name);
              return (
                <tr key={column.name}>
                  <td data-label="Column"><code>{column.name}</code><KeyBadges column={column} /></td>
                  <td data-label="Type">{column.friendly_type}<small>{column.type}</small></td>
                  <td data-label="Description">{column.description ?? <span className="data-muted">—</span>}</td>
                  <td data-label="Empty" className="numeric">{stat ? formatPercent(stat.nulls_percent) : "—"}</td>
                  <td data-label="Distinct" className="numeric">{stat?.distinct_approx != null ? `≈${formatCompact(stat.distinct_approx)}` : "—"}</td>
                  <td data-label="Range">
                    {stat?.min != null && stat.max != null ? <span className="data-range">{stat.min} → {stat.max}</span>
                      : stat?.top_values.length ? <span className="data-top-values">{stat.top_values.map((value) => <code key={value}>{value}</code>)}</span>
                      : column.hidden ? <span className="data-muted">Hidden</span> : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Activity({ detail, market }: { detail: CatalogTableDetail; market: string }) {
  const [params, setParams] = useUrlParams();
  const grain = params.get("grain") === "month" ? "month" : "day";
  const activity = useRemote<TableActivity>(
    `/hub/api/v1/catalog/tables/${detail.name}/activity?grain=${grain}${market ? `&country=${market}` : ""}`,
  );
  const bars = (activity.data?.buckets ?? []).map((item) => ({ label: item.bucket, value: item.rows }));
  return (
    <section className="data-activity" aria-label="Activity">
      <div className="data-activity__heading">
        <h3>Rows stored per {grain}</h3>
        <div className="segmented" role="group" aria-label="Chart grain">
          {(["day", "month"] as const).map((item) => (
            <button key={item} type="button" aria-pressed={grain === item} className={grain === item ? "is-active" : ""}
              onClick={() => setParams((next) => next.set("grain", item))}>{item === "day" ? "Last 90 days" : "Last 3 years"}</button>
          ))}
        </div>
      </div>
      {activity.error ? <ErrorBanner onRetry={activity.refresh}>{errorMessage(activity.error, "Activity could not be loaded.")}</ErrorBanner> : null}
      {activity.loading && !activity.data ? <Skeleton rows={3} /> : bars.length ? (
        <BarChart bars={bars} caption={`Stored${market ? ` ${marketLabel(market)}` : ""} rows by ${grain} of ${activity.data?.time_column ?? "time"}`} />
      ) : activity.data ? <EmptyState title="No rows to chart yet." /> : null}
      <h3>Recent collection runs</h3>
      {activity.data && <RunList runs={activity.data.runs} now={Date.now()} />}
    </section>
  );
}

export function TableDetail({ name }: { name: string }) {
  const detail = useRemote<CatalogTableDetail>(`/hub/api/v1/catalog/tables/${name}`, 60_000);
  const [params, setParams] = useUrlParams();
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : "overview";
  const data = detail.data;

  if (detail.loading && !data) return <main className="data-page"><Skeleton rows={8} /></main>;
  if (!data) {
    return (
      <main className="data-page">
        <nav className="data-breadcrumbs"><a href="/data">Catalog</a> / <span>{name}</span></nav>
        <ErrorBanner onRetry={detail.refresh}>{errorMessage(detail.error, "This table could not be loaded.")}</ErrorBanner>
      </main>
    );
  }

  const tabs: TabItem<Tab>[] = [
    { id: "overview", label: "Overview" },
    { id: "columns", label: "Columns", hint: String(data.column_count) },
    { id: "preview", label: "Preview rows", disabled: data.kind === "view" },
    { id: "activity", label: "Activity", disabled: data.kind === "view" },
  ];
  const markets = (data.markets ?? []).filter((item) => item.stored_rows > 0);
  const requestedMarket = params.get("market") ?? "";
  const market = markets.some((item) => item.country_code === requestedMarket) ? requestedMarket : "";

  function chooseMarket(code: string) {
    setParams((query) => {
      if (code) query.set("market", code);
      else query.delete("market");
      // A window or filter chosen for one market rarely fits another.
      ["start", "end", "page", "f.country_code"].forEach((key) => query.delete(key));
    });
  }
  return (
    <main className="data-page data-table-page">
      <nav className="data-breadcrumbs" aria-label="Breadcrumb">
        <a href="/data">Catalog</a> / <a href={`/data?ns=${data.namespace}`}>{data.namespace}</a> / <span>{data.name}</span>
      </nav>
      <header className="data-table-header">
        <div>
          <span className="eyebrow">{data.kind === "view" ? "View" : "Table"} · <code>{data.name}</code></span>
          <h1>{data.title}</h1>
          <p>{data.summary}</p>
        </div>
        <div className="data-table-header__status">
          {data.kind === "table" ? <StatusPill status={data.status} /> : <span className="data-badge">View</span>}
          <small>{data.status_reason}</small>
        </div>
      </header>
      {markets.length > 1 && (
        <MarketSwitch
          value={market}
          onChange={chooseMarket}
          options={markets.map((item) => ({ code: item.country_code, label: item.label, detail: `${formatCompact(item.stored_rows)} rows` }))}
        />
      )}
      <Tabs label="Table sections" tabs={tabs} active={tab} onChange={(next) => setParams((query) => {
        [...query.keys()].forEach((key) => { if (key !== "tab" && key !== "market" && next !== "preview") query.delete(key); });
        query.set("tab", next);
      }, true)} />
      <div className="data-tab-panel" role="tabpanel">
        {tab === "overview" && <Overview detail={data} market={market} />}
        {tab === "columns" && <Columns key={market} detail={data} market={market} />}
        {tab === "preview" && <TablePreview detail={data} market={market} />}
        {tab === "activity" && <Activity key={market} detail={data} market={market} />}
      </div>
      <footer>FactorLab data catalog · Times in IST unless marked UTC</footer>
    </main>
  );
}
