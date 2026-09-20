import { describe, expect, it } from "vitest";

import { schemaToJson, schemaToMarkdown } from "./schemaExport";
import type { SchemaMapResponse } from "./schemaTypes";

const schema = {
  generated_at: "2026-09-19T00:00:00Z",
  database: "factorlab",
  schema_fingerprint: "abc123",
  tables: [{
    name: "ref_exchanges",
    domain: "Reference",
    engine: "ReplacingMergeTree",
    stored_rows: 4,
    bytes_on_disk: 100,
    primary_key: "exchange_code",
    sorting_key: "exchange_code",
    partition_key: "",
    columns: [{
      name: "exchange_code",
      type: "LowCardinality(String)",
      position: 1,
      nullable: false,
      default_kind: null,
      default_expression: null,
      in_primary_key: true,
      in_sorting_key: true,
      in_partition_key: false,
    }],
  }],
  relationships: [],
  layout: { revision: 0, schema_fingerprint: "", nodes: [], viewport: { x: 0, y: 0, zoom: 1 }, updated_at: null },
  warnings: [],
} satisfies SchemaMapResponse;

describe("schema export", () => {
  it("formats a readable Markdown data dictionary", () => {
    const output = schemaToMarkdown(schema);
    expect(output).toContain("# factorlab database schema");
    expect(output).toContain("### `ref_exchanges`");
    expect(output).toContain("| exchange_code | LowCardinality(String) | no | PRIMARY, SORT | — |");
  });

  it("formats the complete response as JSON", () => {
    const output = schemaToJson(schema);
    expect(JSON.parse(output)).toEqual(schema);
    expect(output).toContain('  "tables": [');
  });
});
