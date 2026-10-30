import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./components/us/USDashboardPanel", () => ({
  USDashboardPanel: () => <a href="/us">US universe and collection</a>,
}));
vi.mock("./pages/USMarkets", () => ({ USMarkets: () => null }));
vi.mock("./pages/schema/SchemaExplorer", () => ({ SchemaExplorer: () => <h1>Schema map canvas</h1> }));
vi.mock("./pages/DockerImages", () => ({ DockerImages: () => <h1>Docker image inventory</h1> }));

const overview = {
  generated_at: "2026-08-20T05:30:00Z",
  summary: { database: "factorlab", table_count: 2, populated_tables: 1, stored_rows: 18605, bytes_on_disk: 397783, healthy_tables: 1, attention_tables: 0 },
  india: { trading_date: "2026-08-20", market_status: "open", expected_series: 10, data_points: 1000, expected_data_points: 1060, coverage_percent: 94.34, last_ingested_at: "2026-08-20T05:29:00Z", freshness_seconds: 60, status: "attention", status_reason: "94.3% coverage; the 95% target is not currently met." },
  political: { checked_at: "2026-08-20T05:30:00Z", datasets_with_data: 1, fresh_datasets: 1, stale_datasets: 0, last_ingested_at: "2026-08-20T01:00:00Z", status: "healthy", status_reason: "All populated political datasets are within their freshness window." },
  tables: [
    { name: "market_candles_1min", domain: "India market", category: "Market data", engine: "ReplacingMergeTree", stored_rows: 18605, bytes_on_disk: 397783, count_kind: "stored", first_data_at: "2026-08-01T00:00:00Z", last_data_at: "2026-08-20T05:29:00Z", last_ingested_at: "2026-08-20T05:29:00Z", today_rows: 1000, today_status: "attention", status_reason: "Coverage below target." },
    { name: "market_candles_daily", domain: "Global market", category: "Market data", engine: "ReplacingMergeTree", stored_rows: 0, bytes_on_disk: 0, count_kind: "stored", first_data_at: null, last_data_at: null, last_ingested_at: null, today_rows: 0, today_status: "not_configured", status_reason: "No active producer is configured for this table yet." },
  ],
  warnings: [],
};

const SHELL_FEEDS = ["/hub/api/v1/monitoring/summary", "/hub/api/v1/us/indexes", "/hub/api/v1/us/futures"];
const pageCalls = () => vi.mocked(fetch).mock.calls.map(([url]) => String(url)).filter((url) => !SHELL_FEEDS.includes(url));
const overviewCalls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/hub/api/v1/overview");

describe("FactorLab Hub", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/ops");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => overview }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("renders live health and all tables", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Ops overview" })).toBeInTheDocument();
    expect(screen.getByText("18.6K")).toBeInTheDocument();
    expect(screen.getByText("market_candles_1min")).toBeInTheDocument();
    expect(screen.getByText("market_candles_daily")).toBeInTheDocument();
    expect(screen.getByText("US universe and collection")).toBeInTheDocument();
    expect(screen.getAllByText("Needs attention").length).toBeGreaterThan(0);
  });

  it("shows the API version in the footer when the build carries one", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({
      ...overview, build: { release_id: null, commit: "c".repeat(40), component: "api", version: "1.2.0" },
    }) } as Response);
    render(<App />);
    expect(await screen.findByRole("link", { name: "v1.2.0" })).toHaveAttribute("href", "/docker-images");
  });

  it("filters the table inventory", async () => {
    render(<App />);
    await screen.findByText("market_candles_1min");
    fireEvent.change(screen.getByPlaceholderText("Search table or domain"), { target: { value: "daily" } });
    expect(screen.queryByText("market_candles_1min")).not.toBeInTheDocument();
    expect(screen.getByText("market_candles_daily")).toBeInTheDocument();
  });

  it("filters by status, hides empty tables, and shows the running build", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({
      ...overview,
      build: { release_id: "20260925T050000Z-abcdef123456", commit: "a".repeat(40) },
      summary: { ...overview.summary, view_count: 1 },
      tables: [...overview.tables, { ...overview.tables[1], name: "research.bars", kind: "view", engine: "View", category: "View", today_status: "not_expected" }],
    }) } as Response);
    render(<App />);
    await screen.findByText("market_candles_1min");
    expect(screen.getByText("Plus 1 research views")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "20260925T050000Z-abcdef123456" })).toHaveAttribute("href", "/docker-images");
    expect(screen.getByText("View · View")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "attention" } });
    expect(screen.getByText("market_candles_1min")).toBeInTheDocument();
    expect(screen.queryByText("market_candles_daily")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "all" } });
    fireEvent.click(screen.getByLabelText("Hide empty tables"));
    expect(screen.queryByText("market_candles_daily")).not.toBeInTheDocument();
    expect(screen.queryByText("research.bars")).not.toBeInTheDocument();
    expect(screen.getByText("market_candles_1min")).toBeInTheDocument();
  });

  it("keeps the last snapshot when refresh fails", async () => {
    const fetchMock = vi.mocked(fetch)
      .mockImplementation(async () => ({ ok: true, json: async () => overview }) as Response);
    render(<App />);
    await screen.findByText("market_candles_1min");
    fetchMock.mockImplementation(async () => { throw new Error("offline"); });
    fireEvent.click(screen.getByRole("button", { name: "Refresh data" }));
    await waitFor(() => expect(screen.getByText(/Live data is temporarily unavailable/)).toBeInTheDocument());
    expect(screen.getByText("market_candles_1min")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/hub/api/v1/overview")).toHaveLength(2);
  });

  it("polls once per minute while visible", async () => {
    vi.useFakeTimers();
    render(<App />);
    await act(async () => Promise.resolve());
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(overviewCalls()).toHaveLength(2);
  });

  it("renders the roadmap route without fetching data", () => {
    window.history.replaceState({}, "", "/roadmap");
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "From data confidence to research execution." })).toBeInTheDocument();
    expect(screen.getByText("Backtesting")).toBeInTheDocument();
    expect(pageCalls()).toEqual([]);
  });

  it("routes Docker images without loading the dashboard", () => {
    window.history.replaceState({}, "", "/docker-images");
    render(<App />);
    expect(screen.getByText("Docker image inventory")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Images" })).toHaveClass("active");
    expect(pageCalls()).toEqual([]);
  });

  it("groups navigation into Data, Portfolio and Ops", () => {
    window.history.replaceState({}, "", "/docker-images");
    render(<App />);
    const primary = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    expect(primary.getAllByRole("link").map((link) => link.textContent)).toEqual(["Home", "Data", "Portfolio", "Ops"]);
    expect(primary.getByRole("link", { name: "Ops" })).toHaveClass("active");
    expect(primary.getByRole("link", { name: "Data" })).toHaveAttribute("href", "/market-data");
    expect(primary.getByRole("link", { name: "Portfolio" })).toHaveAttribute("href", "/portfolio");
    const section = within(screen.getByRole("navigation", { name: "Section navigation" }));
    expect(section.getByRole("link", { name: "Pipelines" })).toHaveAttribute("href", "/data/pipelines");
    expect(section.getByRole("link", { name: "Monitoring" })).toHaveAttribute("href", "/monitoring");
    expect(section.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/ops");
    expect(screen.getByRole("link", { name: "FactorLab home" })).toHaveAttribute("href", "/");
  });

  it("lists the catalog and schema under Data", () => {
    window.history.replaceState({}, "", "/schema");
    render(<App />);
    const section = within(screen.getByRole("navigation", { name: "Section navigation" }));
    expect(section.getByRole("link", { name: "Catalog" })).toHaveAttribute("href", "/data");
    expect(section.getByRole("link", { name: "Schema" })).toHaveClass("active");
    expect(within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole("link", { name: "Data" })).toHaveClass("active");
  });

  it("redirects the old indexes page into Market Data", () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: "Down" }) } as Response);
    window.history.replaceState({}, "", "/indexes");
    render(<App />);
    expect(window.location.pathname + window.location.search).toBe("/market-data?view=indexes");
    expect(screen.getByRole("link", { name: "Indexes" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "Markets" })).not.toHaveClass("active");
    expect(screen.getByRole("button", { name: "Indexes" })).toHaveAttribute("aria-pressed", "true");
  });

  it("opens /futures as the Market Data futures view", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ generated_at: new Date().toISOString(), market_open: false, items: [] }) } as Response);
    window.history.replaceState({}, "", "/futures");
    render(<App />);
    expect(window.location.pathname + window.location.search).toBe("/market-data?view=futures");
    expect(screen.getByRole("button", { name: "Futures" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("No futures recorded yet")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === "/hub/api/v1/us/futures")).toBe(true);
  });

  it("routes data pages without loading the ops dashboard", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: "Catalog down" }) } as Response);
    window.history.replaceState({}, "", "/data/tables/market.bars");
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Catalog down");
    expect(pageCalls().every((url) => url.startsWith("/hub/api/v1/catalog"))).toBe(true);
  });

  it("renders the schema map route without loading the dashboard", async () => {
    window.history.replaceState({}, "", "/schema");
    render(<App />);
    expect(await screen.findByText("Schema map canvas")).toBeInTheDocument();
    expect(pageCalls()).toEqual([]);
  });

  it("renders the v2 schema map route without loading the dashboard", async () => {
    window.history.replaceState({}, "", "/schema/v2");
    render(<App />);
    expect(await screen.findByText("Schema map canvas")).toBeInTheDocument();
    expect(pageCalls()).toEqual([]);
  });
});
