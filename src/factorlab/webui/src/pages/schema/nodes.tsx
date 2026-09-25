import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from "@xyflow/react";
import { useEffect } from "react";

import { formatCompact } from "../../shared/format";
import type { SchemaColumn, SchemaTable } from "../../schemaTypes";
import { areaColor, keyColumns, type LinkedColumns } from "./graph";

export const CARD_WIDTH = 300;
const HEADER = 104;
const HEADER_FADED = 58;
const COLUMN_ROW = 40;
const FOOTER = 28;

export function shortType(type: string): string {
  const inner = type.replace(/^(Nullable|LowCardinality)\((.*)\)$/, "$2").replace(/^(Nullable|LowCardinality)\((.*)\)$/, "$2");
  if (/^(String|FixedString)/.test(inner)) return "text";
  if (inner.startsWith("Enum")) return "category";
  if (/^U?Int\d+$/.test(inner)) return "whole number";
  if (inner.startsWith("Float")) return "number";
  if (inner.startsWith("Decimal")) return "decimal";
  if (inner.startsWith("DateTime")) return "timestamp";
  if (inner.startsWith("Date")) return "date";
  if (inner === "UUID") return "ID";
  if (inner === "Bool") return "yes / no";
  if (inner.startsWith("Array")) return "list";
  return "structured";
}

export function cardColumns(table: SchemaTable, linked: LinkedColumns, expanded: boolean, faded: boolean): SchemaColumn[] {
  if (faded) return table.columns.filter((column) => linked.outgoing.has(column.name) || linked.incoming.has(column.name));
  return expanded ? table.columns : keyColumns(table, linked);
}

export function cardHeight(table: SchemaTable, linked: LinkedColumns, expanded: boolean, faded: boolean): number {
  const shown = cardColumns(table, linked, expanded, faded).length;
  const hidden = table.columns.length - shown;
  return (faded ? HEADER_FADED : HEADER) + shown * COLUMN_ROW + (!faded && hidden > 0 ? FOOTER : 8);
}

export type TableCardData = {
  table: SchemaTable;
  linked: LinkedColumns;
  expanded: boolean;
  faded: boolean;
  focus: boolean;
  dimmed: boolean;
  /** Title-only card for the full map; links attach to the card, not to columns. */
  mini?: boolean;
  titleOf: (name: string) => string;
  onToggle: (name: string) => void;
};

export const MINI_HEIGHT = 92;
export type TableCardNode = Node<TableCardData, "tableCard">;

function RowsBadge({ table }: { table: SchemaTable }) {
  if (table.kind === "view") return <span className="schema-badge schema-badge--view">View</span>;
  if (!table.stored_rows) return <span className="schema-badge schema-badge--reserved">Reserved · no data yet</span>;
  return <span className="schema-badge">{formatCompact(table.stored_rows)} rows</span>;
}

export function TableCard({ id, data, selected }: NodeProps<TableCardNode>) {
  const updateInternals = useUpdateNodeInternals();
  const { table, linked, expanded, faded } = data;
  const columns = cardColumns(table, linked, expanded, faded);
  const hidden = table.columns.length - columns.length;
  useEffect(() => updateInternals(id), [id, expanded, faded, columns.length, updateInternals]);
  const classes = ["schema-card", selected && "is-selected", faded && "is-faded", data.focus && "is-focus",
    data.mini && "is-mini", data.dimmed && "is-dimmed", !table.stored_rows && table.kind === "table" && "is-reserved"]
    .filter(Boolean).join(" ");
  if (data.mini) {
    return (
      <article className={classes} style={{ "--area-color": areaColor(table.namespace) } as React.CSSProperties} title={table.summary}>
        <Handle type="target" position={Position.Left} isConnectable={false} />
        <header className="schema-card__header">
          <div className="schema-card__title"><strong>{table.title}</strong><code>{table.name}</code></div>
          <div className="schema-card__meta"><RowsBadge table={table} /></div>
        </header>
        <Handle type="source" position={Position.Right} isConnectable={false} />
      </article>
    );
  }
  return (
    <article className={classes} style={{ "--area-color": areaColor(table.namespace) } as React.CSSProperties}>
      <header className="schema-card__header">
        <div className="schema-card__title">
          <strong>{table.title}</strong>
          <code>{table.name}</code>
        </div>
        {!faded && <p>{table.summary}</p>}
        <div className="schema-card__meta">
          <RowsBadge table={table} />
          {!faded && hidden > 0 && (
            <button type="button" className="nodrag schema-card__toggle" onClick={(event) => {
              event.stopPropagation();
              data.onToggle(table.name);
            }}>
              {expanded ? "Key columns only" : `All ${table.columns.length} columns`}
            </button>
          )}
        </div>
      </header>
      <ol className="schema-card__columns">
        {columns.map((column) => {
          const out = linked.outgoing.get(column.name);
          const incoming = linked.incoming.get(column.name);
          return (
            <li key={column.name} title={column.description ?? column.type}>
              {incoming && <Handle id={`t:${column.name}`} type="target" position={Position.Left} isConnectable={false} />}
              <div className="schema-card__column">
                <code>{column.name}</code>
                <span className="schema-card__type">{shortType(column.type)}</span>
                {(column.in_primary_key || column.in_sorting_key) && <b className="schema-card__key" title="Part of the table's key">key</b>}
              </div>
              <small>
                {out ? <>→ {data.titleOf(out.target.table)}</>
                  : incoming ? <>used by {incoming.length} {incoming.length === 1 ? "table" : "tables"}</>
                  : column.description ?? ""}
              </small>
              {out && <Handle id={`s:${column.name}`} type="source" position={Position.Right} isConnectable={false} />}
            </li>
          );
        })}
      </ol>
      {!faded && hidden > 0 && !expanded && <footer className="schema-card__more">+ {hidden} more columns</footer>}
    </article>
  );
}

export type AreaGroupData = { title: string; area: string; count: number };
export type AreaGroupNode = Node<AreaGroupData, "areaGroup">;

export function AreaGroup({ data }: NodeProps<AreaGroupNode>) {
  return (
    <section className="schema-area-group" style={{ "--area-color": areaColor(data.area) } as React.CSSProperties}>
      <header><strong>{data.title}</strong><span>{data.count} tables</span></header>
    </section>
  );
}

export const nodeTypes = { tableCard: TableCard, areaGroup: AreaGroup };
