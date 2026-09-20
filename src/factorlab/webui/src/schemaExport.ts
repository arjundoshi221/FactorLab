import type { SchemaMapResponse } from "./schemaTypes";

export type SchemaExportFormat = "md" | "json";

function markdownCell(value: string | number | boolean | null): string {
  return String(value ?? "—").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function keyRoles(column: SchemaMapResponse["tables"][number]["columns"][number]): string {
  const roles = [];
  if (column.in_primary_key) roles.push("PRIMARY");
  if (column.in_sorting_key) roles.push("SORT");
  if (column.in_partition_key) roles.push("PARTITION");
  return roles.join(", ") || "—";
}

export function schemaToMarkdown(schema: SchemaMapResponse): string {
  const lines = [
    `# ${schema.database} database schema`,
    "",
    `Generated: ${schema.generated_at}`,
    "",
    `- Tables: ${schema.tables.length}`,
    `- Logical relationships: ${schema.relationships.length}`,
    `- Schema fingerprint: \`${schema.schema_fingerprint}\``,
    "",
    "## Tables",
  ];

  schema.tables.forEach((table) => {
    lines.push(
      "",
      `### \`${table.name}\``,
      "",
      `- Domain: ${table.domain}`,
      `- Engine: \`${table.engine}\``,
      `- Stored rows: ${table.stored_rows}`,
      `- Primary index: \`${table.primary_key || "—"}\``,
      `- Sorting key: \`${table.sorting_key || "—"}\``,
      `- Partition key: \`${table.partition_key || "—"}\``,
      "",
      "| Column | Type | Nullable | Key roles | Default |",
      "| --- | --- | --- | --- | --- |",
    );
    table.columns.forEach((column) => lines.push(
      `| ${markdownCell(column.name)} | ${markdownCell(column.type)} | ${column.nullable ? "yes" : "no"} | ${keyRoles(column)} | ${markdownCell(column.default_expression)} |`,
    ));
  });

  lines.push(
    "",
    "## Logical relationships",
    "",
    "| Source | Target | Cardinality | Optional | Enforced |",
    "| --- | --- | --- | --- | --- |",
  );
  schema.relationships.forEach((relationship) => lines.push(
    `| \`${relationship.source.table}.${relationship.source.column}\` | \`${relationship.target.table}.${relationship.target.column}\` | ${relationship.cardinality} | ${relationship.optional ? "yes" : "no"} | ${relationship.enforced ? "yes" : "no"} |`,
  ));

  if (schema.warnings.length) {
    lines.push("", "## Warnings", "", ...schema.warnings.map((warning) => `- ${warning}`));
  }
  return `${lines.join("\n")}\n`;
}

export function schemaToJson(schema: SchemaMapResponse): string {
  return `${JSON.stringify(schema, null, 2)}\n`;
}

export function downloadSchemaExport(schema: SchemaMapResponse, format: SchemaExportFormat): void {
  const contents = format === "md" ? schemaToMarkdown(schema) : schemaToJson(schema);
  const mimeType = format === "md" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8";
  const date = schema.generated_at.slice(0, 10) || new Date().toISOString().slice(0, 10);
  const filename = `${schema.database}-schema-${date}.${format}`;
  const url = URL.createObjectURL(new Blob([contents], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
