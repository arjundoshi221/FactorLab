import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("defaults to all instruments, retains inactive duplicates and bookmarks selection", async () => {
  const fetcher = vi.fn(async (url: string) => response(url.includes("/coverage") ? coverage : url.includes(item.id) ? detail() : { items: [item, second], total: 2 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  expect(screen.getByRole("button", { name: "All tickers" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(await screen.findByRole("button", { name: "SAME · NYSE" }));
  expect(await screen.findByRole("heading", { name: "SAME · NYSE" })).toBeInTheDocument();
  expect(window.location.search).toContain(`id=${item.id}`);
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
  render(<MarketData />);
  fireEvent.click(await screen.findByRole("button", { name: "SAME · NYSE" }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "SAME · NASDAQ" }));
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
