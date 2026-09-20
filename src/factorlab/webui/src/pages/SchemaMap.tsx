import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
  useEdgesState,
  useNodesState,
  useUpdateNodeInternals,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import ELK from "elkjs/lib/elk.bundled.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { downloadSchemaExport } from "../schemaExport";
import type {
  SchemaMapResponse,
  SchemaRelationship,
  SchemaTable,
  SharedSchemaLayout,
} from "../schemaTypes";

const elk = new ELK();
const CARD_WIDTH = 320;
const HEADER_HEIGHT = 88;
const COLUMN_HEIGHT = 28;
const FIT_OPTIONS = { padding: 0.12, minZoom: 0.3, maxZoom: 1, duration: 300 } as const;

const domainColors: Record<string, string> = {
  Political: "#f2c66d",
  "Market data": "#74a8ff",
  Reference: "#b8f35a",
  "Raw archive": "#c49aff",
  "India operations": "#5dd69b",
  "US operations": "#ff8179",
  Operations: "#82d8e8",
  "Hub internals": "#9ba5ae",
  Unclassified: "#9ba5ae",
};

type SchemaNodeData = {
  table: SchemaTable;
  collapsed: boolean;
  outgoing: Set<string>;
  incoming: Set<string>;
  onToggle: (table: string) => void;
};

type SchemaFlowNode = Node<SchemaNodeData, "schemaTable">;

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`;
}

function linkedColumns(relationships: SchemaRelationship[], table: string) {
  const outgoing = new Set<string>();
  const incoming = new Set<string>();
  relationships.forEach((relationship) => {
    if (relationship.source.table === table) outgoing.add(relationship.source.column);
    if (relationship.target.table === table) incoming.add(relationship.target.column);
  });
  return { outgoing, incoming };
}

function SchemaTableNode({ id, data, selected }: NodeProps<SchemaFlowNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  const linked = data.table.columns.filter(
    (column) => data.outgoing.has(column.name) || data.incoming.has(column.name),
  );

  useEffect(() => updateNodeInternals(id), [data.collapsed, id, updateNodeInternals]);

  return (
    <article
      className={`schema-card ${selected ? "is-selected" : ""}`}
      style={{ "--domain-color": domainColors[data.table.domain] ?? domainColors.Unclassified } as React.CSSProperties}
    >
      <header className="schema-card__header schema-drag-handle">
        <div>
          <span>{data.table.domain}</span>
          <strong>{data.table.name}</strong>
        </div>
        <button
          type="button"
          className="nodrag"
          onClick={(event) => {
            event.stopPropagation();
            data.onToggle(data.table.name);
          }}
          aria-label={`${data.collapsed ? "Expand" : "Collapse"} ${data.table.name}`}
        >
          {data.collapsed ? "+" : "−"}
        </button>
        <small>
          {data.table.engine} · {data.table.columns.length} columns
          {data.collapsed && (data.incoming.size > 0 || data.outgoing.size > 0)
            ? ` · ${data.incoming.size} in / ${data.outgoing.size} out`
            : ""}
        </small>
        {data.collapsed && linked.map((column, index) => (
          <span className="schema-card__collapsed-port" style={{ top: 54 + index * 3 }} key={column.name}>
            {data.incoming.has(column.name) && (
              <Handle id={`target:${column.name}`} type="target" position={Position.Left} isConnectable={false} />
            )}
            {data.outgoing.has(column.name) && (
              <Handle id={`source:${column.name}`} type="source" position={Position.Right} isConnectable={false} />
            )}
          </span>
        ))}
      </header>
      {!data.collapsed && (
        <ol className="schema-card__columns">
          {data.table.columns.map((column) => (
            <li key={column.name} title={column.default_expression ?? undefined}>
              {data.incoming.has(column.name) && (
                <Handle id={`target:${column.name}`} type="target" position={Position.Left} isConnectable={false} />
              )}
              <code>{column.name}</code>
              <span>{column.type}</span>
              <div>
                {column.in_primary_key && <b title="ClickHouse primary index column">PK</b>}
                {data.outgoing.has(column.name) && <b title="Logical foreign-key reference (not enforced)">FK</b>}
                {data.incoming.has(column.name) && !column.in_primary_key && <b title="Referenced non-primary column">REF</b>}
                {column.in_sorting_key && <b title="ClickHouse sorting / primary index key">SORT</b>}
                {column.in_partition_key && <b title="ClickHouse partition key">PART</b>}
                {column.nullable && <b title="Nullable">NULL</b>}
              </div>
              {data.outgoing.has(column.name) && (
                <Handle id={`source:${column.name}`} type="source" position={Position.Right} isConnectable={false} />
              )}
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

const nodeTypes = { schemaTable: SchemaTableNode };

function makeEdges(relationships: SchemaRelationship[], tables: SchemaTable[]): Edge[] {
  const primaryColumns = new Set(tables.flatMap((table) => table.columns
    .filter((column) => column.in_primary_key)
    .map((column) => `${table.name}.${column.name}`)));
  return relationships.map((relationship) => ({
    id: relationship.id,
    source: relationship.source.table,
    sourceHandle: `source:${relationship.source.column}`,
    target: relationship.target.table,
    targetHandle: `target:${relationship.target.column}`,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, width: 13, height: 13 },
    style: { stroke: relationship.optional ? "#68727b" : "#9ba5ae", strokeWidth: 1.2 },
    label: primaryColumns.has(`${relationship.target.table}.${relationship.target.column}`) ? "FK → PK" : "FK → REF",
    labelStyle: { fill: "#aeb7be", fontSize: 8, fontWeight: 600 },
    labelBgStyle: { fill: "#11161b", fillOpacity: 0.92 },
    labelBgPadding: [4, 2],
    labelBgBorderRadius: 0,
  }));
}

async function autoLayout(nodes: SchemaFlowNode[], edges: Edge[]): Promise<SchemaFlowNode[]> {
  const graph = await elk.layout({
    id: "schema-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "72",
      "elk.layered.spacing.nodeNodeBetweenLayers": "130",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: CARD_WIDTH,
      height: node.data.collapsed
        ? HEADER_HEIGHT
        : HEADER_HEIGHT + node.data.table.columns.length * COLUMN_HEIGHT,
    })),
    edges: edges
      .filter((edge) => edge.source !== edge.target)
      .map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
  });
  const positions = new Map((graph.children ?? []).map((child) => [child.id, child]));
  return nodes.map((node) => ({
    ...node,
    position: {
      x: positions.get(node.id)?.x ?? 0,
      y: positions.get(node.id)?.y ?? 0,
    },
  }));
}

function overviewLayout(nodes: SchemaFlowNode[]): SchemaFlowNode[] {
  const ordered = [...nodes].sort((left, right) =>
    `${left.data.table.domain}:${left.id}`.localeCompare(`${right.data.table.domain}:${right.id}`),
  );
  const columns = Math.max(1, Math.ceil(Math.sqrt(ordered.length * 1.5)));
  const positions = new Map(ordered.map((node, index) => [
    node.id,
    {
      x: (index % columns) * (CARD_WIDTH + 52),
      y: Math.floor(index / columns) * (HEADER_HEIGHT + 54),
    },
  ]));
  return nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position }));
}

function SchemaInspector({
  table,
  tables,
  relationships,
  onClose,
}: {
  table: SchemaTable;
  tables: SchemaTable[];
  relationships: SchemaRelationship[];
  onClose: () => void;
}) {
  const links = relationships.filter(
    (relationship) => relationship.source.table === table.name || relationship.target.table === table.name,
  );
  const columnLinks = linkedColumns(relationships, table.name);
  const primaryColumns = new Set(tables.flatMap((candidate) => candidate.columns
    .filter((column) => column.in_primary_key)
    .map((column) => `${candidate.name}.${column.name}`)));
  return (
    <aside className="schema-inspector" aria-label={`${table.name} details`}>
      <header>
        <div><span className="eyebrow">Table details</span><h2>{table.name}</h2></div>
        <button type="button" onClick={onClose} aria-label="Close table details">×</button>
      </header>
      <dl>
        <div><dt>Domain</dt><dd>{table.domain}</dd></div>
        <div><dt>Engine</dt><dd>{table.engine}</dd></div>
        <div><dt>Stored rows</dt><dd>{table.stored_rows.toLocaleString("en-IN")}</dd></div>
        <div><dt>On disk</dt><dd>{formatBytes(table.bytes_on_disk)}</dd></div>
        <div><dt>Primary index</dt><dd><code>{table.primary_key || "—"}</code></dd></div>
        <div><dt>Sort key</dt><dd><code>{table.sorting_key || "—"}</code></dd></div>
        <div><dt>Partition key</dt><dd><code>{table.partition_key || "—"}</code></dd></div>
      </dl>
      <section className="schema-inspector__columns">
        <h3>Columns <span>{table.columns.length}</span></h3>
        <ol>
          {table.columns.map((column) => (
            <li key={column.name}>
              <code>{column.name}</code>
              <span>{column.type}</span>
              <div>
                {column.in_primary_key && <b>PK</b>}
                {columnLinks.outgoing.has(column.name) && <b>FK</b>}
                {columnLinks.incoming.has(column.name) && !column.in_primary_key && <b>REF</b>}
                {column.in_sorting_key && <b>SORT</b>}
                {column.in_partition_key && <b>PART</b>}
                {column.nullable && <b>NULL</b>}
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="schema-inspector__relationships">
        <h3>Logical relationships <span>{links.length}</span></h3>
        {links.length ? (
          <ul>{links.map((link) => (
            <li key={link.id}>
              <div><b>FK</b><code>{link.source.table}.{link.source.column}</code></div>
              <span>references</span>
              <div>
                <b>{primaryColumns.has(`${link.target.table}.${link.target.column}`) ? "PK" : "REF"}</b>
                <code>{link.target.table}.{link.target.column}</code>
              </div>
            </li>
          ))}</ul>
        ) : <p>No reviewed logical links.</p>}
      </section>
      <p className="schema-inspector__note">
        ClickHouse primary indexes and logical links optimize or describe joins; they do not enforce uniqueness or foreign-key integrity.
      </p>
    </aside>
  );
}

function SchemaMapCanvas() {
  const [schema, setSchema] = useState<SchemaMapResponse | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<SchemaFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [instance, setInstance] = useState<ReactFlowInstance<SchemaFlowNode, Edge> | null>(null);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("All domains");
  const [showLinks, setShowLinks] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const initialized = useRef(false);
  const restoredRevision = useRef<number | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);

  const toggleCollapsed = useCallback((tableName: string) => {
    setNodes((current) => current.map((node) => node.id === tableName
      ? { ...node, data: { ...node.data, collapsed: !node.data.collapsed } }
      : node));
    setDirty(true);
  }, [setNodes]);

  const hydrate = useCallback(async (data: SchemaMapResponse) => {
    const saved = new Map(data.layout.nodes.map((node) => [node.table, node]));
    const baseNodes: SchemaFlowNode[] = data.tables.map((table) => {
      const links = linkedColumns(data.relationships, table.name);
      return {
        id: table.name,
        type: "schemaTable",
        position: { x: 0, y: 0 },
        dragHandle: ".schema-drag-handle",
        data: {
          table,
          collapsed: saved.get(table.name)?.collapsed ?? true,
          ...links,
          onToggle: toggleCollapsed,
        },
      };
    });
    const nextEdges = makeEdges(data.relationships, data.tables);
    let positioned: SchemaFlowNode[];
    if (saved.size === 0) {
      positioned = overviewLayout(baseNodes);
    } else {
      const maxSavedX = Math.max(...Array.from(saved.values()).map((item) => item.x), 0);
      let newIndex = 0;
      positioned = baseNodes.map((node) => {
        const position = saved.get(node.id);
        if (position) return { ...node, position: { x: position.x, y: position.y } };
        const staged = { x: maxSavedX + CARD_WIDTH + 140, y: newIndex * 180 };
        newIndex += 1;
        return { ...node, position: staged };
      });
    }
    setSchema(data);
    setEdges(nextEdges);
    setNodes(positioned);
    setDirty(false);
    setError(null);
    setNotice(data.layout.schema_fingerprint && data.layout.schema_fingerprint !== data.schema_fingerprint
      ? "The live schema changed. New tables were staged to the right; review and save the updated layout."
      : null);
    if (data.layout.schema_fingerprint && data.layout.schema_fingerprint !== data.schema_fingerprint) setDirty(true);
    restoredRevision.current = null;
  }, [setEdges, setNodes, toggleCollapsed]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch("/hub/api/v1/schema-map", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Schema request failed (${response.status})`);
      await hydrate(await response.json() as SchemaMapResponse);
    } catch {
      setError("The live ClickHouse schema could not be loaded.");
    }
  }, [hydrate]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void load();
  }, [load]);

  useEffect(() => {
    if (!instance || !schema || nodes.length === 0 || restoredRevision.current === schema.layout.revision) return;
    restoredRevision.current = schema.layout.revision;
    if (schema.layout.nodes.length) void instance.setViewport(schema.layout.viewport);
    else void instance.fitView(FIT_OPTIONS);
  }, [instance, nodes.length, schema]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === boardRef.current);
      window.setTimeout(() => void instance?.fitView(FIT_OPTIONS), 80);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, [instance]);

  useEffect(() => {
    if (!dirty) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [dirty]);

  const arrange = useCallback(async () => {
    const arranged = nodes.every((node) => node.data.collapsed)
      ? overviewLayout(nodes)
      : await autoLayout(nodes, edges);
    setNodes(arranged);
    setDirty(true);
    setNotice("Automatic layout applied. Save when the arrangement looks right.");
    window.setTimeout(() => void instance?.fitView(FIT_OPTIONS), 0);
  }, [edges, instance, nodes, setNodes]);

  const toggleAllColumns = useCallback(async () => {
    const collapse = nodes.some((node) => !node.data.collapsed);
    const resized = nodes.map((node) => ({
      ...node,
      data: { ...node.data, collapsed: collapse },
    }));
    const arranged = collapse ? overviewLayout(resized) : await autoLayout(resized, edges);
    setNodes(arranged);
    setDirty(true);
    setNotice(collapse
      ? "Compact overview restored. Select a table to inspect every column."
      : "All columns are visible. Compact the cards to return to the readable overview.");
    window.setTimeout(() => void instance?.fitView(FIT_OPTIONS), 0);
  }, [edges, instance, nodes, setNodes]);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await boardRef.current?.requestFullscreen();
    } catch {
      setError("Fullscreen mode is unavailable in this browser.");
    }
  }, []);

  const save = useCallback(async () => {
    if (!schema || !instance) return;
    setSaving(true);
    setError(null);
    try {
      const response = await fetch("/hub/api/v1/schema-map/layout", {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          base_revision: schema.layout.revision,
          schema_fingerprint: schema.schema_fingerprint,
          nodes: nodes.map((node) => ({
            table: node.id,
            x: node.position.x,
            y: node.position.y,
            collapsed: node.data.collapsed,
          })),
          viewport: instance.getViewport(),
        }),
      });
      if (response.status === 409) throw new Error("Someone saved a newer layout. Reload before saving your arrangement.");
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `Save failed (${response.status})`);
      }
      const layout = await response.json() as SharedSchemaLayout;
      setSchema((current) => current ? { ...current, layout } : current);
      setDirty(false);
      setNotice("Shared layout saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "The layout could not be saved.");
    } finally {
      setSaving(false);
    }
  }, [instance, nodes, schema]);

  const domains = useMemo(
    () => ["All domains", ...Array.from(new Set(schema?.tables.map((table) => table.domain) ?? [])).sort()],
    [schema],
  );
  const visibleNodes = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return nodes.map((node) => ({
      ...node,
      hidden: (domain !== "All domains" && node.data.table.domain !== domain)
        || Boolean(normalized && !`${node.id} ${node.data.table.columns.map((column) => column.name).join(" ")}`.toLowerCase().includes(normalized)),
    }));
  }, [domain, nodes, query]);
  const visibleIds = useMemo(() => new Set(visibleNodes.filter((node) => !node.hidden).map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(() => edges.map((edge) => ({
    ...edge,
    hidden: !showLinks || !visibleIds.has(edge.source) || !visibleIds.has(edge.target),
    style: {
      ...edge.style,
      opacity: selectedTable
        ? (edge.source === selectedTable || edge.target === selectedTable ? 1 : 0.06)
        : 0.38,
      strokeWidth: edge.source === selectedTable || edge.target === selectedTable ? 2 : 1.1,
    },
    zIndex: edge.source === selectedTable || edge.target === selectedTable ? 2 : 0,
  })), [edges, selectedTable, showLinks, visibleIds]);
  const selected = schema?.tables.find((table) => table.name === selectedTable) ?? null;
  const hasExpandedCards = nodes.some((node) => !node.data.collapsed);
  const warningCount = schema?.warnings.length ?? 0;

  return (
    <main className="schema-page">
      <section className="schema-heading">
        <div>
          <span className="eyebrow">Live ClickHouse architecture</span>
          <h1>Schema map</h1>
          <p>Drag tables into place, trace reviewed key relationships, and inspect every column without exposing row data.</p>
        </div>
        <div className="schema-heading__stats">
          <strong>{schema?.tables.length ?? "—"}</strong><span>tables</span>
          <strong>{schema?.relationships.length ?? "—"}</strong><span>logical links</span>
        </div>
      </section>
      <div className="schema-board" ref={boardRef}>
        <section className="schema-toolbar" aria-label="Schema map tools">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find table or column" aria-label="Find table or column" />
          <select value={domain} onChange={(event) => setDomain(event.target.value)} aria-label="Filter by domain">
            {domains.map((item) => <option key={item}>{item}</option>)}
          </select>
          <label><input type="checkbox" checked={showLinks} onChange={(event) => setShowLinks(event.target.checked)} /> Relationships</label>
          <span className="schema-toolbar__spacer" />
          <button type="button" className="button-secondary" onClick={() => void toggleAllColumns()} disabled={!schema}>{hasExpandedCards ? "Compact all" : "Show columns"}</button>
          <button type="button" className="button-secondary" onClick={() => void instance?.fitView(FIT_OPTIONS)}>Fit view</button>
          <button type="button" className="button-secondary" onClick={() => void arrange()} disabled={!schema}>Arrange</button>
          <button type="button" className="button-secondary" onClick={() => void toggleFullscreen()}>{isFullscreen ? "Exit fullscreen" : "Fullscreen"}</button>
          <details className="schema-export">
            <summary className="button-secondary">Export</summary>
            <div>
              <button type="button" onClick={() => schema && downloadSchemaExport(schema, "md")} disabled={!schema}>Markdown (.md)</button>
              <button type="button" onClick={() => schema && downloadSchemaExport(schema, "json")} disabled={!schema}>JSON (.json)</button>
            </div>
          </details>
          <button type="button" onClick={() => void save()} disabled={!dirty || saving}>{saving ? "Saving…" : dirty ? "Save layout" : "Layout saved"}</button>
        </section>
        {Boolean(error || notice || warningCount > 0) && (
          <div className={error ? "notice notice--error schema-notice" : "notice schema-notice"} role="status">
            {error ?? notice ?? `${warningCount} configured relationship(s) do not match the live schema.`}
            {error?.includes("newer layout") && <button type="button" onClick={() => void load()}>Reload</button>}
          </div>
        )}
        <section className="schema-workspace">
          {!schema && !error && <div className="schema-loading">Reading ClickHouse metadata…</div>}
          {schema && (
            <ReactFlow<SchemaFlowNode, Edge>
              nodes={visibleNodes}
              edges={visibleEdges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onInit={setInstance}
              onNodeClick={(_, node) => setSelectedTable(node.id)}
              onNodeDragStop={() => setDirty(true)}
              onPaneClick={() => setSelectedTable(null)}
              nodesConnectable={false}
              deleteKeyCode={null}
              minZoom={0.2}
              maxZoom={1.8}
              fitView
              fitViewOptions={FIT_OPTIONS}
              colorMode="dark"
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#303740" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => domainColors[(node.data as SchemaNodeData).table.domain] ?? domainColors.Unclassified}
                maskColor="rgba(8, 10, 12, .76)"
              />
            </ReactFlow>
          )}
          {selected && <SchemaInspector table={selected} tables={schema?.tables ?? []} relationships={schema?.relationships ?? []} onClose={() => setSelectedTable(null)} />}
        </section>
      </div>
      <footer>Solid arrows are reviewed logical joins · ClickHouse does not enforce foreign keys or uniqueness</footer>
    </main>
  );
}

export function SchemaMap() {
  return <ReactFlowProvider><SchemaMapCanvas /></ReactFlowProvider>;
}
