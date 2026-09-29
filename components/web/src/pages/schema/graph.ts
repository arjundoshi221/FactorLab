import type {
  RelationshipCategory,
  SchemaMapResponse,
  SchemaRelationship,
  SchemaTable,
} from "../../schemaTypes";

export const CATEGORY_LABELS: Record<RelationshipCategory, { label: string; hint: string }> = {
  identity: { label: "Business links", hint: "What each row is about: listings, contracts, companies, filings." },
  lineage: { label: "Lineage", hint: "Where a row came from: the raw response or collection run behind it." },
  lookup: { label: "Shared codes", hint: "Country, currency, exchange, and source codes shared by many tables." },
};

export const AREA_COLORS: Record<string, string> = {
  ref: "#b8f35a",
  market: "#74a8ff",
  alt: "#f2c66d",
  meta: "#82d8e8",
  raw: "#c49aff",
  research: "#9fe6b8",
  fundamentals: "#ffb86b",
  derived: "#8fb4ff",
  broker: "#5dd69b",
  book: "#ff8179",
  risk: "#ff5f73",
};

export function areaColor(area: string): string {
  return AREA_COLORS[area] ?? "#9ba5ae";
}

export type SchemaView =
  | { kind: "areas" }
  | { kind: "area"; area: string; context: boolean }
  | { kind: "table"; table: string; depth: 1 | 2 }
  | { kind: "custom"; tables: string[]; neighbours: boolean }
  | { kind: "all" };

export interface ViewGraph {
  /** Tables drawn, in a stable order. */
  tables: SchemaTable[];
  /** Tables drawn only as context (neighbours outside the chosen set). */
  faded: Set<string>;
  relationships: SchemaRelationship[];
  focus: string | null;
  /** Tables elsewhere that point into this view but were left out to keep it readable. */
  skippedReferrers: number;
}

export function visibleRelationships(
  relationships: SchemaRelationship[], categories: ReadonlySet<RelationshipCategory>,
): SchemaRelationship[] {
  return relationships.filter((item) => categories.has(item.category));
}

function adjacency(relationships: SchemaRelationship[]): Map<string, Set<string>> {
  const graph = new Map<string, Set<string>>();
  const link = (from: string, to: string) => {
    if (!graph.has(from)) graph.set(from, new Set());
    graph.get(from)!.add(to);
  };
  relationships.forEach((item) => {
    link(item.source.table, item.target.table);
    link(item.target.table, item.source.table);
  });
  return graph;
}

/**
 * Tables within ``depth`` links of ``start``. The first step follows every link; later steps
 * follow what each table refers to, and its referrers only when there are a handful, so a hub
 * like Listings does not pull in half the database.
 */
export function neighbourhood(start: string, depth: number, relationships: SchemaRelationship[]): Set<string> {
  return neighbourhoodWithSkipped(start, depth, relationships).tables;
}

export function neighbourhoodWithSkipped(
  start: string, depth: number, relationships: SchemaRelationship[],
): { tables: Set<string>; skipped: number } {
  const graph = adjacency(relationships);
  const refersTo = new Map<string, Set<string>>();
  const referredBy = new Map<string, Set<string>>();
  relationships.forEach((item) => {
    if (!refersTo.has(item.source.table)) refersTo.set(item.source.table, new Set());
    refersTo.get(item.source.table)!.add(item.target.table);
    if (!referredBy.has(item.target.table)) referredBy.set(item.target.table, new Set());
    referredBy.get(item.target.table)!.add(item.source.table);
  });
  const seen = new Set([start]);
  const skipped = new Set<string>();
  let frontier = [start];
  for (let step = 0; step < depth; step += 1) {
    const next: string[] = [];
    frontier.forEach((table) => {
      const referrers = [...(referredBy.get(table) ?? [])];
      const candidates = step === 0
        ? [...(graph.get(table) ?? [])]
        : [...(refersTo.get(table) ?? []), ...(referrers.length <= MAX_CONTEXT_REFERRERS ? referrers : [])];
      if (step > 0 && referrers.length > MAX_CONTEXT_REFERRERS) referrers.forEach((name) => { if (!seen.has(name)) skipped.add(name); });
      candidates.forEach((other) => {
        if (!seen.has(other)) {
          seen.add(other);
          next.push(other);
        }
      });
    });
    frontier = next;
  }
  return { tables: seen, skipped: [...skipped].filter((name) => !seen.has(name)).length };
}

/** Context tables beyond this many referrers are summarized instead of drawn. */
export const MAX_CONTEXT_REFERRERS = 6;

function neighboursOf(core: Set<string>, relationships: SchemaRelationship[]): { tables: Set<string>; skipped: number } {
  const referenced = new Set<string>();
  const referrers = new Set<string>();
  relationships.forEach((item) => {
    if (core.has(item.source.table) && !core.has(item.target.table)) referenced.add(item.target.table);
    if (core.has(item.target.table) && !core.has(item.source.table)) referrers.add(item.source.table);
  });
  // What an area refers to always helps explain it; a dimension table like Listings can be
  // referenced by dozens of tables, which would bury the area itself.
  const drawn = referrers.size <= MAX_CONTEXT_REFERRERS ? referrers : new Set<string>();
  const tables = new Set([...referenced, ...drawn]);
  return { tables, skipped: [...referrers].filter((name) => !tables.has(name)).length };
}

/** The tables, context tables, and links a view draws. */
export function viewGraph(view: SchemaView, schema: SchemaMapResponse, relationships: SchemaRelationship[]): ViewGraph {
  const byName = new Map(schema.tables.map((table) => [table.name, table]));
  let core = new Set<string>();
  let faded = new Set<string>();
  let skippedReferrers = 0;
  let focus: string | null = null;
  if (view.kind === "all") core = new Set(byName.keys());
  if (view.kind === "area") {
    core = new Set(schema.tables.filter((table) => table.namespace === view.area).map((table) => table.name));
    if (view.context) ({ tables: faded, skipped: skippedReferrers } = neighboursOf(core, relationships));
  }
  if (view.kind === "table" && byName.has(view.table)) {
    focus = view.table;
    ({ tables: core, skipped: skippedReferrers } = neighbourhoodWithSkipped(view.table, view.depth, relationships));
  }
  if (view.kind === "custom") {
    core = new Set(view.tables.filter((name) => byName.has(name)));
    if (view.neighbours) ({ tables: faded, skipped: skippedReferrers } = neighboursOf(core, relationships));
  }
  const drawn = new Set([...core, ...faded]);
  return {
    tables: schema.tables.filter((table) => drawn.has(table.name)),
    faded,
    // Context tables are shown only for their links into the chosen set.
    relationships: relationships.filter((item) => drawn.has(item.source.table) && drawn.has(item.target.table)
      && (core.has(item.source.table) || core.has(item.target.table))),
    focus,
    skippedReferrers,
  };
}

export interface AreaSummary {
  id: string;
  title: string;
  summary: string;
  tables: number;
  views: number;
  withData: number;
  rows: number;
}

export interface AreaLink {
  id: string;
  source: string;
  target: string;
  count: number;
  columns: string[];
}

/** Areas as nodes, with the number of table-to-table links between each pair. */
export function areaGraph(schema: SchemaMapResponse, relationships: SchemaRelationship[]): { areas: AreaSummary[]; links: AreaLink[] } {
  const areas = schema.areas.map((area) => {
    const members = schema.tables.filter((table) => table.namespace === area.id);
    const stored = members.filter((table) => table.kind === "table");
    return {
      ...area,
      tables: stored.length,
      views: members.length - stored.length,
      withData: stored.filter((table) => table.stored_rows > 0).length,
      rows: stored.reduce((sum, table) => sum + table.stored_rows, 0),
    };
  });
  const namespaceOf = new Map(schema.tables.map((table) => [table.name, table.namespace]));
  const links = new Map<string, AreaLink>();
  relationships.forEach((item) => {
    const source = namespaceOf.get(item.source.table);
    const target = namespaceOf.get(item.target.table);
    if (!source || !target || source === target) return;
    const id = `${source}->${target}`;
    const link = links.get(id) ?? { id, source, target, count: 0, columns: [] };
    link.count += 1;
    if (!link.columns.includes(item.source.column)) link.columns.push(item.source.column);
    links.set(id, link);
  });
  return { areas, links: [...links.values()].sort((a, b) => b.count - a.count) };
}

export interface LinkedColumns {
  outgoing: Map<string, SchemaRelationship>;
  incoming: Map<string, SchemaRelationship[]>;
}

export function linkedColumns(table: string, relationships: SchemaRelationship[]): LinkedColumns {
  const outgoing = new Map<string, SchemaRelationship>();
  const incoming = new Map<string, SchemaRelationship[]>();
  relationships.forEach((item) => {
    if (item.source.table === table) outgoing.set(item.source.column, item);
    if (item.target.table === table) incoming.set(item.target.column, [...(incoming.get(item.target.column) ?? []), item]);
  });
  return { outgoing, incoming };
}

export interface Sentence {
  lead: string;
  subject: string;
  middle: string;
  object: string;
  via: string;
  optional: boolean;
}

/** Explain one link in words, e.g. "Each Price bars row refers to one Listings row". */
export function relationSentence(item: SchemaRelationship, titleOf: (name: string) => string): Sentence {
  const subject = titleOf(item.source.table);
  const object = titleOf(item.target.table);
  const via = `${item.source.column} → ${item.target.table}.${item.target.column}`;
  const middle = item.category === "lineage"
    ? item.source.column === "raw_id" ? "row keeps the raw response it was parsed from in"
      : item.source.column === "ingest_run_id" ? "row names the collection run that wrote it in"
      : "row names the computation that produced it in"
    : item.category === "lookup" ? "row uses a shared code defined in" : "row refers to one row in";
  return { lead: "Each", subject, middle, object, via, optional: item.optional };
}

export interface SearchHit {
  table: SchemaTable;
  score: number;
  columns: { name: string; description: string | null }[];
}

export function searchSchema(tables: SchemaTable[], query: string): SearchHit[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const hits: SearchHit[] = [];
  for (const table of tables) {
    const heading = `${table.name} ${table.title}`.toLowerCase();
    const summary = table.summary.toLowerCase();
    let score = 0;
    const matched = new Map<string, string | null>();
    let every = true;
    for (const term of terms) {
      const columns = table.columns.filter((column) =>
        column.name.toLowerCase().includes(term) || (column.description ?? "").toLowerCase().includes(term));
      if (heading.includes(term)) score += 4;
      else if (summary.includes(term)) score += 2;
      else if (columns.length) score += 1;
      else every = false;
      columns.slice(0, 3).forEach((column) => matched.set(column.name, column.description ?? null));
    }
    if (every) hits.push({ table, score, columns: [...matched].map(([name, description]) => ({ name, description })) });
  }
  return hits.sort((a, b) => b.score - a.score || b.table.stored_rows - a.table.stored_rows).slice(0, 25);
}

/** The columns a compact card lists: identifiers, keys, and anything that links out or in. */
export function keyColumns(table: SchemaTable, linked: LinkedColumns): SchemaTable["columns"] {
  return table.columns.filter((column) => column.in_primary_key || column.in_sorting_key
    || linked.outgoing.has(column.name) || linked.incoming.has(column.name));
}
