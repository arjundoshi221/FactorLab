import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarketData } from "./InstrumentExplorer";

const item = { kind: "listing", id: "00000000-0000-0000-0000-000000000001", listing_id: "l", security_id: "s", entity_id: "e", symbol: "SAME", name: "Company", exchange: "NYSE", country: "US", instrument_type: "common", active: false, configured: false };
const second = { ...item, id: "00000000-0000-0000-0000-000000000002", exchange: "NASDAQ" };
const dataset = { table: "market.bars", title: "Bars", description: "Physical bars", group: "Market", classification: "queryable", relationships: ["direct"], dimensions: ["source"], notes: [], writers: [] };
const detail = (instrument = item) => ({ instrument, aliases: [], identifiers: {}, related: [second], warnings: [], datasets: [dataset] });
const coverage = { table: "market.bars", state: "empty", checked_at: "2026-10-16T00:00:00Z", groups: [], message: "", rows: [], columns: [], evidence: {}, has_more: false };
const response = (body: unknown) => ({ ok: true, json: async () => body });

beforeEach(() => window.history.replaceState(null, "", "/market-data"));
afterEach(() => vi.unstubAllGlobals());

it("defaults to all instruments, retains inactive duplicates and links each to its ticker page", async () => {
  const fetcher = vi.fn(async () => response({ items: [item, second], total: 2 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  expect(screen.getByRole("button", { name: "All tickers" })).toHaveAttribute("aria-pressed", "true");
  expect(await screen.findByRole("link", { name: "SAME · NYSE" })).toHaveAttribute("href", `/market-data/tickers/${item.id}`);
  expect(screen.getByRole("link", { name: "SAME · NASDAQ" })).toHaveAttribute("href", `/market-data/tickers/${second.id}`);
});

it("still opens the raw record view from a bookmarked link", async () => {
  window.history.replaceState(null, "", `/market-data?kind=listing&id=${item.id}`);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(url.includes("/coverage") ? coverage : url.includes(item.id) ? detail() : { items: [], total: 0 })));
  render(<MarketData />);
  expect(await screen.findByRole("heading", { name: "SAME · NYSE" })).toBeInTheDocument();
  expect(await screen.findByText("No matching records")).toBeInTheDocument();
  expect(screen.getByText(/Collection membership: Not configured/)).toBeInTheDocument();
});

it("loads shareable filters and restores browser back state", async () => {
  window.history.replaceState(null, "", "/market-data?search=alias&active=no&exchange=NYSE");
  const fetcher = vi.fn(async () => response({ items: [], total: 0 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  expect(screen.getByRole("searchbox")).toHaveValue("alias");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "other" } });
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("search=other"), expect.anything()));
  act(() => { window.history.replaceState(null, "", "/market-data?search=alias&active=no"); window.dispatchEvent(new PopStateEvent("popstate")); });
  expect(screen.getByRole("searchbox")).toHaveValue("alias");
  expect(screen.getByLabelText("Active status")).toHaveValue("no");
});

it("keeps independent coverage failures isolated and opens paginated previews", async () => {
  window.history.replaceState(null, "", `/market-data?kind=listing&id=${item.id}`);
  const fetcher = vi.fn(async (url: string) => {
    if (url.includes("market.bars/coverage")) return { ok: false, status: 429, json: async () => ({ detail: "Busy" }) };
    if (url.includes("/coverage")) return response(coverage);
    if (url.includes("/rows")) return response({ ...coverage, state: "stored", rows: [["value"]], columns: ["source"], has_more: true });
    if (url.includes(item.id)) return response({ ...detail(), datasets: [dataset, { ...dataset, table: "market.quotes", title: "Quotes" }] });
    return response({ items: [], total: 0 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  expect(await screen.findByText("Busy")).toBeInTheDocument();
  expect(await screen.findByText("No matching records")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Inspect records" })[0]);
  expect(await screen.findByText("value")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next records" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("offset=50"), expect.anything()));
});

it("does not display a late identity response after rapid ticker switching", async () => {
  let finish: ((r: ReturnType<typeof response>) => void) | undefined;
  const fetcher = vi.fn(async (url: string) => {
    if (url.includes("/coverage")) return response(coverage);
    if (url.includes(item.id)) return new Promise<ReturnType<typeof response>>(resolve => { finish = resolve; });
    if (url.includes(second.id)) return response(detail(second));
    return response({ items: [item, second], total: 2 });
  });
  vi.stubGlobal("fetch", fetcher);
  window.history.replaceState(null, "", `/market-data?kind=listing&id=${item.id}`);
  render(<MarketData />);
  await waitFor(() => expect(finish).toBeDefined());
  act(() => { window.history.pushState(null, "", `/market-data?kind=listing&id=${second.id}`); window.dispatchEvent(new PopStateEvent("popstate")); });
  expect(await screen.findByRole("heading", { name: "SAME · NASDAQ" })).toBeInTheDocument();
  await act(async () => { finish!(response(detail())); });
  expect(screen.queryByRole("heading", { name: "SAME · NYSE" })).not.toBeInTheDocument();
});

it("keeps the existing collection screen reachable", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(url.includes("universe") ? { market: "US", stream: "daily", configured_total: 0, total: 0, items: [], limit: 50, offset: 0 } : { items: [], total: 0 })));
  render(<MarketData />);
  fireEvent.click(screen.getByRole("button", { name: "Collection coverage" }));
  expect(screen.getByRole("button", { name: "Historical recovery" })).toBeInTheDocument();
  expect(window.location.search).toContain("view=collection");
});

const sectors = { items: [{ sector_id: "10", sector: "Energy", count: 4 }, { sector_id: "45", sector: "Information Technology", count: 2 }], unclassified: 3, total: 9 };

it("groups listings by GICS sector and lists a sector's tickers on demand", async () => {
  const tech = { ...item, sector_id: "45", sector: "Information Technology" };
  const fetcher = vi.fn(async (url: string) => response(url.includes("/sectors") ? sectors
    : url.includes("/coverage") ? coverage : url.includes(item.id) ? detail(tech) : { items: [tech], total: 1 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  fireEvent.click(screen.getByRole("button", { name: "By sector" }));
  const before = fetcher.mock.calls.length;
  expect(window.location.search).toContain("view=sectors");
  const list = within(await screen.findByRole("group", { name: "Sectors" }));
  expect(list.getByText("Energy")).toBeInTheDocument();
  expect(list.getByText("Unclassified")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "Listings by GICS sector" })).getAllByRole("button")).toHaveLength(list.getAllByText(/./, { selector: ".md-sector__name" }).length);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/market-data/sectors?country=US"), expect.anything());
  // Members load only when a sector is opened.
  expect(fetcher.mock.calls.slice(before).some(([url]) => String(url).includes("/instruments?"))).toBe(false);
  fireEvent.click(list.getByText("Information Technology"));
  expect(window.location.search).toContain("sector=45");
  expect(screen.getByRole("button", { name: /^Information Technology: / })).toHaveAttribute("aria-pressed", "true");
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringMatching(/instruments\?.*kind=listing&sector=45/), expect.anything()));
  expect(await screen.findByRole("link", { name: "SAME · NYSE" })).toHaveAttribute("href", `/market-data/tickers/${item.id}`);
});

it("asks for unclassified listings with sector=none", async () => {
  window.history.replaceState(null, "", "/market-data?view=sectors&sector=none");
  const fetcher = vi.fn(async (url: string) => response(url.includes("/sectors") ? sectors : { items: [], total: 0 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("sector=none"), expect.anything()));
  expect(await screen.findByText("No instruments match these filters.")).toBeInTheDocument();
});

it("embeds the indexes dashboard as a tab without a second page heading", async () => {
  window.history.replaceState(null, "", "/market-data?view=indexes");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(url.includes("/sectors/performance")
    ? { as_of: null, weighting: "cw", periods: [], sectors: [], benchmarks: {} }
    : { tracked: [{ code: "$SPX", name: "S&P 500", group: "index", value: null, change: null, trade_date: null, complete: false }], constructed: [], sectors: [], base_date: "2016-01-04", universe: "r3k_iwv" })));
  render(<MarketData />);
  expect(screen.getByRole("button", { name: "Indexes" })).toHaveAttribute("aria-pressed", "true");
  expect(await screen.findByText("S&P 500")).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});
