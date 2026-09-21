import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("elkjs/lib/elk.bundled.js", () => ({
  default: class {
    async layout(graph: { children: Array<{ id: string }> }) {
      return {
        ...graph,
        children: graph.children.map((child, index) => ({ ...child, x: index * 420, y: 20 })),
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
    ReactFlow: ({ nodes, nodeTypes, onInit, onNodeClick }: any) => {
      useEffect(() => {
        onInit({
          fitView: async () => undefined,
          setViewport: async () => undefined,
          getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
        });
      }, [onInit]);
      return <div data-testid="flow">{nodes.filter((node: any) => !node.hidden).map((node: any) => {
        const Component = nodeTypes[node.type];
        return <div key={node.id} onClick={(event) => onNodeClick?.(event, node)}><Component id={node.id} data={node.data} selected={false} /></div>;
      })}</div>;
    },
    useEdgesState: (initial: any[]) => {
      const [value, setValue] = React.useState(initial);
      return [value, setValue, vi.fn()];
    },
    useNodesState: (initial: any[]) => {
      const [value, setValue] = React.useState(initial);
      return [value, setValue, vi.fn()];
    },
    useUpdateNodeInternals: () => vi.fn(),
  };
});

import { SchemaMap } from "./SchemaMap";

const schema = {
  generated_at: "2026-09-18T12:00:00Z",
  database: "factorlab",
  schema_fingerprint: "a".repeat(64),
  tables: [
    {
      name: "ref_countries",
      domain: "Reference",
      engine: "ReplacingMergeTree",
      stored_rows: 2,
      bytes_on_disk: 100,
      primary_key: "country_code",
      sorting_key: "country_code",
      partition_key: "",
      columns: [
        { name: "country_code", type: "FixedString(2)", position: 1, nullable: false, default_kind: null, default_expression: null, in_primary_key: true, in_sorting_key: true, in_partition_key: false },
        { name: "name", type: "String", position: 2, nullable: false, default_kind: null, default_expression: null, in_primary_key: false, in_sorting_key: false, in_partition_key: false },
      ],
    },
    {
      name: "ref_exchanges",
      domain: "Reference",
      engine: "ReplacingMergeTree",
      stored_rows: 3,
      bytes_on_disk: 200,
      primary_key: "exchange_code",
      sorting_key: "exchange_code",
      partition_key: "",
      columns: [
        { name: "exchange_code", type: "String", position: 1, nullable: false, default_kind: null, default_expression: null, in_primary_key: true, in_sorting_key: true, in_partition_key: false },
        { name: "country_code", type: "FixedString(2)", position: 2, nullable: false, default_kind: null, default_expression: null, in_primary_key: false, in_sorting_key: false, in_partition_key: false },
      ],
    },
  ],
  relationships: [{
    id: "ref_exchanges.country_code->ref_countries.country_code",
    source: { table: "ref_exchanges", column: "country_code" },
    target: { table: "ref_countries", column: "country_code" },
    kind: "logical",
    cardinality: "many_to_one",
    optional: false,
    enforced: false,
  }],
  layout: { revision: 0, schema_fingerprint: "", nodes: [], viewport: { x: 0, y: 0, zoom: 1 }, updated_at: null },
  warnings: [],
};

describe("SchemaMap", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("starts compact, shows full table details on selection, and explicitly saves layout changes", async () => {
    const savedLayout = { ...schema.layout, revision: 1, schema_fingerprint: schema.schema_fingerprint };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => schema })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => savedLayout });
    vi.stubGlobal("fetch", fetchMock);

    render(<SchemaMap />);

    expect(await screen.findByText("ref_countries")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show columns" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fullscreen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand ref_countries" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Export"));
    expect(screen.getByRole("button", { name: "Markdown (.md)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "JSON (.json)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Layout saved" })).toBeDisabled();

    fireEvent.click(screen.getByText("ref_countries"));
    const inspector = screen.getByRole("complementary", { name: "ref_countries details" });
    expect(inspector).toHaveTextContent("country_code");
    expect(inspector).toHaveTextContent("FixedString(2)");
    expect(inspector).toHaveTextContent("FK");
    expect(inspector).toHaveTextContent("PK");

    fireEvent.click(screen.getByRole("button", { name: "Expand ref_countries" }));
    fireEvent.click(screen.getByRole("button", { name: "Save layout" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [, request] = fetchMock.mock.calls[1];
    const body = JSON.parse(request.body);
    expect(request.method).toBe("PUT");
    expect(body.base_revision).toBe(0);
    expect(body.nodes).toHaveLength(2);
    expect(body.nodes.find((node: { table: string }) => node.table === "ref_countries").collapsed).toBe(false);
    expect(await screen.findByText("Shared layout saved.")).toBeInTheDocument();
  });

  it("opens the schema board in fullscreen", async () => {
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => schema }));

    render(<SchemaMap />);
    await screen.findByText("ref_countries");
    fireEvent.click(screen.getByRole("button", { name: "Fullscreen" }));

    await waitFor(() => expect(requestFullscreen).toHaveBeenCalledTimes(1));
    Reflect.deleteProperty(HTMLElement.prototype, "requestFullscreen");
  });

  it("loads the multi-database v2 map from its independent endpoint", async () => {
    const v2Schema = {
      ...schema,
      database: "factorlab_v2",
      tables: schema.tables.map((table) => ({ ...table, name: `ref.${table.name}` })),
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => v2Schema });
    vi.stubGlobal("fetch", fetchMock);

    render(<SchemaMap version="v2" />);

    expect(await screen.findByText("V2 schema preview")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "V2 preview" })).toHaveClass("active");
    expect(fetchMock).toHaveBeenCalledWith(
      "/hub/api/v1/schema-map/v2",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });
});
