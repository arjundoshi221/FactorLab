import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CatalogIndex, CatalogTableDetail, CatalogTableSummary, PipelinesResponse, RowsPage } from "../../dataTypes";
import { formatRange, formatRelative } from "../../shared/format";
import { DataHome } from "./DataHome";
import { Pipelines } from "./Pipelines";
import { TableDetail } from "./TableDetail";

const NOW = "2026-09-25T05:00:00+00:00";

const bars: CatalogTableSummary = {
  name: "market.bars", namespace: "market", title: "Price bars", summary: "Open, high, low, close, and volume.",
  kind: "table", engine: "ReplacingMergeTree", stored_rows: 10_000_000, bytes_on_disk: 900_000_000,
  first_data_at: "1985-01-02T00:00:00+00:00", last_data_at: "2026-09-25T04:55:00+00:00",
  last_ingested_at: "2026-09-25T04:56:00+00:00", status: "not_expected", status_reason: "Tracked per market.",
  column_count: 3, columns: ["bar_time", "close", "source"], column_notes: { close: "Closing price" }, previewable: true,
};

const catalog: CatalogIndex = {
  generated_at: NOW,
  previews_enabled: true,
  csv_enabled: true,
  namespaces: [
    { id: "market", title: "Market data", summary: "Prices.", table_count: 2, populated_tables: 1, view_count: 0,
      stored_rows: 10_000_000, bytes_on_disk: 900_000_000, status_counts: { not_expected: 1, not_configured: 1 } },
    { id: "alt", title: "Alternative data", summary: "Politics.", table_count: 1, populated_tables: 1, view_count: 0,
      stored_rows: 500, bytes_on_disk: 1000, status_counts: { healthy: 1 } },
  ],
  tables: [
    { ...bars },
    { ...bars, name: "market.options_bars", title: "Options bars", summary: "Reserved.", stored_rows: 0, bytes_on_disk: 0,
      first_data_at: null, last_data_at: null, last_ingested_at: null, status: "not_configured", columns: ["strike"], column_notes: {} },
    { ...bars, name: "alt.political_trades", namespace: "alt", title: "Congressional trades", summary: "Disclosed trades.",
      stored_rows: 500, status: "healthy", columns: ["ticker_raw", "amount_min"], column_notes: { ticker_raw: "The ticker as filed" } },
  ],
};

const pipelines: PipelinesResponse = {
  generated_at: NOW,
  sources: [{ country_code: "US", source: "schwab", status: "ready", detail: "503 equities", checked_at: NOW }],
  pipelines: [
    {
      id: "india_intraday_1min", label: "India intraday bars", market: "India", source: "Upstox", description: "1-minute bars.",
      schedule: "Every 5 minutes", scheduled: true, per_symbol_runs: false, tables_written: ["market.bars", "meta.ingestion_runs"],
      status: "healthy", status_reason: "Last run 4 min ago (success).", last_status: "success",
      last_started_at: "2026-09-25T04:56:00+00:00", last_completed_at: NOW, runs_24h: 60, success_24h: 60, problem_24h: 0,
      runs_7d: 300, success_7d: 298, problem_7d: 2, rows_24h: 812_000,
      days: [{ day: "2026-09-25", success: 60, problem: 0, total: 60 }], recent_problems: [],
    },
    {
      id: "political_bootstrap", label: "US political disclosures", market: "Political", source: "House Clerk", description: "PTRs.",
      schedule: "Daily at 02:15 UTC", scheduled: true, per_symbol_runs: false, tables_written: ["alt.political_trades"],
      status: "attention", status_reason: "The latest run failed (3 h ago): PDF parse failed", last_status: "failed",
      last_started_at: "2026-09-25T02:15:00+00:00", last_completed_at: null, runs_24h: 1, success_24h: 0, problem_24h: 1,
      runs_7d: 7, success_7d: 6, problem_7d: 1, rows_24h: 0, days: [],
      recent_problems: [{ run_id: "r9", pipeline: "political_bootstrap", source: "political", status: "failed",
        started_at: "2026-09-25T02:15:00+00:00", completed_at: null, rows_written: 0, requested_series: 1,
        successful_series: 0, failed_series: 1, error: "PDF parse failed" }],
    },
  ],
};

const detail: CatalogTableDetail = {
  ...bars,
  design_notes: "The primary market data table.",
  notes: ["Filter by resolution."],
  keys: { primary_key: "", sorting_key: "listing_id, bar_time", partition_key: "toYYYYMM(bar_time)",
    explanation: ["Each row is identified by (listing_id, bar_time). When a row is re-collected, the newer version replaces the older one."] },
  column_details: [
    { name: "bar_time", type: "DateTime64(3, 'UTC')", friendly_type: "Timestamp (UTC)", type_class: "datetime", description: "Bar start",
      nullable: false, in_primary_key: false, in_sorting_key: true, in_partition_key: true, hidden: false, operators: ["gte", "lt", "notnull", "null"] },
    { name: "close", type: "Nullable(Decimal(18, 6))", friendly_type: "Decimal number", type_class: "decimal", description: "Closing price",
      nullable: true, in_primary_key: false, in_sorting_key: false, in_partition_key: false, hidden: false,
      operators: ["eq", "gt", "gte", "lt", "lte", "neq", "notnull", "null"] },
    { name: "source", type: "LowCardinality(String)", friendly_type: "Text", type_class: "text", description: null,
      nullable: false, in_primary_key: false, in_sorting_key: false, in_partition_key: false, hidden: false,
      operators: ["contains", "eq", "in", "neq", "notnull", "null", "prefix"] },
  ],
  related: [{ direction: "out", column: "listing_id", table: "ref.listings", table_title: "Listings", target_column: "listing_id" }],
  writers: [{ id: "india_intraday_1min", label: "India intraday bars", schedule: "Every 5 minutes" }],
  preview: { enabled: true, reason: null, csv_enabled: true, windowed: true, time_column: "bar_time",
    default_start: "2026-09-24T04:55:01+00:00", default_end: "2026-09-25T04:55:01+00:00", max_window_days: 31,
    latest_version_only: true, hidden_columns: [], max_csv_rows: 10_000, max_page_rows: 200 },
};

const rows: RowsPage = {
  table: "market.bars", columns: ["bar_time", "close", "source"],
  rows: [["2026-09-25T04:55:00+00:00", "101.500000", "upstox"], ["2026-09-25T04:54:00+00:00", null, "upstox"]],
  offset: 0, limit: 50, has_more: true, window_start: "2026-09-24T04:55:01+00:00", window_end: "2026-09-25T04:55:01+00:00",
  sort: "bar_time", descending: true, latest_version_only: true, truncated_cells: 0, notes: ["Shows the latest version of each row."],
};

function respond(routes: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const match = Object.keys(routes).find((key) => url.startsWith(key));
    if (!match) return { ok: false, status: 404, json: async () => ({ detail: "not found" }) } as Response;
    const body = routes[match];
    if (body instanceof Error) return { ok: false, status: 400, json: async () => ({ detail: body.message }) } as Response;
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

beforeEach(() => window.history.replaceState({}, "", "/data"));
afterEach(() => vi.unstubAllGlobals());

describe("data catalog home", () => {
  it("shows areas, collection status, and populated tables, hiding reserved ones by default", async () => {
    vi.stubGlobal("fetch", respond({ "/hub/api/v1/catalog/pipelines": pipelines, "/hub/api/v1/catalog": catalog }));
    render(<DataHome />);
    expect(await screen.findByRole("heading", { name: "Find the data you need." })).toBeInTheDocument();
    const results = screen.getByRole("heading", { name: "Tables with data" }).closest("section")!;
    expect(within(results).getByText("Price bars")).toBeInTheDocument();
    expect(within(results).queryByText("Options bars")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show 1 reserved table/ }));
    expect(within(results).getByText("Options bars")).toBeInTheDocument();
    const now = await screen.findByRole("heading", { name: "Collecting now" });
    expect(within(now.closest("section")!).getByText("India intraday bars")).toBeInTheDocument();
  });

  it("searches column names and descriptions and filters by area through the URL", async () => {
    vi.stubGlobal("fetch", respond({ "/hub/api/v1/catalog/pipelines": pipelines, "/hub/api/v1/catalog": catalog }));
    render(<DataHome />);
    const search = await screen.findByRole("searchbox", { name: "Search tables and columns" });
    fireEvent.change(search, { target: { value: "ticker" } });
    expect(window.location.search).toBe("?q=ticker");
    const card = screen.getByText("Congressional trades").closest("a")!;
    expect(within(card).getByText("ticker_raw")).toBeInTheDocument();
    expect(screen.queryByText("Price bars")).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "" } });
    fireEvent.click(within(screen.getByRole("navigation", { name: "Filter by area" })).getByRole("button", { name: /Alternative data/ }));
    expect(window.location.search).toBe("?ns=alt");
    expect(screen.getByRole("heading", { name: "Alternative data" })).toBeInTheDocument();
  });

  it("explains a failed load and retries", async () => {
    const fetchMock = respond({});
    vi.stubGlobal("fetch", fetchMock);
    render(<DataHome />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not found");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2));
  });
});

describe("table detail", () => {
  it("opens the tab named in the URL and explains the table", async () => {
    window.history.replaceState({}, "", "/data/tables/market.bars");
    vi.stubGlobal("fetch", respond({ "/hub/api/v1/catalog/tables/market.bars": detail }));
    render(<TableDetail name="market.bars" />);
    expect(await screen.findByRole("heading", { name: "Price bars" })).toBeInTheDocument();
    expect(screen.getByText("Filter by resolution.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Listings" })).toHaveAttribute("href", "/data/tables/ref.listings");
    expect(screen.getByRole("link", { name: "India intraday bars" })).toHaveAttribute("href", "/data/pipelines#india_intraday_1min");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("previews rows, builds URL filters, sorts, and links a matching CSV", async () => {
    window.history.replaceState({}, "", "/data/tables/market.bars?tab=preview");
    const fetchMock = respond({
      "/hub/api/v1/catalog/tables/market.bars/rows": rows,
      "/hub/api/v1/catalog/tables/market.bars": detail,
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<TableDetail name="market.bars" />);
    expect(await screen.findByText("101.500000")).toBeInTheDocument();
    expect(screen.getAllByText("upstox")).toHaveLength(2);
    expect(screen.getByText("empty")).toBeInTheDocument();
    expect(screen.getByText("Shows the latest version of each row.")).toBeInTheDocument();

    const builder = screen.getByRole("form", { name: "Add a filter" });
    fireEvent.change(within(builder).getByLabelText("Column"), { target: { value: "source" } });
    fireEvent.change(within(builder).getByLabelText("Value"), { target: { value: "upstox" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Add filter" }));
    expect(new URLSearchParams(window.location.search).get("f.source")).toBe("eq:upstox");
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("f.source=eq%3Aupstox"))).toBe(true));
    expect(screen.getByRole("link", { name: /Download CSV/ })).toHaveAttribute(
      "href", "/hub/api/v1/catalog/tables/market.bars/rows.csv?f.source=eq%3Aupstox",
    );

    fireEvent.click(screen.getByRole("button", { name: /^close/ }));
    expect(new URLSearchParams(window.location.search).get("sort")).toBe("close");
    fireEvent.click(screen.getByRole("button", { name: "Remove filter on source" }));
    expect(new URLSearchParams(window.location.search).get("f.source")).toBeNull();
  });

  it("shows why previews are unavailable and surfaces API errors", async () => {
    window.history.replaceState({}, "", "/data/tables/market.bars?tab=preview");
    vi.stubGlobal("fetch", respond({
      "/hub/api/v1/catalog/tables/market.bars/rows": new Error("Narrow the date range or add a filter."),
      "/hub/api/v1/catalog/tables/market.bars": detail,
    }));
    render(<TableDetail name="market.bars" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Narrow the date range");

    vi.stubGlobal("fetch", respond({
      "/hub/api/v1/catalog/tables/market.bars": { ...detail, preview: { ...detail.preview, enabled: false, reason: "Row previews are disabled on this hub." } },
    }));
    window.history.replaceState({}, "", "/data/tables/market.bars?tab=preview");
    render(<TableDetail name="market.bars" />);
    expect(await screen.findByText("Row previews are disabled on this hub.")).toBeInTheDocument();
  });
});

describe("pipelines", () => {
  it("orders problems first and explains each pipeline against its schedule", async () => {
    vi.stubGlobal("fetch", respond({ "/hub/api/v1/catalog/pipelines": pipelines }));
    render(<Pipelines />);
    const cards = await screen.findAllByRole("article");
    expect(within(cards[0]).getByRole("heading", { name: "US political disclosures" })).toBeInTheDocument();
    expect(within(cards[0]).getByText(/PDF parse failed/, { selector: ".data-pipeline__reason" })).toBeInTheDocument();
    expect(within(cards[1]).getByText("8.1L")).toBeInTheDocument();
    expect(screen.getByText("503 equities")).toBeInTheDocument();
    expect(screen.getByText("need attention", { exact: false, selector: ".data-hero__facts span" })).toHaveTextContent("1 need attention");
  });
});

describe("formatters", () => {
  it("formats relative times and ranges", () => {
    const now = new Date(NOW).valueOf();
    expect(formatRelative("2026-09-25T04:57:00+00:00", now)).toBe("3 min ago");
    expect(formatRelative(null, now)).toBe("never");
    expect(formatRange(null, null)).toBe("No data yet");
    expect(formatRange("2026-09-24", "2026-09-24")).toBe(formatRange("2026-09-24", null).split(" →")[0]);
  });
});
