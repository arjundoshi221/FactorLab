import { describe, expect, it } from "vitest";

import { schemaToJson, schemaToMarkdown, type SchemaExportView } from "./schemaExport";
import type { SchemaTable } from "./schemaTypes";

const column = { position: 1, nullable: false, default_kind: null, default_expression: null, in_primary_key: false, in_sorting_key: false, in_partition_key: false };
const listings: SchemaTable = {
  name: "ref.listings", namespace: "ref", domain: "Reference", title: "Listings", summary: "Every tradable listing.",
  notes: ["Join market.bars.listing_id here."], kind: "table", engine: "ReplacingMergeTree", stored_rows: 3, bytes_on_disk: 10,
  primary_key: "listing_id", sorting_key: "listing_id", partition_key: "",
  columns: [{ ...column, name: "listing_id", type: "UUID", in_primary_key: true, in_sorting_key: true, description: "FactorLab listing ID" }],
};
const bars: SchemaTable = {
  ...listings, name: "market.bars", namespace: "market", domain: "Market data", title: "Price bars", summary: "OHLCV bars.", notes: [],
  columns: [{ ...column, name: "listing_id", type: "UUID", description: "The listing" }, { ...column, name: "close", type: "Nullable(Decimal(18, 6))", nullable: true, description: "Closing price | last" }],
};
const view: SchemaExportView = {
  label: "Market data",
  tables: [bars, listings],
  relationships: [{
    id: "market.bars.listing_id->ref.listings.listing_id", source: { table: "market.bars", column: "listing_id" },
    target: { table: "ref.listings", column: "listing_id" }, kind: "logical", cardinality: "many_to_one", optional: false,
    enforced: false, category: "identity",
  }],
  titleOf: (name) => ({ "market.bars": "Price bars", "ref.listings": "Listings" })[name] ?? name,
};

describe("schema export", () => {
  it("writes a readable Markdown summary of the current view", () => {
    const markdown = schemaToMarkdown(view, "2026-09-26T00:00:00Z");
    expect(markdown).toContain("# FactorLab schema: Market data");
    expect(markdown).toContain("## Price bars (`market.bars`)");
    expect(markdown).toContain("| `close` | Nullable(Decimal(18, 6)) | Closing price \\| last | — |");
    expect(markdown).toContain("- Each Price bars row refers to one row in Listings. `listing_id` → `ref.listings.listing_id`");
    expect(markdown).toContain("- Join market.bars.listing_id here.");
  });

  it("round-trips the view as JSON", () => {
    const parsed = JSON.parse(schemaToJson(view));
    expect(parsed.label).toBe("Market data");
    expect(parsed.tables).toHaveLength(2);
    expect(parsed.relationships[0].category).toBe("identity");
  });
});
