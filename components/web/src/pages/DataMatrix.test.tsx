import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarketData } from "./InstrumentExplorer";
import type { MatrixPage } from "../matrixTypes";

const instrument = { kind: "listing", id: "00000000-0000-0000-0000-00000000000a", listing_id: "a", security_id: "s", entity_id: "e", symbol: "AAA", name: "Alpha Inc", exchange: "NYSE", country: "US", instrument_type: "common", active: true, configured: true, expiry: null, strike: null, right: "" };
const column = (key: string, label: string, group = "market", dataset = "market.bars") => ({ key, label, group, dataset, level: key === "fundamentals" ? "issuer" : "listing", country: "US", source: "schwab", resolution: "1min", has_intent: true });
const page: MatrixPage = {
  columns: [column("bars:US:schwab:1min", "US · schwab · 1min"), column("bars:US:schwab:daily", "US · schwab · daily"), column("fundamentals", "Fundamentals", "fundamentals", "fundamentals.filings"), column("trades", "Trades", "broker", "broker.executions"), column("tags", "Tags", "tags", "book.order_assignment_events")] as MatrixPage["columns"],
  items: [{ instrument: instrument as MatrixPage["items"][number]["instrument"], cells: {
    "bars:US:schwab:1min": { state: "ok", latest: "2026-10-16", count: null, detail: "Up to date", items: [] },
    "bars:US:schwab:daily": { state: "stale", latest: "2026-10-10", count: null, detail: "Behind: expected 2026-10-16", items: [] },
    fundamentals: { state: "unavailable", latest: null, count: null, detail: "Fundamentals could not be read", items: [] },
    trades: { state: "ok", latest: "2026-10-15T14:00:00Z", count: 4, detail: "4 orders", items: [] },
    tags: { state: "needs_tags", latest: null, count: 1, detail: "1 of 4 orders need tags", items: [] },
  } }],
  total: 1, limit: 50, offset: 0, generated_at: "2026-10-17T00:00:00Z", expected_sessions: { US: "2026-10-16" }, unavailable: ["Fundamentals"],
};
const response = (body: unknown) => ({ ok: true, json: async () => body });

beforeEach(() => window.history.replaceState(null, "", "/market-data?view=matrix"));
afterEach(() => vi.unstubAllGlobals());

it("shows each ticker against each dataset with honest states", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response(page)));
  render(<MarketData />);
  expect(screen.getByRole("button", { name: "Data matrix" })).toHaveAttribute("aria-pressed", "true");
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("button", { name: "AAA US · schwab · 1min: Up to date" })).toHaveTextContent("2026-10-16");
  expect(within(table).getByRole("button", { name: "AAA US · schwab · daily: Behind" })).toBeInTheDocument();
  expect(within(table).getByRole("button", { name: "AAA Fundamentals: Unavailable" })).toHaveTextContent("?");
  expect(screen.getByText(/Could not read: Fundamentals/)).toBeInTheDocument();
  // Trades and tags open the ledger for this ticker.
  expect(within(table).getByText("1 untagged").closest("a")).toHaveAttribute("href", `/portfolio/trades?listing_id=${instrument.id}&tag=untagged`);
  expect(within(table).getByText(/^4 · 2026-10-15/).closest("a")).toHaveAttribute("href", `/portfolio/trades?listing_id=${instrument.id}`);
  expect(screen.getByText(/latest sessions US 2026-10-16/)).toBeInTheDocument();
});

it("turns filters into server-side query parameters and resets paging", async () => {
  window.history.replaceState(null, "", "/market-data?view=matrix&offset=50");
  const fetcher = vi.fn(async () => response({ ...page, total: 120, offset: 50 }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("offset=50"), expect.anything()));
  fireEvent.change(screen.getByLabelText("Needs attention"), { target: { value: "bars" } });
  fireEvent.change(screen.getByLabelText("Country"), { target: { value: "US" } });
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringMatching(/country=US&attention=bars&offset=0/), expect.anything()));
  expect(window.location.search).toContain("attention=bars");
  expect(window.location.search).not.toContain("offset=50");
});

it("opens the ticker with an at-a-glance strip and a link to its trades", async () => {
  const identity = { instrument, aliases: [], identifiers: {}, related: [], warnings: [], datasets: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(
    url.includes("/matrix?listing_id=") ? page : url.includes("/matrix") ? page : url.includes(`/instruments/listing/${instrument.id}`) ? identity : { items: [], total: 0 },
  )));
  render(<MarketData />);
  fireEvent.click(await screen.findByRole("button", { name: "Open AAA" }));
  const glance = await screen.findByRole("region", { name: "At a glance" });
  expect(within(glance).getByText("Fundamentals")).toBeInTheDocument();
  expect(within(glance).getByRole("link", { name: "All trades for this ticker →" })).toHaveAttribute("href", `/portfolio/trades?listing_id=${instrument.id}`);
  expect(within(glance).getByText("US · schwab · daily").parentElement!.querySelector("a")).toHaveAttribute("href", "#dataset-market.bars");
  expect(window.location.search).toContain("view=matrix");
});
