import { useEffect, useState } from "react";
import { errorMessage, fetchJson } from "../shared/api";
import type { Dataset, DatasetResult, Identity, Instrument, InstrumentPage } from "../instrumentTypes";
import { CollectionCoverage } from "./MarketData";
import "./InstrumentExplorer.css";

const base = "/hub/api/v1/market-data/instruments";
const groups = ["Market", "Alternative & political", "Fundamentals", "Broker & portfolio", "Derived", "Reference & operations"];
const label = (i: Instrument) => `${i.symbol || i.id} · ${i.exchange}${i.kind === "contract" ? ` · ${i.expiry} ${i.strike ?? ""} ${i.right}` : ""}`;
const cell = (v: unknown) => v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
const states = { stored: "Stored data", empty: "No matching records", missing_table: "Missing table", unavailable: "Query unavailable", unsupported: "Unsupported linkage" };

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

function Grid({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return <div className="instrument-grid"><table><thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j}>{cell(v)}</td>)}</tr>)}</tbody></table></div>;
}

function Preview({ url }: { url: string }) {
  const [provider, setProvider] = useState("");
  const [start, setStart] = useState(() => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ provider, start: `${start}T00:00:00Z`, end: `${end}T23:59:59Z`, offset: String(offset), limit: "50" });
  const { data, error } = useLoad<DatasetResult>(start && end ? `${url}/rows?${params}` : null);
  return <section aria-label="Record preview">
    <p>Latest 30 days by default. Dates are UTC. Coverage above is lifetime; this preview is a separate window.</p>
    <div className="instrument-filters">
      <label>Provider<input value={provider} onChange={e => { setProvider(e.target.value); setOffset(0); }} /></label>
      <label>From<input type="date" value={start} onChange={e => { setStart(e.target.value); setOffset(0); }} /></label>
      <label>Through<input type="date" value={end} onChange={e => { setEnd(e.target.value); setOffset(0); }} /></label>
    </div>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>Loading records…</p>}
    {data && <><p>{states[data.state]} {data.message}</p><Grid columns={data.columns} rows={data.rows} />
      <button disabled={offset === 0} onClick={() => setOffset(offset - 50)}>Previous records</button>
      <button disabled={!data.has_more || offset >= 5000} onClick={() => setOffset(offset + 50)}>Next records</button>
      <p>Rows {offset + 1}–{offset + data.rows.length}. Checked {data.checked_at}</p>
      <details><summary>Recorded provenance for this page</summary>{Object.entries(data.evidence).map(([key, evidence]) =>
        <section key={key}><h5>{key}: {evidence.state}</h5><p>{evidence.message}</p>
          {evidence.table && <a href={`/data/tables/${evidence.table}`}>{evidence.table}</a>}
          {!!evidence.unavailable_ids?.length && <p>Missing or expired evidence: {evidence.unavailable_ids.join(", ")}</p>}
          {evidence.columns && <Grid columns={evidence.columns} rows={evidence.rows ?? []} />}
        </section>)}</details>
    </>}
  </section>;
}

function DatasetPanel({ dataset, coverage, error, url }: { dataset: Dataset; coverage?: DatasetResult; error?: string; url: string }) {
  const [expanded, setExpanded] = useState(false);
  return <article className="instrument-dataset">
    <h4>{dataset.title} <a href={`/data/tables/${dataset.table}`}><code>{dataset.table}</code></a></h4>
    <p>{dataset.description}</p><p>{dataset.relationships.join(", ") || "No reliable instrument link"} · {dataset.classification}</p>
    {dataset.notes.map(note => <p key={note}>{note}</p>)}
    {dataset.classification === "investigation" && <strong>Investigation candidates only — excluded from confirmed coverage.</strong>}
    {error && <p role="alert">{error}</p>}
    {coverage ? <><p><strong>{states[coverage.state]}</strong> {coverage.message} · Checked {coverage.checked_at}</p>
      {!!coverage.groups.length && <Grid columns={Object.keys(coverage.groups[0])} rows={coverage.groups.map(g => Object.values(g))} />}</>
      : dataset.relationships.length ? <p>{error ? "Coverage unavailable" : "Coverage pending…"}</p> : <p>Unsupported linkage</p>}
    {dataset.writers.map(writer => <a key={writer} className="instrument-link" href={`/data/pipelines#${writer}`}>{writer} runs</a>)}
    {!!dataset.relationships.length && <><button aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide records" : "Inspect records"}</button>
      {expanded && <Preview url={url} />}</>}
  </article>;
}

function Detail({ kind, id, select }: { kind: string; id: string; select: (i: Instrument) => void }) {
  const url = `${base}/${kind}/${id}`;
  const { data, error } = useLoad<Identity>(url);
  const [coverage, setCoverage] = useState<Record<string, DatasetResult>>({});
  const [failures, setFailures] = useState<Record<string, string>>({});
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
  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading instrument identity…</p>;
  const item = data.instrument;
  return <section aria-label="Instrument detail">
    <h2>{label(item)}</h2><p>{item.name} · {item.instrument_type} · {item.active ? "Active" : "Inactive"}</p>
    <p>Collection membership: {item.configured ? "Configured" : "Not configured"}. This does not establish stored data availability.</p>
    <dl>{["id", "listing_id", "security_id", "entity_id"].map(key => <div key={key}><dt>{key}{item.kind === "contract" && key !== "id" ? " (underlying)" : ""}</dt><dd>{cell(item[key as keyof Instrument])}</dd></div>)}</dl>
    {data.warnings.map(w => <p role="status" key={w}>{w}</p>)}
    {Object.entries(data.identifiers ?? {}).map(([table, records]) => <details key={table}><summary>{table} identity</summary>{!!records.length && <Grid columns={Object.keys(records[0])} rows={records.map(r => Object.values(r))} />}</details>)}
    <details><summary>Provider identifiers and alias history ({data.aliases.length})</summary>
      {!!data.aliases.length && <Grid columns={Object.keys(data.aliases[0])} rows={data.aliases.map(a => Object.values(a))} />}
    </details>
    <details><summary>Related listings and contracts ({data.related.length})</summary>{data.related.map(i => <button key={`${i.kind}/${i.id}`} onClick={() => select(i)}>{label(i)}</button>)}</details>
    <p>Expected series, recorded session coverage and recovery state appear under Reference &amp; operations. Run outcomes are evidence, not proof of complete coverage. Account-wide balances remain related context.</p>
    {groups.map(group => <section key={group}><h3>{group}</h3>{data.datasets.filter(d => d.group === group).map(dataset =>
      <DatasetPanel key={dataset.table} dataset={dataset} coverage={coverage[dataset.table]} error={failures[dataset.table]} url={`${url}/datasets/${dataset.table}`} />)}</section>)}
  </section>;
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
  const collection = params.get("view") === "collection";
  const inventory = new URLSearchParams();
  for (const key of ["search", "country", "exchange", "instrument_type", "active", "configured", "offset"]) if (params.has(key)) inventory.set(key, params.get(key)!);
  const { data, error } = useLoad<InstrumentPage>(collection ? null : `${base}?${inventory}`);
  const select = (i: Instrument) => update({ kind: i.kind, id: i.id });
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  return <main className="instrument-explorer"><h1>Market Data</h1>
    <nav aria-label="Market Data views"><button aria-pressed={!collection} onClick={() => update({ view: "" })}>All tickers</button><button aria-pressed={collection} onClick={() => update({ view: "collection" })}>Collection coverage</button></nav>
    {collection ? <CollectionCoverage /> : <>
      <p>Every registered listing and derivative contract, including inactive instruments and instruments without collected data.</p>
      <div className="instrument-filters">
        {[['search', 'Search symbol, company, UUID or alias'], ['country', 'Country'], ['exchange', 'Exchange'], ['instrument_type', 'Instrument type']].map(([key, title]) =>
          <label key={key}>{title}<input type={key === "search" ? "search" : "text"} value={params.get(key) ?? ""} onChange={e => update({ [key]: e.target.value, offset: "" })} /></label>)}
        {[["active", "Active status"], ["configured", "Collection membership"]].map(([key, title]) => <label key={key}>{title}<select value={params.get(key) ?? "all"} onChange={e => update({ [key]: e.target.value, offset: "" })}><option value="all">All</option><option value="yes">{key === "active" ? "Active" : "Configured"}</option><option value="no">{key === "active" ? "Inactive" : "Not configured"}</option></select></label>)}
      </div>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p>Loading instruments…</p>}
      {data && <><p>{data.total.toLocaleString()} instruments · 50 per page</p>
        <div className="instrument-grid"><table><thead><tr><th>Instrument</th><th>Company</th><th>Country / type</th><th>Status</th><th>Collection</th></tr></thead><tbody>{data.items.map(i => <tr key={`${i.kind}/${i.id}`}><td><button onClick={() => select(i)}>{label(i)}</button></td><td>{i.name}</td><td>{i.country} / {i.instrument_type}</td><td>{i.active ? "Active" : "Inactive"}</td><td>{i.configured ? "Configured" : "Not configured"}</td></tr>)}</tbody></table></div>
        {!data.items.length && <p>No instruments match these filters.</p>}
        <button disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - 50)) })}>Previous instruments</button><button disabled={offset + 50 >= data.total} onClick={() => update({ offset: String(offset + 50) })}>Next instruments</button>
      </>}
      {params.get("kind") && params.get("id") && <Detail key={`${params.get("kind")}/${params.get("id")}`} kind={params.get("kind")!} id={params.get("id")!} select={select} />}
    </>}
  </main>;
}
