import { formatCompact } from "../../shared/format";
import type { SchemaMapResponse } from "../../schemaTypes";
import { areaColor, type AreaLink, type AreaSummary } from "./graph";

/** The starting point: every area as a readable card, with where its links lead. */
export function AreaOverview({ schema, areas, titleOf, onOpen, onFocus }: {
  schema: SchemaMapResponse;
  areas: { areas: AreaSummary[]; links: AreaLink[] };
  titleOf: (name: string) => string;
  onOpen: (area: string) => void;
  onFocus: (table: string) => void;
}) {
  const titles = new Map(areas.areas.map((area) => [area.id, area.title]));
  const ordered = [...areas.areas].sort((a, b) => Number(b.withData > 0) - Number(a.withData > 0) || b.rows - a.rows);
  return (
    <section className="schema-overview" aria-label="Areas of the database">
      <div className="schema-overview__intro">
        <strong>New here?</strong>
        <ol>
          <li>Pick an area below. Areas with data come first.</li>
          <li>In an area, click a table to read what it holds and what each column means.</li>
          <li>Double-click a table (or use “Focus”) to see only it and the tables it links to.</li>
        </ol>
      </div>
      <ul className="schema-overview__grid">
        {ordered.map((area) => {
          const members = schema.tables
            .filter((table) => table.namespace === area.id)
            .sort((a, b) => b.stored_rows - a.stored_rows || a.title.localeCompare(b.title));
          const main = members.filter((table) => table.stored_rows > 0 || table.kind === "view").slice(0, 5);
          const shown = main.length ? main : members.slice(0, 4);
          const outgoing = areas.links.filter((link) => link.source === area.id);
          const incoming = areas.links.filter((link) => link.target === area.id);
          return (
            <li key={area.id} className={`schema-overview__card${area.withData ? "" : " is-reserved"}`}
              style={{ "--area-color": areaColor(area.id) } as React.CSSProperties}>
              <header>
                <span className="schema-overview__id">{area.id}</span>
                <h2>{area.title}</h2>
                <p>{area.summary}</p>
              </header>
              <dl>
                <div><dt>Tables</dt><dd>{area.tables}</dd></div>
                <div><dt>With data</dt><dd>{area.withData}</dd></div>
                <div><dt>Rows</dt><dd>{area.withData ? formatCompact(area.rows) : "—"}</dd></div>
                {area.views > 0 && <div><dt>Views</dt><dd>{area.views}</dd></div>}
              </dl>
              <div className="schema-overview__tables">
                <span>{main.length ? "Main tables" : "Reserved tables (no data yet)"}</span>
                <ul>
                  {shown.map((table) => (
                    <li key={table.name}>
                      <button type="button" className="schema-link" onClick={() => onFocus(table.name)} title={table.summary}>
                        {titleOf(table.name)}
                      </button>
                      <small>{table.kind === "view" ? "view" : table.stored_rows ? `${formatCompact(table.stored_rows)} rows` : "reserved"}</small>
                    </li>
                  ))}
                </ul>
              </div>
              {(outgoing.length > 0 || incoming.length > 0) && (
                <div className="schema-overview__links">
                  {outgoing.length > 0 && (
                    <p>Refers to {outgoing.map((link, index) => (
                      <span key={link.id}>{index ? ", " : ""}<button type="button" className="schema-link" onClick={() => onOpen(link.target)}>{titles.get(link.target)}</button> ({link.count})</span>
                    ))}</p>
                  )}
                  {incoming.length > 0 && (
                    <p>Referred to by {incoming.map((link, index) => (
                      <span key={link.id}>{index ? ", " : ""}<button type="button" className="schema-link" onClick={() => onOpen(link.source)}>{titles.get(link.source)}</button> ({link.count})</span>
                    ))}</p>
                  )}
                </div>
              )}
              <button type="button" className="schema-overview__open" onClick={() => onOpen(area.id)}>
                Open {area.title} →
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
