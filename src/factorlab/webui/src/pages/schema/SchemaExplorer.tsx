import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type ReactFlowInstance,
  useNodesInitialized,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorBanner, Skeleton } from "../../components/ui";
import type { CatalogIndex } from "../../dataTypes";
import { downloadSchemaExport } from "../../schemaExport";
import type { RelationshipCategory, SchemaMapResponse, SchemaRelationship } from "../../schemaTypes";
import { errorMessage, useRemote } from "../../shared/api";
import { formatCompact } from "../../shared/format";
import { useUrlParams } from "../../shared/urlState";
import {
  CATEGORY_LABELS, areaColor, areaGraph, linkedColumns, searchSchema, viewGraph, visibleRelationships,
  type SchemaView,
} from "./graph";
import { Inspector } from "./Inspector";
import { layoutFlat, layoutGrouped } from "./layout";
import { AreaOverview } from "./AreaOverview";
import { CARD_WIDTH, MINI_HEIGHT, cardHeight, nodeTypes, type TableCardData } from "./nodes";

const FIT = { padding: 0.14, minZoom: 0.2, maxZoom: 1.1, duration: 300 } as const;
const CATEGORIES: RelationshipCategory[] = ["identity", "lookup", "lineage"];
const EDGE_STYLE: Record<RelationshipCategory, { stroke: string; dash?: string }> = {
  identity: { stroke: "#9fb0bd" },
  lookup: { stroke: "#6d7a86", dash: "2 4" },
  lineage: { stroke: "#a386d6", dash: "6 4" },
};

function readView(params: URLSearchParams): SchemaView {
  const table = params.get("table");
  if (table) return { kind: "table", table, depth: params.get("depth") === "2" ? 2 : 1 };
  const area = params.get("area");
  if (area) return { kind: "area", area, context: params.get("context") !== "0" };
  const tables = params.get("tables");
  if (tables) return { kind: "custom", tables: tables.split(",").filter(Boolean), neighbours: params.get("nb") === "1" };
  if (params.get("view") === "all") return { kind: "all" };
  return { kind: "areas" };
}

function readCategories(params: URLSearchParams): Set<RelationshipCategory> {
  const raw = params.get("links");
  if (!raw) return new Set(["identity"]);
  return new Set(raw.split(",").filter((item): item is RelationshipCategory => CATEGORIES.includes(item as RelationshipCategory)));
}

function Legend() {
  return (
    <details className="schema-legend">
      <summary>How to read this</summary>
      <div>
        <p><strong>Cards are tables.</strong> The bold name says what the table holds; the code name below is what you query.</p>
        <p><strong>Rows under a card are columns.</strong> By default only key columns and columns that link to other tables are shown; each has a one-line description.</p>
        <p><strong>Lines are links.</strong> A line runs from a column to the table it refers to, like <code>listing_id</code> → Listings.</p>
        <ul>
          <li><span className="schema-legend__line schema-legend__line--identity" /> Business link: what a row is about.</li>
          <li><span className="schema-legend__line schema-legend__line--lookup" /> Shared code: country, currency, exchange, or source.</li>
          <li><span className="schema-legend__line schema-legend__line--lineage" /> Lineage: the raw response or collection run behind a row.</li>
        </ul>
        <p><span className="schema-badge schema-badge--reserved">Reserved · no data yet</span> tables are designed but not collected yet.</p>
        <p>Click a card for details; double-click to focus on it and its neighbours.</p>
      </div>
    </details>
  );
}

function ExplorerCanvas({ schema, catalog }: { schema: SchemaMapResponse; catalog: CatalogIndex | null }) {
  const [params, setParams] = useUrlParams();
  const view = readView(params);
  const categories = readCategories(params);
  const selected = params.get("sel");
  const expandAll = params.get("cols") === "all";
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [instance, setInstance] = useState<ReactFlowInstance | null>(null);
  const [laying, setLaying] = useState(true);
  const boardRef = useRef<HTMLDivElement>(null);

  const byName = useMemo(() => new Map(schema.tables.map((table) => [table.name, table])), [schema]);
  const titleOf = useCallback((name: string) => byName.get(name)?.title ?? name, [byName]);
  const catalogByName = useMemo(() => new Map((catalog?.tables ?? []).map((table) => [table.name, table])), [catalog]);
  const relationships = useMemo(() => visibleRelationships(schema.relationships, categories),
    [schema, [...categories].sort().join(",")]);
  const graph = useMemo(() => view.kind === "areas" ? null : viewGraph(view, schema, relationships),
    [schema, relationships, JSON.stringify(view)]);
  const areas = useMemo(() => areaGraph(schema, relationships), [schema, relationships]);
  const myView = useMemo(() => (params.get("tables") ?? "").split(",").filter((name) => byName.has(name)), [params, byName]);

  const update = useCallback((change: (next: URLSearchParams) => void, push = true) => setParams(change, push), [setParams]);
  const goTo = useCallback((change: (next: URLSearchParams) => void) => update((next) => {
    ["area", "table", "depth", "view", "context", "nb", "sel"].forEach((key) => next.delete(key));
    change(next);
  }), [update]);
  const openArea = useCallback((area: string) => goTo((next) => next.set("area", area)), [goTo]);
  const focusTable = useCallback((table: string) => goTo((next) => {
    next.delete("tables");
    next.set("table", table);
    next.set("sel", table);
  }), [goTo]);
  const select = useCallback((table: string | null) => update((next) => {
    if (table) next.set("sel", table); else next.delete("sel");
  }, false), [update]);
  const toggleMyView = useCallback((table: string) => update((next) => {
    const current = (next.get("tables") ?? "").split(",").filter(Boolean);
    const updated = current.includes(table) ? current.filter((name) => name !== table) : [...current, table];
    if (updated.length) next.set("tables", updated.join(",")); else next.delete("tables");
    if (!next.get("table") && !next.get("area") && next.get("view") !== "all" && !updated.length) next.delete("nb");
  }, false), [update]);
  const toggleExpanded = useCallback((name: string) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  }), []);

  // Lay out whichever view is active.
  useEffect(() => {
    let cancelled = false;
    setLaying(true);
    (async () => {
      if (view.kind === "areas") {
        // The overview is a card grid, not a canvas.
        setNodes([]);
        setEdges([]);
      } else if (graph) {
        const sizes = new Map<string, { width: number; height: number }>();
        const cards: Node[] = graph.tables.map((table) => {
          const linked = linkedColumns(table.name, graph.relationships);
          const faded = graph.faded.has(table.name);
          const isExpanded = !faded && (expandAll || expanded.has(table.name));
          const mini = view.kind === "all";
          const data: TableCardData = {
            table, linked, expanded: isExpanded, faded, focus: graph.focus === table.name, dimmed: false, mini,
            titleOf, onToggle: toggleExpanded,
          };
          sizes.set(table.name, { width: CARD_WIDTH, height: mini ? MINI_HEIGHT : cardHeight(table, linked, isExpanded, faded) });
          return { id: table.name, type: "tableCard", position: { x: 0, y: 0 }, data };
        });
        const tableEdges: Edge[] = graph.relationships.filter((item) => item.source.table !== item.target.table).map((item: SchemaRelationship) => ({
          id: item.id, source: item.source.table, target: item.target.table,
          // Full-map cards have one handle per side; focused views attach links to the column rows.
          ...(view.kind === "all" ? {} : { sourceHandle: `s:${item.source.column}`, targetHandle: `t:${item.target.column}`, label: item.source.column }),
          type: "smoothstep", data: { category: item.category },
          markerEnd: { type: MarkerType.ArrowClosed, width: 13, height: 13 },
          labelStyle: { fill: "#aeb7be", fontSize: 9, fontFamily: "var(--mono)" },
          labelBgStyle: { fill: "#0f1418", fillOpacity: 0.95 },
        }));
        if (view.kind === "all") {
          const namespaces = schema.areas.filter((area) => cards.some((card) => (card.data as TableCardData).table.namespace === area.id));
          const namespaceOf = new Map(graph.tables.map((table) => [table.name, table.namespace]));
          const groupLinks = new Map<string, { id: string; source: string; target: string }>();
          graph.relationships.forEach((item) => {
            const source = namespaceOf.get(item.source.table);
            const target = namespaceOf.get(item.target.table);
            if (source && target && source !== target) groupLinks.set(`${source}->${target}`, { id: `${source}->${target}`, source, target });
          });
          const placed = await layoutGrouped(
            namespaces.map((area) => ({
              id: `group:${area.id}`,
              boxes: cards.filter((card) => (card.data as TableCardData).table.namespace === area.id)
                .map((card) => ({ id: card.id, ...sizes.get(card.id)! })),
            })),
            [...groupLinks.values()].map((link) => ({ ...link, source: `group:${link.source}`, target: `group:${link.target}` })),
          );
          if (cancelled) return;
          const groups: Node[] = namespaces.map((area) => {
            const box = placed.groups.get(`group:${area.id}`);
            return {
              id: `group:${area.id}`, type: "areaGroup", position: { x: box?.x ?? 0, y: box?.y ?? 0 },
              data: { title: area.title, area: area.id, count: cards.filter((card) => (card.data as TableCardData).table.namespace === area.id).length },
              style: { width: box?.width ?? 400, height: box?.height ?? 300 }, selectable: false, draggable: false, zIndex: -1,
            };
          });
          setNodes([...groups, ...cards.map((card) => ({
            ...card, parentId: `group:${(card.data as TableCardData).table.namespace}`,
            position: { x: placed.nodes.get(card.id)?.x ?? 0, y: placed.nodes.get(card.id)?.y ?? 0 },
          }))]);
        } else {
          const box = boardRef.current?.querySelector(".schema-workspace")?.getBoundingClientRect();
          const placed = await layoutFlat(cards.map((card) => ({ id: card.id, ...sizes.get(card.id)! })), tableEdges,
            box && box.width > 0 ? { width: box.width, height: box.height } : undefined);
          if (cancelled) return;
          setNodes(cards.map((card) => ({ ...card, position: { x: placed.get(card.id)?.x ?? 0, y: placed.get(card.id)?.y ?? 0 } })));
        }
        setEdges(tableEdges);
      }
      if (!cancelled) {
        setLaying(false);
        setLayoutKey((key) => key + 1);
      }
    })();
    return () => { cancelled = true; };
  }, [areas, graph, view.kind, expandAll, expanded, schema, titleOf, toggleExpanded, openArea, setNodes]);

  // Re-fit after each layout; a node expanding in place keeps the current viewport.
  const [layoutKey, setLayoutKey] = useState(0);
  const lastFit = useRef("");
  const fitTarget = `${JSON.stringify(view)}|${[...categories].sort().join(",")}`;
  const measured = useNodesInitialized();
  useEffect(() => {
    // Fit only once the canvas has measured every new card, or the last ones end up off-screen.
    if (!instance || !layoutKey || !measured || lastFit.current === fitTarget) return;
    const timer = window.setTimeout(() => {
      lastFit.current = fitTarget;
      void instance.fitView(FIT);
    }, 30);
    return () => window.clearTimeout(timer);
  }, [layoutKey, instance, fitTarget, measured]);

  // Highlight the hovered or selected table's links and fade the rest.
  const active = hovered ?? selected;
  // Hovering fades unrelated cards; a selection only highlights its links so the view stays readable.
  const touching = useMemo(() => {
    if (!hovered) return null;
    const names = new Set([hovered]);
    edges.forEach((edge) => {
      if (edge.source === hovered) names.add(edge.target);
      if (edge.target === hovered) names.add(edge.source);
    });
    return names;
  }, [hovered, edges]);
  const displayEdges = useMemo(() => edges.map((edge) => {
    const category = ((edge.data as { category?: RelationshipCategory } | undefined)?.category ?? "identity");
    const lit = active !== null && (edge.source === active || edge.target === active);
    const style = view.kind === "areas" ? edge.style : {
      stroke: lit ? "#b8f35a" : EDGE_STYLE[category].stroke,
      strokeDasharray: EDGE_STYLE[category].dash,
      strokeWidth: lit ? 2 : 1.3,
    };
    return { ...edge, style: { ...style, opacity: hovered && !lit ? 0.12 : 1 }, zIndex: lit ? 10 : 0, animated: lit && category === "identity" };
  }), [edges, active, hovered, view.kind]);
  const displayNodes = useMemo(() => nodes.map((node) => node.type === "areaGroup" ? node : {
    ...node, selected: node.id === selected,
    className: touching && !touching.has(node.id) ? "is-dimmed" : undefined,
  }), [nodes, touching, selected]);

  const hits = useMemo(() => searchSchema(schema.tables, query), [schema, query]);
  const selectedTable = selected ? byName.get(selected) : undefined;
  const counts = { tables: schema.tables.filter((table) => table.kind === "table").length,
    withData: schema.tables.filter((table) => table.kind === "table" && table.stored_rows > 0).length };

  const heading = view.kind === "areas" ? { eyebrow: "Schema explorer", title: "How FactorLab's data fits together",
    text: "The database is split into areas. Each card says what an area holds, its main tables, and which areas it refers to. Open one to see its tables as a diagram, or search for any table or column." }
    : view.kind === "area" ? { eyebrow: `Area · ${view.area}`, title: schema.areas.find((area) => area.id === view.area)?.title ?? view.area,
      text: schema.areas.find((area) => area.id === view.area)?.summary ?? "" }
    : view.kind === "table" ? { eyebrow: "Focus", title: titleOf(view.table),
      text: `${byName.get(view.table)?.summary ?? ""} Showing the tables ${view.depth === 1 ? "directly linked to it" : "up to two links away"}.` }
    : view.kind === "custom" ? { eyebrow: "My view", title: `${myView.length} chosen ${myView.length === 1 ? "table" : "tables"}`,
      text: "Only the tables you picked and the links between them. Add tables from search or from any table's details." }
    : { eyebrow: "Full map", title: "Every table, grouped by area", text: "All tables at once. Use it to get a sense of scale; open an area or focus on a table to read it." };

  function exportView(format: "markdown" | "json") {
    const tables = view.kind === "areas" ? schema.tables : graph?.tables ?? [];
    const names = new Set(tables.map((table) => table.name));
    downloadSchemaExport({
      label: heading.title,
      tables,
      relationships: relationships.filter((item) => names.has(item.source.table) && names.has(item.target.table)),
      titleOf,
    }, format);
  }

  return (
    <div className="schema-explorer" ref={boardRef}>
      <aside className="schema-rail" aria-label="Browse the schema">
        <label className="schema-search">
          <span className="sr-only">Search tables and columns</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tables or columns, e.g. ticker, listing" />
        </label>
        {query ? (
          <section aria-label="Search results">
            <h3>{hits.length ? `${hits.length} matches` : "No matches"}</h3>
            <ul className="schema-hits">
              {hits.map((hit) => (
                <li key={hit.table.name} style={{ "--area-color": areaColor(hit.table.namespace) } as React.CSSProperties}>
                  <button type="button" onClick={() => focusTable(hit.table.name)}>
                    <strong>{hit.table.title}</strong><code>{hit.table.name}</code>
                  </button>
                  {hit.columns.map((column) => (
                    <small key={column.name}><code>{column.name}</code>{column.description ? ` — ${column.description}` : ""}</small>
                  ))}
                  <button type="button" className="schema-link" onClick={() => toggleMyView(hit.table.name)}>
                    {myView.includes(hit.table.name) ? "In my view ✓" : "+ Add to my view"}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <section aria-label="Areas">
            <h3>Areas</h3>
            <ul className="schema-area-list">
              {areas.areas.map((area) => (
                <li key={area.id}>
                  <button type="button" aria-current={view.kind === "area" && view.area === area.id ? "true" : undefined}
                    style={{ "--area-color": areaColor(area.id) } as React.CSSProperties} onClick={() => openArea(area.id)}>
                    <strong>{area.title}</strong>
                    <small>{area.withData}/{area.tables} tables with data{area.views ? ` · ${area.views} views` : ""}</small>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section aria-label="My view" className="schema-my-view">
          <h3>My view <span>{myView.length}</span></h3>
          {myView.length ? (
            <>
              <ul>
                {myView.map((name) => (
                  <li key={name}>
                    <button type="button" className="schema-link" onClick={() => select(name)}>{titleOf(name)}</button>
                    <button type="button" className="schema-remove" aria-label={`Remove ${titleOf(name)} from my view`} onClick={() => toggleMyView(name)}>×</button>
                  </li>
                ))}
              </ul>
              <button type="button" onClick={() => goTo((next) => next.set("tables", myView.join(",")))}>Show my view</button>
            </>
          ) : <p className="data-muted">Pick tables with “Add to my view” to build your own diagram.</p>}
        </section>
      </aside>

      <section className="schema-main">
        <nav className="data-breadcrumbs schema-crumbs" aria-label="Breadcrumb">
          <button type="button" className="schema-link" onClick={() => goTo(() => undefined)}>All areas</button>
          {view.kind === "area" && <> / <span>{heading.title}</span></>}
          {view.kind === "table" && <> / <button type="button" className="schema-link" onClick={() => openArea(byName.get(view.table)?.namespace ?? "")}>{schema.areas.find((area) => area.id === byName.get(view.table)?.namespace)?.title}</button> / <span>{heading.title}</span></>}
          {view.kind === "custom" && <> / <span>My view</span></>}
          {view.kind === "all" && <> / <span>Full map</span></>}
        </nav>
        <header className="schema-heading">
          <div>
            <span className="eyebrow">{heading.eyebrow}</span>
            <h1>{heading.title}</h1>
            <p>{heading.text}</p>
          </div>
          <dl className="schema-heading__stats">
            <div><dt>Tables</dt><dd>{counts.tables}</dd></div>
            <div><dt>With data</dt><dd>{counts.withData}</dd></div>
            <div><dt>Areas</dt><dd>{schema.areas.length}</dd></div>
            {graph && <div><dt>Shown</dt><dd>{graph.tables.length}</dd></div>}
          </dl>
        </header>
        {schema.warnings.map((warning) => <div className="notice" key={warning}>{warning}</div>)}
        {graph && graph.skippedReferrers > 0 && (
          <p className="schema-note" role="status">
            {graph.skippedReferrers} more tables link in through widely shared tables (such as Listings) and are left out to keep this readable.
            {" "}Click one of those shared tables and choose “Focus on this table” to see everything that uses it.
          </p>
        )}

        <div className="schema-toolbar" role="toolbar" aria-label="Diagram options">
          <div className="segmented" role="group" aria-label="View">
            <button type="button" className={view.kind === "areas" || view.kind === "area" ? "is-active" : ""} aria-pressed={view.kind === "areas" || view.kind === "area"} onClick={() => goTo(() => undefined)}>Areas</button>
            <button type="button" className={view.kind === "custom" ? "is-active" : ""} aria-pressed={view.kind === "custom"} disabled={!myView.length}
              onClick={() => goTo((next) => next.set("tables", myView.join(",")))}>My view</button>
            <button type="button" className={view.kind === "all" ? "is-active" : ""} aria-pressed={view.kind === "all"} onClick={() => goTo((next) => next.set("view", "all"))}>Full map</button>
          </div>
          {view.kind === "table" && (
            <div className="segmented" role="group" aria-label="Neighbourhood">
              {([1, 2] as const).map((depth) => (
                <button key={depth} type="button" className={view.depth === depth ? "is-active" : ""} aria-pressed={view.depth === depth}
                  onClick={() => update((next) => next.set("depth", String(depth)), false)}>{depth === 1 ? "Direct links" : "Two steps"}</button>
              ))}
            </div>
          )}
          {view.kind === "area" && (
            <label className="schema-check"><input type="checkbox" checked={view.context}
              onChange={(event) => update((next) => { if (event.target.checked) next.delete("context"); else next.set("context", "0"); }, false)} />
              Show linked tables from other areas</label>
          )}
          {view.kind === "custom" && (
            <label className="schema-check"><input type="checkbox" checked={view.neighbours}
              onChange={(event) => update((next) => { if (event.target.checked) next.set("nb", "1"); else next.delete("nb"); }, false)} />
              Include their neighbours</label>
          )}
          <div className="schema-links" role="group" aria-label="Link types">
            {CATEGORIES.map((category) => (
              <label key={category} className={`schema-chip schema-chip--${category}`} title={CATEGORY_LABELS[category].hint}>
                <input type="checkbox" checked={categories.has(category)} onChange={() => update((next) => {
                  const current = readCategories(next);
                  if (current.has(category)) current.delete(category); else current.add(category);
                  next.set("links", [...current].join(",") || "none");
                }, false)} />
                {CATEGORY_LABELS[category].label}
              </label>
            ))}
          </div>
          <span className="schema-toolbar__spacer" />
          {view.kind !== "areas" && (
            <button type="button" className="button-secondary" onClick={() => update((next) => { if (expandAll) next.delete("cols"); else next.set("cols", "all"); }, false)}>
              {expandAll ? "Key columns only" : "All columns"}
            </button>
          )}
          {view.kind !== "areas" && <button type="button" className="button-secondary" onClick={() => void instance?.fitView(FIT)}>Fit</button>}
          {view.kind !== "areas" && <button type="button" className="button-secondary" onClick={() => {
            if (document.fullscreenElement) void document.exitFullscreen();
            else void boardRef.current?.requestFullscreen?.();
          }}>Fullscreen</button>}
          <details className="schema-export">
            <summary className="button-secondary">Export</summary>
            <div>
              <button type="button" onClick={() => exportView("markdown")}>Markdown (this view)</button>
              <button type="button" onClick={() => exportView("json")}>JSON (this view)</button>
            </div>
          </details>
        </div>

        {view.kind === "areas" ? (
          <AreaOverview schema={schema} areas={areas} titleOf={titleOf} onOpen={openArea} onFocus={focusTable} />
        ) : (
        <div className="schema-stage">
          <div className="schema-workspace" aria-busy={laying}>
            {graph && graph.tables.length === 0 ? (
              <div className="schema-empty">
                <strong>Nothing to draw yet.</strong>
                <p>{view.kind === "custom" ? "Add tables to your view from search or a table's details." : "No tables match this view."}</p>
              </div>
            ) : (
              <ReactFlow
                nodes={displayNodes}
                edges={displayEdges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onInit={setInstance}
                onNodeClick={(_, node) => {
                  if (node.type === "area") openArea(node.id);
                  else if (node.type === "tableCard") select(node.id);
                }}
                onNodeDoubleClick={(_, node) => { if (node.type === "tableCard") focusTable(node.id); }}
                onNodeMouseEnter={(_, node) => { if (node.type !== "areaGroup") setHovered(node.id); }}
                onNodeMouseLeave={() => setHovered(null)}
                onPaneClick={() => select(null)}
                nodesConnectable={false}
                minZoom={0.1}
                maxZoom={1.6}
                colorMode="dark"
                proOptions={{ hideAttribution: true }}
              >
                <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#252c33" />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable nodeColor={(node) => areaColor(
                  node.type === "tableCard" ? (node.data as TableCardData).table.namespace
                    : node.type === "area" ? node.id : String((node.data as { area?: string }).area ?? ""),
                )} maskColor="rgba(8, 10, 12, .7)" />
              </ReactFlow>
            )}
            <Legend />
          </div>
          {selectedTable && (
            <Inspector
              table={selectedTable}
              relationships={relationships}
              catalog={catalogByName.get(selectedTable.name)}
              titleOf={titleOf}
              onSelect={(name) => select(name)}
              onFocus={focusTable}
              onToggleView={toggleMyView}
              inView={myView.includes(selectedTable.name)}
              onClose={() => select(null)}
            />
          )}
        </div>
        )}
        <p className="schema-footnote">
          Links are inferred from shared column names (ClickHouse does not enforce foreign keys).
          {" "}{formatCompact(schema.relationships.length)} links in total; the switches above choose which kinds are drawn.
        </p>
      </section>
    </div>
  );
}

export function SchemaExplorer() {
  const schema = useRemote<SchemaMapResponse>("/hub/api/v1/schema-map");
  const catalog = useRemote<CatalogIndex>("/hub/api/v1/catalog");
  if (schema.loading && !schema.data) return <main className="schema-page"><Skeleton rows={6} /></main>;
  if (!schema.data) {
    return (
      <main className="schema-page">
        <ErrorBanner onRetry={schema.refresh}>{errorMessage(schema.error, "The schema could not be loaded.")}</ErrorBanner>
      </main>
    );
  }
  return (
    <main className="schema-page">
      <ReactFlowProvider>
        <ExplorerCanvas schema={schema.data} catalog={catalog.data} />
      </ReactFlowProvider>
    </main>
  );
}
