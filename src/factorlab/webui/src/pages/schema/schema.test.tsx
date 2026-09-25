import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("elkjs/lib/elk.bundled.js", () => ({
  default: class {
    async layout(graph: { children: Array<{ id: string; children?: Array<{ id: string }> }> }) {
      return {
        ...graph,
        children: graph.children.map((child, index) => ({
          ...child, x: index * 400, y: 20, width: 380, height: 300,
          children: child.children?.map((inner, position) => ({ ...inner, x: 20, y: 60 + position * 200 })),
        })),
      };
    }
  },
}));

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  return {
    Background: () => null,
    BackgroundVariant: { Dots: "dots" },
    Controls: () => null,
    Handle: () => null,
    MarkerType: { ArrowClosed: "arrowclosed" },
    MiniMap: () => null,
    Position: { Left: "left", Right: "right" },
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
    ReactFlow: ({ nodes, edges, nodeTypes, onInit, onNodeClick, onNodeDoubleClick }: any) => {
      useEffect(() => { onInit({ fitView: async () => undefined }); }, [onInit]);
      return (
        <div data-testid="flow" data-edges={edges.map((edge: any) => edge.id).join(" ")}>
          {nodes.map((node: any) => {
            const Component = nodeTypes[node.type];
            return (
              <div key={node.id} data-testid={`node-${node.id}`} className={node.className}
                onClick={(event) => onNodeClick?.(event, node)} onDoubleClick={(event) => onNodeDoubleClick?.(event, node)}>
                <Component id={node.id} data={node.data} selected={node.selected} />
              </div>
            );
          })}
        </div>
      );
    },
    useNodesState: (initial: any[]) => {
      const [value, setValue] = React.useState(initial);
      return [value, setValue, vi.fn()];
    },
    useUpdateNodeInternals: () => vi.fn(),
    useNodesInitialized: () => true,
  };
});

import type { SchemaMapResponse, SchemaRelationship, SchemaTable } from "../../schemaTypes";
import { areaGraph, neighbourhood, relationSentence, searchSchema, viewGraph, visibleRelationships } from "./graph";
import { SchemaExplorer } from "./SchemaExplorer";

const column = { position: 1, nullable: false, default_kind: null, default_expression: null, in_primary_key: false, in_sorting_key: false, in_partition_key: false };
function table(name: string, title: string, rows: number, columns: Partial<SchemaTable["columns"][number]>[], extra: Partial<SchemaTable> = {}): SchemaTable {
  const namespace = name.split(".")[0];
  return {
    name, namespace, domain: namespace, title, summary: `${title} summary.`, notes: [], kind: "table", engine: "ReplacingMergeTree",
    stored_rows: rows, bytes_on_disk: 1, primary_key: "", sorting_key: "", partition_key: "",
    columns: columns.map((item, index) => ({ ...column, position: index + 1, name: "c", type: "String", ...item })),
    ...extra,
  };
}
function link(source: string, column: string, target: string, targetColumn: string, category: SchemaRelationship["category"], optional = false): SchemaRelationship {
  return { id: `${source}.${column}->${target}.${targetColumn}`, source: { table: source, column }, target: { table: target, column: targetColumn },
    kind: "logical", cardinality: "many_to_one", optional, enforced: false, category };
}

const schema: SchemaMapResponse = {
  generated_at: "2026-09-26T00:00:00Z",
  schema_fingerprint: "f",
  warnings: [],
  areas: [
    { id: "ref", title: "Reference", summary: "The shared dictionary." },
    { id: "market", title: "Market data", summary: "Price and volume bars." },
    { id: "raw", title: "Raw archive", summary: "Vendor responses." },
    { id: "broker", title: "Broker mirror", summary: "Reserved." },
    { id: "research", title: "Research views", summary: "Views." },
  ],
  tables: [
    table("market.bars", "Price bars", 1000, [
      { name: "listing_id", type: "UUID", in_sorting_key: true, description: "The listing's FactorLab ID." },
      { name: "close", type: "Nullable(Decimal(18, 6))", nullable: true, description: "Closing price of the bar." },
      { name: "raw_id", type: "Nullable(UUID)", nullable: true, description: "Raw response." },
    ], { notes: ["Filter by resolution."] }),
    table("ref.listings", "Listings", 50, [
      { name: "listing_id", type: "UUID", in_primary_key: true, in_sorting_key: true },
      { name: "security_id", type: "UUID" },
      { name: "country_code", type: "FixedString(2)" },
    ]),
    table("ref.securities", "Securities", 40, [{ name: "security_id", type: "UUID", in_sorting_key: true }]),
    table("ref.countries", "Countries", 2, [{ name: "country_code", type: "FixedString(2)", in_sorting_key: true }]),
    table("raw.archive", "Raw vendor responses", 9, [{ name: "raw_id", type: "UUID", in_sorting_key: true }]),
    table("broker.executions", "Executions", 0, [{ name: "exec_id", type: "String", in_sorting_key: true }]),
    table("research.bars", "Point-in-time bars", 0, [{ name: "bar_time", type: "DateTime64(3, 'UTC')" }], { kind: "view", engine: "View" }),
  ],
  relationships: [
    link("market.bars", "listing_id", "ref.listings", "listing_id", "identity"),
    link("market.bars", "raw_id", "raw.archive", "raw_id", "lineage", true),
    link("ref.listings", "security_id", "ref.securities", "security_id", "identity"),
    link("ref.listings", "country_code", "ref.countries", "country_code", "lookup"),
  ],
};

describe("schema graph", () => {
  const identity = visibleRelationships(schema.relationships, new Set(["identity"]));

  it("walks neighbourhoods by depth and filters by link type", () => {
    expect([...neighbourhood("market.bars", 1, identity)].sort()).toEqual(["market.bars", "ref.listings"]);
    expect([...neighbourhood("market.bars", 2, identity)].sort()).toEqual(["market.bars", "ref.listings", "ref.securities"]);
    // Past the first step, a crowded hub's referrers are left out.
    const hub = Array.from({ length: 8 }, (_, index) => link(`market.t${index}`, "listing_id", "ref.listings", "listing_id", "identity"));
    expect(neighbourhood("ref.securities", 2, [...identity, ...hub]).size).toBe(2);
    expect(neighbourhood("ref.listings", 1, [...identity, ...hub]).size).toBe(11);
    expect(identity).toHaveLength(2);
  });

  it("builds area, custom, and full views with faded context tables", () => {
    const area = viewGraph({ kind: "area", area: "market", context: true }, schema, identity);
    expect(area.tables.map((item) => item.name)).toEqual(["market.bars", "ref.listings"]);
    expect([...area.faded]).toEqual(["ref.listings"]);
    const bare = viewGraph({ kind: "area", area: "market", context: false }, schema, identity);
    expect(bare.tables).toHaveLength(1);
    const custom = viewGraph({ kind: "custom", tables: ["market.bars", "ref.securities"], neighbours: false }, schema, identity);
    expect(custom.relationships).toHaveLength(0);
    expect(viewGraph({ kind: "all" }, schema, identity).tables).toHaveLength(schema.tables.length);
  });

  it("keeps what an area refers to but summarizes a crowd of referrers", () => {
    const referrers = Array.from({ length: 8 }, (_, index) => table(`market.t${index}`, `T${index}`, 1, [{ name: "security_id", type: "UUID" }]));
    const crowded = { ...schema, tables: [...schema.tables, ...referrers] };
    const links = [...identity, ...referrers.map((item) => link(item.name, "security_id", "ref.securities", "security_id", "identity"))];
    const view = viewGraph({ kind: "area", area: "ref", context: true }, crowded, links);
    expect([...view.faded]).toEqual([]);
    expect(view.skippedReferrers).toBe(9);
    const market = viewGraph({ kind: "area", area: "market", context: true }, crowded, links);
    expect(market.faded.has("ref.listings") && market.faded.has("ref.securities")).toBe(true);
  });

  it("aggregates links between areas and explains links in words", () => {
    const { areas, links } = areaGraph(schema, schema.relationships);
    expect(areas.find((item) => item.id === "broker")).toMatchObject({ tables: 1, withData: 0 });
    expect(areas.find((item) => item.id === "research")).toMatchObject({ tables: 0, views: 1 });
    expect(links.map((item) => [item.id, item.count])).toEqual([["market->ref", 1], ["market->raw", 1]]);
    const titles = (name: string) => schema.tables.find((item) => item.name === name)?.title ?? name;
    expect(relationSentence(schema.relationships[0], titles)).toMatchObject({ subject: "Price bars", middle: "row refers to one row in", object: "Listings" });
    expect(relationSentence(schema.relationships[1], titles).middle).toContain("raw response");
    expect(relationSentence(schema.relationships[3], titles).middle).toContain("shared code");
  });

  it("searches titles, column names, and column descriptions", () => {
    const hits = searchSchema(schema.tables, "closing");
    expect(hits[0].table.name).toBe("market.bars");
    expect(hits[0].columns[0]).toEqual({ name: "close", description: "Closing price of the bar." });
    expect(searchSchema(schema.tables, "listings")[0].table.name).toBe("ref.listings");
  });
});

describe("schema explorer", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/schema");
    vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).includes("catalog")
      ? { ok: false, status: 503, json: async () => ({ detail: "catalog down" }) }
      : { ok: true, status: 200, json: async () => schema }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("starts with the areas and opens one as a focused diagram", async () => {
    render(<SchemaExplorer />);
    expect(await screen.findByRole("heading", { name: "How FactorLab's data fits together" })).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "Areas of the database" });
    const market = within(overview).getByRole("heading", { name: "Market data" }).closest("li")!;
    expect(within(market).getByText("Refers to", { exact: false })).toHaveTextContent("Refers to Reference (1)");
    expect(within(market).getByRole("button", { name: "Price bars" })).toBeInTheDocument();
    const broker = within(overview).getByRole("heading", { name: "Broker mirror" }).closest("li")!;
    expect(within(broker).getByText("Reserved tables (no data yet)")).toBeInTheDocument();
    expect(screen.queryByTestId("flow")).not.toBeInTheDocument();

    fireEvent.click(within(market).getByRole("button", { name: "Open Market data →" }));
    expect(window.location.search).toBe("?area=market");
    await waitFor(() => expect(within(screen.getByTestId("flow")).getByText("Price bars")).toBeInTheDocument());
    expect(screen.getByTestId("node-ref.listings").querySelector(".schema-card")).toHaveClass("is-faded");
    expect(within(screen.getByTestId("node-market.bars")).getByText("→ Listings")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-market.bars")).getByText("+ 2 more columns")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/Lineage/));
    expect(new URLSearchParams(window.location.search).get("links")).toBe("identity,lineage");
    await waitFor(() => expect(screen.getByTestId("node-raw.archive")).toBeInTheDocument());
  });

  it("explains a table, focuses its neighbourhood, and builds a custom view", async () => {
    window.history.replaceState({}, "", "/schema?area=market");
    render(<SchemaExplorer />);
    fireEvent.click(await screen.findByTestId("node-market.bars"));
    const inspector = await screen.findByRole("complementary", { name: "Price bars details" });
    expect(within(inspector).getByText("Filter by resolution.")).toBeInTheDocument();
    expect(within(inspector).getByText("Closing price of the bar.")).toBeInTheDocument();
    expect(within(inspector).getByText(/row refers to one row in/)).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Open in data catalog" })).toHaveAttribute("href", "/data/tables/market.bars");

    fireEvent.click(within(inspector).getByRole("button", { name: "Focus on this table" }));
    expect(new URLSearchParams(window.location.search).get("table")).toBe("market.bars");
    fireEvent.click(await screen.findByRole("button", { name: "Two steps" }));
    await waitFor(() => expect(screen.getByTestId("node-ref.securities")).toBeInTheDocument());

    fireEvent.click(within(screen.getByRole("complementary", { name: "Price bars details" })).getByRole("button", { name: "Add to my view" }));
    expect(new URLSearchParams(window.location.search).get("tables")).toBe("market.bars");
    fireEvent.change(screen.getByPlaceholderText(/Search tables or columns/), { target: { value: "securities" } });
    const results = screen.getByRole("region", { name: "Search results" });
    fireEvent.click(within(results).getByRole("button", { name: "+ Add to my view" }));
    expect(new URLSearchParams(window.location.search).get("tables")).toBe("market.bars,ref.securities");
    fireEvent.click(screen.getByRole("button", { name: "My view" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "2 chosen tables" })).toBeInTheDocument());
  });

  it("groups the full map by area and marks reserved tables", async () => {
    window.history.replaceState({}, "", "/schema?view=all");
    render(<SchemaExplorer />);
    await waitFor(() => expect(screen.getByTestId("node-group:market")).toBeInTheDocument());
    expect(within(screen.getByTestId("node-group:broker")).getByText("1 tables")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-broker.executions")).getByText("Reserved · no data yet")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-research.bars")).getByText("View")).toBeInTheDocument();
  });
});
