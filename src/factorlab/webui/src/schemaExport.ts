import { relationSentence } from "./pages/schema/graph";
import type { SchemaColumn, SchemaRelationship, SchemaTable } from "./schemaTypes";

export type SchemaExportFormat = "markdown" | "json";

/** The tables and links of whatever the explorer currently shows. */
export interface SchemaExportView {
  label: string;
  tables: SchemaTable[];
  relationships: SchemaRelationship[];
  titleOf: (name: string) => string;
}

function cell(value: string | number | boolean | null | undefined): string {
  return String(value ?? "—").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function keyRoles(column: SchemaColumn): string {
  const roles = [];
  if (column.in_primary_key) roles.push("primary");
  if (column.in_sorting_key) roles.push("sort");
  if (column.in_partition_key) roles.push("partition");
  return roles.join(", ") || "—";
}

function sentence(item: SchemaRelationship, titleOf: (name: string) => string): string {
  const parts = relationSentence(item, titleOf);
  return `${parts.lead} ${parts.subject} ${parts.middle} ${parts.object}${parts.optional ? " (can be empty)" : ""}.`;
}

export function schemaToMarkdown(view: SchemaExportView, generatedAt = new Date().toISOString()): string {
  const lines = [
    `# FactorLab schema: ${view.label}`,
    "",
    `Generated: ${generatedAt}`,
    "",
    `- Tables: ${view.tables.length}`,
    `- Links: ${view.relationships.length} (inferred from column names; not enforced by ClickHouse)`,
  ];
  view.tables.forEach((table) => {
    const uses = view.relationships.filter((item) => item.source.table === table.name);
    lines.push(
      "",
      `## ${table.title} (\`${table.name}\`)`,
      "",
      table.summary,
      "",
      `- Kind: ${table.kind}; stored rows: ${table.stored_rows || (table.kind === "view" ? "—" : "none yet (reserved)")}`,
      `- Sorting key: \`${table.sorting_key || "—"}\`; partition key: \`${table.partition_key || "—"}\``,
      ...table.notes.map((note) => `- ${note}`),
      "",
      "| Column | Type | Description | Key roles |",
      "| --- | --- | --- | --- |",
      ...table.columns.map((column) =>
        `| \`${cell(column.name)}\` | ${cell(column.type)} | ${cell(column.description)} | ${keyRoles(column)} |`),
    );
    if (uses.length) {
      lines.push("", "Links:", ...uses.map((item) =>
        `- ${sentence(item, view.titleOf)} \`${item.source.column}\` → \`${item.target.table}.${item.target.column}\``));
    }
  });
  return `${lines.join("\n")}\n`;
}

export function schemaToJson(view: SchemaExportView): string {
  return `${JSON.stringify({ label: view.label, tables: view.tables, relationships: view.relationships }, null, 2)}\n`;
}

export function downloadSchemaExport(view: SchemaExportView, format: SchemaExportFormat): void {
  const contents = format === "markdown" ? schemaToMarkdown(view) : schemaToJson(view);
  const mimeType = format === "markdown" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8";
  const slug = view.label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "schema";
  const filename = `factorlab-schema-${slug}-${new Date().toISOString().slice(0, 10)}.${format === "markdown" ? "md" : "json"}`;
  const url = URL.createObjectURL(new Blob([contents], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
