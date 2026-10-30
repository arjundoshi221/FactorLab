import { useEffect, useRef, useState, type ReactNode } from "react";
import { PageHeader } from "../components/ui";
import { errorMessage, fetchJson } from "../shared/api";
import { Treemap } from "../shared/charts";
import type { Dataset, DatasetResult, Identity, Instrument, InstrumentPage, SectorSummary } from "../instrumentTypes";
import { AtAGlance, DataMatrix } from "./DataMatrix";
import { Futures } from "./Futures";
import { Indexes } from "./Indexes";
import { CollectionCoverage } from "./MarketData";
import "./InstrumentExplorer.css";

const base = "/hub/api/v1/market-data/instruments";
const groups = ["Market", "Alternative & political", "Fundamentals", "Broker & portfolio", "Derived", "Reference & operations"];
const label = (i: Instrument) => `${i.symbol || i.id} · ${i.exchange}${i.kind === "contract" ? ` · ${i.expiry} ${i.strike ?? ""} ${i.right}` : ""}`;
const cell = (v: unknown) => v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
const states = { stored: "Stored data", empty: "No matching records", missing_table: "Missing table", unavailable: "Query unavailable", unsupported: "Unsupported linkage" };
const PAGE = 50;

type View = "all" | "sectors" | "indexes" | "futures" | "matrix" | "collection";
const views: { key: View; title: string; code: string; intro: string }[] = [
  { key: "all", code: "MKT", title: "All tickers", intro: "Every registered listing and derivative contract, including inactive instruments and instruments without collected data. Select one to see everything stored for it." },
  { key: "sectors", code: "SECT", title: "By sector", intro: "Every listing grouped by its current GICS sector (iShares IWV holdings). Expand a sector to see its tickers; listings without a sector are grouped as unclassified." },
  { key: "indexes", code: "INDX", title: "Indexes", intro: "Official US indexes, plus FactorLab's own GICS sector indexes over the Russell 3000 proxy, equal- and cap-weighted." },
  { key: "futures", code: "FUT", title: "Futures", intro: "US futures: each recorded root's live contracts at the latest Schwab quote, a price history for the selected expiry, and the contract table. No rolls or back-adjustment." },
  { key: "matrix", code: "MTX", title: "Data matrix", intro: "Each FactorLab ticker against each dataset. Bars are judged against the exchange's latest completed session; holdings, trades and tags follow the account owner's profile. Select a ticker to see everything linked to it." },
  { key: "collection", code: "COV", title: "Collection coverage", intro: "Every ticker configured for collection in the US and India, with the bars stored for each in daily collection and historical recovery." },
];

// Abort and clear on every URL change; a slow old instrument must never replace a new one.
function useLoad<T>(url: string | null) {
  const [state, setState] = useState<{ data?: T; error?: string; url: string | null }>({ url: null });
  useEffect(() => {
    const controller = new AbortController();
    setState({ url });
    if (url) void fetchJson<T>(url, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setState({ data, url });
    }).catch(error => { if (!controller.signal.aborted) setState({ error: errorMessage(error), url }); });
    return () => controller.abort();
  }, [url]);
  return state.url === url ? state : { url };
}

function Pill({ tone, children }: { tone?: "healthy"; children: ReactNode }) {
  return <span className={`status${tone ? ` status--${tone}` : ""}`}><span className="status__dot" aria-hidden="true" />{children}</span>;
}

function Grid({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return <div className="instrument-grid"><table><thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j}>{cell(v)}</td>)}</tr>)}</tbody></table></div>;
}

function Preview({ url }: { url: string }) {
  const [provider, setProvider] = useState("");
  const [start, setStart] = useState(() => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ provider, start: `${start}T00:00:00Z`, end: `${end}T23:59:59Z`, offset: String(offset), limit: String(PAGE) });
  const { data, error } = useLoad<DatasetResult>(start && end ? `${url}/rows?${params}` : null);
  return <section className="md-preview" aria-label="Record preview">
    <p className="md-note">Latest 30 days by default. Dates are UTC. Coverage above is lifetime; this preview is a separate window.</p>
    <div className="instrument-filters">
      <label>Provider<input value={provider} placeholder="Any" onChange={e => { setProvider(e.target.value); setOffset(0); }} /></label>
      <label>From<input type="date" value={start} onChange={e => { setStart(e.target.value); setOffset(0); }} /></label>
      <label>Through<input type="date" value={end} onChange={e => { setEnd(e.target.value); setOffset(0); }} /></label>
    </div>
    {error && <p role="alert" className="notice notice--error">{error}</p>}
    {!data && !error && <p role="status" className="md-loading">Loading records…</p>}
    {data && <>
      <p className="md-meta">{states[data.state]} {data.message}</p>
      <Grid columns={data.columns} rows={data.rows} />
      <div className="md-pager"><span>Rows {offset + 1}–{offset + data.rows.length} · checked {data.checked_at}</span><div>
        <button type="button" className="md-ghost" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Previous records</button>
        <button type="button" className="md-ghost" disabled={!data.has_more || offset >= 5000} onClick={() => setOffset(offset + PAGE)}>Next records</button>
      </div></div>
      <details className="md-disclosure"><summary>Recorded provenance for this page</summary>{Object.entries(data.evidence).map(([key, evidence]) =>
        <section key={key}><h5>{key}: {evidence.state}</h5><p>{evidence.message}</p>
          {evidence.table && <a className="md-link" href={`/data/tables/${evidence.table}`}>{evidence.table}</a>}
          {!!evidence.unavailable_ids?.length && <p>Missing or expired evidence: {evidence.unavailable_ids.join(", ")}</p>}
          {evidence.columns && <Grid columns={evidence.columns} rows={evidence.rows ?? []} />}
        </section>)}</details>
    </>}
  </section>;
}

function DatasetPanel({ dataset, coverage, error, url }: { dataset: Dataset; coverage?: DatasetResult; error?: string; url: string }) {
  const [expanded, setExpanded] = useState(false);
  const linked = dataset.relationships.length > 0;
  const tone = coverage?.state ?? (error ? "unavailable" : linked ? "pending" : "unsupported");
  return <article className={`md-dataset md-dataset--${tone}${expanded ? " is-expanded" : ""}`} id={`dataset-${dataset.table}`}>
    <header>
      <div><h4>{dataset.title}</h4><a className="md-table-link" href={`/data/tables/${dataset.table}`}><code>{dataset.table}</code></a></div>
      {coverage ? <strong className="md-state">{states[coverage.state]}</strong>
        : <span className="md-state">{!linked ? "Unsupported linkage" : error ? "Coverage unavailable" : "Coverage pending…"}</span>}
    </header>
    <p>{dataset.description}</p>
    <p className="md-meta">{dataset.relationships.join(", ") || "No reliable instrument link"} · {dataset.classification}</p>
    {dataset.notes.map(note => <p key={note} className="md-note">{note}</p>)}
    {dataset.classification === "investigation" && <p className="notice">Investigation candidates only — excluded from confirmed coverage.</p>}
    {error && <p role="alert" className="notice notice--error">{error}</p>}
    {coverage && <>
      <p className="md-meta">{coverage.message ? `${coverage.message} · ` : ""}Checked {coverage.checked_at}</p>
      {!!coverage.groups.length && <Grid columns={Object.keys(coverage.groups[0])} rows={coverage.groups.map(g => Object.values(g))} />}
    </>}
    {(linked || dataset.writers.length > 0) && <div className="md-dataset__actions">
      {dataset.writers.map(writer => <a key={writer} className="md-link" href={`/data/pipelines#${writer}`}>{writer} runs →</a>)}
      {linked && <button type="button" className="md-ghost" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide records" : "Inspect records"}</button>}
    </div>}
    {expanded && <Preview url={url} />}
  </article>;
}

function Detail({ kind, id, select, close }: { kind: string; id: string; select: (i: Instrument) => void; close: () => void }) {
  const url = `${base}/${kind}/${id}`;
  const { data, error } = useLoad<Identity>(url);
  const [coverage, setCoverage] = useState<Record<string, DatasetResult>>({});
  const [failures, setFailures] = useState<Record<string, string>>({});
  const top = useRef<HTMLElement>(null);
  // Selecting a ticker from a long list should land on its detail, not leave it below the fold.
  useEffect(() => { top.current?.scrollIntoView?.({ block: "start" }); }, [url]);
  useEffect(() => {
    setCoverage({}); setFailures({});
    if (!data) return;
    const controller = new AbortController();
    const queue = data.datasets.filter(d => d.relationships.length);
    async function worker() {
      while (queue.length && !controller.signal.aborted) {
        const dataset = queue.shift()!;
        try {
          const result = await fetchJson<DatasetResult>(`${url}/datasets/${dataset.table}/coverage`, { signal: controller.signal });
          if (!controller.signal.aborted) setCoverage(old => ({ ...old, [dataset.table]: result }));
        } catch (e) { if (!controller.signal.aborted) setFailures(old => ({ ...old, [dataset.table]: errorMessage(e) })); }
      }
    }
    // Leave one server query slot available for identity, inventory, and record previews.
    void worker(); void worker();
    return () => controller.abort();
  }, [data, url]);
  const item = data?.instrument;
  return <section aria-label="Instrument detail" ref={top} className="md-detail">
    {error && <p role="alert" className="notice notice--error">{error}</p>}
    {!data && !error && <p role="status" className="md-loading">Loading instrument identity…</p>}
    {data && item && <>
      <header className="md-detail__head">
        <div>
          <span className="eyebrow">{item.kind === "contract" ? "Derivative contract" : "Listing"}{item.country ? ` · ${item.country}` : ""}</span>
          <h2>{label(item)}</h2>
          <div className="md-detail__tags">
            {item.name && <span className="md-detail__name">{item.name}</span>}
            <Pill tone={item.active ? "healthy" : undefined}>{item.active ? "Active" : "Inactive"}</Pill>
            {item.instrument_type && <Pill>{item.instrument_type}</Pill>}
          </div>
        </div>
        <button type="button" className="md-ghost" onClick={close}>Close</button>
      </header>
      {item.kind === "listing" && <AtAGlance listingId={item.id} />}
      <p className="md-note">Collection membership: {item.configured ? "Configured" : "Not configured"}. This does not establish stored data availability.</p>
      <dl className="md-ids">{["id", "listing_id", "security_id", "entity_id"].map(key => <div key={key}><dt>{key}{item.kind === "contract" && key !== "id" ? " (underlying)" : ""}</dt><dd>{cell(item[key as keyof Instrument])}</dd></div>)}</dl>
      {data.warnings.map(w => <p role="status" className="notice" key={w}>{w}</p>)}
      {Object.entries(data.identifiers ?? {}).map(([table, records]) => <details className="md-disclosure" key={table}><summary>{table} identity</summary>{!!records.length && <Grid columns={Object.keys(records[0])} rows={records.map(r => Object.values(r))} />}</details>)}
      <details className="md-disclosure"><summary>Provider identifiers and alias history ({data.aliases.length})</summary>
        {!!data.aliases.length && <Grid columns={Object.keys(data.aliases[0])} rows={data.aliases.map(a => Object.values(a))} />}
      </details>
      <details className="md-disclosure"><summary>Related listings and contracts ({data.related.length})</summary>
        <div className="md-related">{data.related.map(i => <button type="button" className="md-ghost" key={`${i.kind}/${i.id}`} onClick={() => select(i)}>{label(i)}</button>)}</div>
      </details>
      <p className="md-note">Expected series, recorded session coverage and recovery state appear under Reference &amp; operations. Run outcomes are evidence, not proof of complete coverage. Account-wide balances remain related context.</p>
      {groups.map(group => {
        const members = data.datasets.filter(d => d.group === group);
        return members.length > 0 && <section key={group} className="md-group"><h3>{group}<small>{members.length}</small></h3>
          <div className="md-group__grid">{members.map(dataset =>
            <DatasetPanel key={dataset.table} dataset={dataset} coverage={coverage[dataset.table]} error={failures[dataset.table]} url={`${url}/datasets/${dataset.table}`} />)}</div></section>;
      })}
    </>}
  </section>;
}

function AllTickers({ params, update, select }: { params: URLSearchParams; update: (values: Record<string, string>) => void; select: (i: Instrument) => void }) {
  const inventory = new URLSearchParams();
  for (const key of ["search", "country", "exchange", "instrument_type", "active", "configured", "offset"]) if (params.has(key)) inventory.set(key, params.get(key)!);
  const { data, error } = useLoad<InstrumentPage>(`${base}?${inventory}`);
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const selectedId = params.get("id");
  return <>
    <div className="instrument-filters">
      {[["search", "Search symbol, company, UUID or alias"], ["country", "Country"], ["exchange", "Exchange"], ["instrument_type", "Instrument type"]].map(([key, title]) =>
        <label key={key} className={key === "search" ? "instrument-filters__search" : ""}>{title}<input type={key === "search" ? "search" : "text"} placeholder={key === "search" ? "e.g. AAPL or Reliance" : "Any"} value={params.get(key) ?? ""} onChange={e => update({ [key]: e.target.value, offset: "" })} /></label>)}
      {[["active", "Active status"], ["configured", "Collection membership"]].map(([key, title]) => <label key={key}>{title}<select value={params.get(key) ?? "all"} onChange={e => update({ [key]: e.target.value, offset: "" })}><option value="all">All</option><option value="yes">{key === "active" ? "Active" : "Configured"}</option><option value="no">{key === "active" ? "Inactive" : "Not configured"}</option></select></label>)}
    </div>
    {error && <p role="alert" className="notice notice--error">{error}</p>}
    {!data && !error && <p role="status" className="md-loading">Loading instruments…</p>}
    {data && <>
      <p className="md-count"><strong>{data.total.toLocaleString()}</strong> instruments</p>
      <InstrumentTable data={data} offset={offset} selectedId={selectedId} select={select} page={value => update({ offset: String(value) })} />
    </>}
  </>;
}

function InstrumentTable({ data, offset, selectedId, select, page }: {
  data: InstrumentPage; offset: number; selectedId: string | null; select: (i: Instrument) => void; page: (offset: number) => void;
}) {
  return <>
    <div className="md-table"><table><thead><tr><th>Instrument</th><th>Company</th><th>Country / type</th><th>Sector</th><th>Status</th><th>Collection</th></tr></thead>
      <tbody>{data.items.map(i => <tr key={`${i.kind}/${i.id}`} className={selectedId === i.id ? "is-selected" : ""}>
        <td data-label="Instrument"><button type="button" className="md-symbol" onClick={() => select(i)}>{label(i)}</button>{i.kind === "contract" && <small>Contract</small>}</td>
        <td data-label="Company">{i.name || "—"}</td>
        <td data-label="Country / type">{i.country} / {i.instrument_type}</td>
        <td data-label="Sector">{i.sector || <span className="md-muted">Unclassified</span>}</td>
        <td data-label="Status"><Pill tone={i.active ? "healthy" : undefined}>{i.active ? "Active" : "Inactive"}</Pill></td>
        <td data-label="Collection">{i.configured ? <Pill tone="healthy">Configured</Pill> : <span className="md-muted">Not configured</span>}</td>
      </tr>)}</tbody></table>
      {!data.items.length && <p className="md-empty">No instruments match these filters.</p>}
    </div>
    <div className="md-pager"><span>{data.total ? `${(offset + 1).toLocaleString()}–${Math.min(offset + PAGE, data.total).toLocaleString()} of ${data.total.toLocaleString()}` : "0 results"}</span><div>
      <button type="button" className="md-ghost" disabled={offset === 0} onClick={() => page(Math.max(0, offset - PAGE))}>Previous instruments</button>
      <button type="button" className="md-ghost" disabled={offset + PAGE >= data.total} onClick={() => page(offset + PAGE)}>Next instruments</button>
    </div></div>
  </>;
}

function BySector({ params, update, select }: { params: URLSearchParams; update: (values: Record<string, string>) => void; select: (i: Instrument) => void }) {
  const filters = new URLSearchParams({ country: params.get("country") || "US" });
  for (const key of ["search", "active", "configured"]) if (params.get(key)) filters.set(key, params.get(key)!);
  const { data, error } = useLoad<SectorSummary>(`/hub/api/v1/market-data/sectors?${filters}`);
  const open = params.get("sector");
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const members = new URLSearchParams(filters);
  if (open) { members.set("kind", "listing"); members.set("sector", open); if (offset) members.set("offset", String(offset)); }
  const listing = useLoad<InstrumentPage>(open ? `${base}?${members}` : null);
  const groups = data ? [...data.items, ...(data.unclassified ? [{ sector_id: "none", sector: "Unclassified", count: data.unclassified }] : [])] : [];
  return <>
    <div className="instrument-filters">
      <label className="instrument-filters__search">Search symbol, company, UUID or alias<input type="search" placeholder="e.g. AAPL or Microsoft" value={params.get("search") ?? ""} onChange={e => update({ search: e.target.value, offset: "" })} /></label>
      <label>Country<input type="text" placeholder="US" value={params.get("country") ?? ""} onChange={e => update({ country: e.target.value, offset: "" })} /></label>
      {[["active", "Active status"], ["configured", "Collection membership"]].map(([key, title]) => <label key={key}>{title}<select value={params.get(key) ?? "all"} onChange={e => update({ [key]: e.target.value, offset: "" })}><option value="all">All</option><option value="yes">{key === "active" ? "Active" : "Configured"}</option><option value="no">{key === "active" ? "Inactive" : "Not configured"}</option></select></label>)}
    </div>
    {error && <p role="alert" className="notice notice--error">{error}</p>}
    {!data && !error && <p role="status" className="md-loading">Loading sectors…</p>}
    {data && <>
      <p className="md-count"><strong>{data.total.toLocaleString()}</strong> listings in <strong>{data.items.length}</strong> sectors</p>
      <Treemap label="Listings by GICS sector" height={180} items={groups.map(group => ({
        key: group.sector_id, label: group.sector, size: group.count, sublabel: group.count.toLocaleString(),
        title: `${group.sector}: ${group.count.toLocaleString()} listings`, selected: open === group.sector_id,
        onSelect: () => update({ sector: open === group.sector_id ? "" : group.sector_id, offset: "" }),
      }))} />
      {!groups.length && <p className="md-empty">No listings match these filters.</p>}
      <div className="md-sectors" role="group" aria-label="Sectors">{groups.map(group => <details key={group.sector_id} className="md-sector" open={open === group.sector_id}>
        <summary onClick={event => { event.preventDefault(); update({ sector: open === group.sector_id ? "" : group.sector_id, offset: "" }); }}>
          <span className="md-sector__name">{group.sector}</span>
          {group.sector_id !== "none" && <code className="md-sector__code">GICS {group.sector_id}</code>}
          <span className="md-sector__count">{group.count.toLocaleString()}</span>
        </summary>
        {open === group.sector_id && <div className="md-sector__body">
          {listing.error && <p role="alert" className="notice notice--error">{listing.error}</p>}
          {!listing.data && !listing.error && <p role="status" className="md-loading">Loading {group.sector} listings…</p>}
          {listing.data && <InstrumentTable data={listing.data} offset={offset} selectedId={params.get("id")} select={select} page={value => update({ offset: String(value) })} />}
        </div>}
      </details>)}</div>
    </>}
  </>;
}

export function MarketData() {
  const [query, setQuery] = useState(() => window.location.search);
  useEffect(() => { const back = () => setQuery(window.location.search); window.addEventListener("popstate", back); return () => window.removeEventListener("popstate", back); }, []);
  const params = new URLSearchParams(query);
  function update(values: Record<string, string>) {
    const next = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(values)) { if (v) next.set(k, v); else next.delete(k); }
    window.history.pushState(null, "", `${window.location.pathname}?${next}`); setQuery(`?${next}`);
  }
  const requested = params.get("view");
  const view: View = views.find(v => v.key === requested)?.key ?? "all";
  const select = (i: Instrument) => update({ kind: i.kind, id: i.id });
  const close = () => update({ kind: "", id: "" });
  const current = views.find(v => v.key === view)!;
  const kind = params.get("kind"), id = params.get("id");
  return <main className="md-page">
    <PageHeader code={current.code} title="Market data" eyebrow={current.title} info={current.intro}>
      <nav className="md-tabs" aria-label="Market Data views">{views.map(v =>
        <button key={v.key} type="button" aria-pressed={view === v.key} className={view === v.key ? "is-active" : ""}
          onClick={() => update({ view: v.key === "all" ? "" : v.key, offset: "", sector: "", group: "", root: "" })}>{v.title}</button>)}
      </nav>
    </PageHeader>
    {view === "collection" ? <CollectionCoverage /> : view === "indexes" ? <Indexes embedded /> : view === "futures" ? <Futures embedded /> : <>
      {view === "matrix" ? <DataMatrix params={params} update={update} select={select} />
        : view === "sectors" ? <BySector params={params} update={update} select={select} />
        : <AllTickers params={params} update={update} select={select} />}
      {kind && id && <Detail key={`${kind}/${id}`} kind={kind} id={id} select={select} close={close} />}
    </>}
  </main>;
}
