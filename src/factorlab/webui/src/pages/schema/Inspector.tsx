import { StatusPill } from "../../components/ui";
import { tableHref, type CatalogTableSummary } from "../../dataTypes";
import { formatCompact, formatRange, formatRelative } from "../../shared/format";
import type { RelationshipCategory, SchemaRelationship, SchemaTable } from "../../schemaTypes";
import { CATEGORY_LABELS, areaColor, relationSentence } from "./graph";
import { shortType } from "./nodes";

function Sentence({ item, titleOf, onSelect, reverse }: {
  item: SchemaRelationship; titleOf: (name: string) => string; onSelect: (name: string) => void; reverse?: boolean;
}) {
  const sentence = relationSentence(item, titleOf);
  if (reverse) {
    return (
      <li className={`schema-sentence schema-sentence--${item.category}`}>
        <p>
          <button type="button" className="schema-link" onClick={() => onSelect(item.source.table)}>{sentence.subject}</button>
          {" "}rows point here through <code>{item.source.column}</code>
          <span className="schema-sentence__category"> · {CATEGORY_LABELS[item.category].label.toLowerCase()}</span>
        </p>
        <code>{item.source.table}.{item.source.column} → {item.target.column}</code>
      </li>
    );
  }
  return (
    <li className={`schema-sentence schema-sentence--${item.category}`}>
      <p>
        {sentence.lead} <strong>{sentence.subject}</strong> {sentence.middle}{" "}
        <button type="button" className="schema-link" onClick={() => onSelect(item.target.table)}>{sentence.object}</button>
        {sentence.optional && <span className="schema-sentence__optional"> (can be empty)</span>}.
      </p>
      <code>{sentence.via}</code>
    </li>
  );
}

const ORDER: RelationshipCategory[] = ["identity", "lookup", "lineage"];

export function Inspector({
  table, relationships, catalog, titleOf, onSelect, onFocus, onToggleView, inView, onClose,
}: {
  table: SchemaTable;
  relationships: SchemaRelationship[];
  catalog: CatalogTableSummary | undefined;
  titleOf: (name: string) => string;
  onSelect: (name: string) => void;
  onFocus: (name: string) => void;
  onToggleView: (name: string) => void;
  inView: boolean;
  onClose: () => void;
}) {
  const uses = relationships.filter((item) => item.source.table === table.name);
  const usedBy = relationships.filter((item) => item.target.table === table.name && item.source.table !== table.name);
  const linkTarget = new Map(uses.map((item) => [item.source.column, item.target.table]));
  const referenced = new Set(usedBy.map((item) => item.target.column));
  return (
    <aside className="schema-inspector" aria-label={`${table.title} details`} style={{ "--area-color": areaColor(table.namespace) } as React.CSSProperties}>
      <header>
        <div>
          <span className="eyebrow">{table.kind === "view" ? "View" : "Table"} · {table.domain}</span>
          <h2>{table.title}</h2>
          <code>{table.name}</code>
        </div>
        <button type="button" className="schema-inspector__close" onClick={onClose} aria-label="Close details">×</button>
      </header>
      <div className="schema-inspector__body">
        <p className="schema-inspector__summary">{table.summary}</p>
        <div className="schema-inspector__actions">
          <button type="button" onClick={() => onFocus(table.name)}>Focus on this table</button>
          <button type="button" className="button-secondary" onClick={() => onToggleView(table.name)}>
            {inView ? "Remove from my view" : "Add to my view"}
          </button>
          {table.kind === "table" && <a className="button-secondary schema-inspector__open" href={tableHref(table.name)}>Open in data catalog</a>}
        </div>

        <dl className="schema-inspector__facts">
          <div><dt>Rows</dt><dd>{table.kind === "view" ? "View" : table.stored_rows ? formatCompact(table.stored_rows) : "Reserved · no data yet"}</dd></div>
          <div><dt>Covers</dt><dd>{catalog ? formatRange(catalog.first_data_at, catalog.last_data_at) : "—"}</dd></div>
          <div><dt>Updated</dt><dd>{catalog?.last_ingested_at ? formatRelative(catalog.last_ingested_at) : "—"}</dd></div>
          <div><dt>Health</dt><dd>{catalog && table.kind === "table" ? <StatusPill status={catalog.status} /> : "—"}</dd></div>
        </dl>

        {table.notes.length > 0 && (
          <section>
            <h3>Good to know</h3>
            <ul className="schema-bullets">{table.notes.map((note) => <li key={note}>{note}</li>)}</ul>
          </section>
        )}

        <section>
          <h3>How it connects</h3>
          {!uses.length && !usedBy.length && <p className="data-muted">No links to other tables in the link types shown.</p>}
          {uses.length > 0 && (
            <>
              <h4>Uses</h4>
              {ORDER.map((category) => {
                const items = uses.filter((item) => item.category === category);
                return items.length ? (
                  <div key={category} className="schema-sentence-group">
                    <span>{CATEGORY_LABELS[category].label}</span>
                    <ul>{items.map((item) => <Sentence key={item.id} item={item} titleOf={titleOf} onSelect={onSelect} />)}</ul>
                  </div>
                ) : null;
              })}
            </>
          )}
          {usedBy.length > 0 && (
            <>
              <h4>Used by</h4>
              <ul>{usedBy.map((item) => <Sentence key={item.id} item={item} titleOf={titleOf} onSelect={onSelect} reverse />)}</ul>
            </>
          )}
        </section>

        <section>
          <h3>Columns <span>{table.columns.length}</span></h3>
          <ol className="schema-inspector__columns">
            {table.columns.map((column) => (
              <li key={column.name}>
                <div>
                  <code>{column.name}</code>
                  <span>{shortType(column.type)}{column.nullable ? " · optional" : ""}</span>
                </div>
                <p>
                  {column.description ?? <span className="data-muted">No description yet.</span>}
                  {linkTarget.has(column.name) && (
                    <> <button type="button" className="schema-link" onClick={() => onSelect(linkTarget.get(column.name)!)}>
                      → {titleOf(linkTarget.get(column.name)!)}
                    </button></>
                  )}
                </p>
                <div className="schema-inspector__badges">
                  {(column.in_primary_key || column.in_sorting_key) && <b title="Part of the key rows are stored and de-duplicated by">key</b>}
                  {column.in_partition_key && <b title="Used to split storage into partitions">partition</b>}
                  {referenced.has(column.name) && <b title="Other tables point to this column">referenced</b>}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </aside>
  );
}
