import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { summarize, Ticker } from "./Ticker";
import type { PriceBar } from "../instrumentTypes";

const ID = "00000000-0000-0000-0000-00000000000a";
const OTHER = "00000000-0000-0000-0000-00000000000b";
const instrument = { kind: "listing", id: ID, listing_id: ID, security_id: "s", entity_id: "e", symbol: "ABBV", name: "AbbVie Inc", exchange: "NYSE", country: "US", instrument_type: "common", active: true, configured: true, expiry: null, strike: null, right: "", sector_id: "35", sector: "Health Care" };
const identity = { instrument, aliases: [], identifiers: {}, warnings: [], datasets: [], related: [{ ...instrument, id: OTHER, exchange: "XETRA" }] };
const bar = (t: string, close: number, volume = 100): PriceBar => ({ t, open: close - 1, high: close + 1, low: close - 2, close, volume });
const daily = [bar("2025-10-30", 100), bar("2026-09-29", 180), bar("2026-10-28", 190), bar("2026-10-29", 200, 300)];
const prices = (range: string) => ({ listing_id: ID, range, resolution: "daily", source: "schwab", derived: false, bars: daily });
const matrix = {
  columns: [{ key: "trades", label: "Trades", group: "broker", dataset: "broker.executions", level: "order", country: null, source: null, resolution: null, has_intent: false }],
  items: [{ instrument, cells: {
    trades: { state: "ok", latest: "2026-10-01T00:00:00Z", count: 4, detail: "4 orders", items: [] },
    positions: { state: "ok", latest: null, count: 1, detail: "", items: [{ broker_code: "ibkr", account_id: "U1", account_mode: "live", currency: "USD", position: 10, market_value: 2000, snapshot_time: "2026-10-29T20:00:00Z" }] },
  } }],
  total: 1, limit: 1, offset: 0, generated_at: "", expected_sessions: {}, unavailable: [],
};
const response = (body: unknown) => ({ ok: true, json: async () => body });

beforeEach(() => window.history.replaceState(null, "", `/market-data/tickers/${ID}`));
afterEach(() => vi.unstubAllGlobals());

it("shows the price, stats, position and links instead of database tables", async () => {
  const fetcher = vi.fn(async (url: string) => {
    const range = new URL(url, "http://x").searchParams.get("range");
    return response(url.includes("/prices") ? prices(range!) : url.includes("/matrix") ? matrix : identity);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Ticker listingId={ID} />);
  expect(await screen.findByRole("heading", { name: "AbbVie Inc" })).toBeInTheDocument();
  expect(await screen.findByText("$200.00")).toBeInTheDocument();
  expect(screen.getByText("+10.00 (+5.26%)")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Peers →" })).toHaveAttribute("href", "/market-data?view=sectors&sector=35");
  expect(screen.getByRole("link", { name: "ABBV · XETRA" })).toHaveAttribute("href", `/market-data/tickers/${OTHER}`);
  const position = screen.getByRole("region", { name: "Your position" });
  expect(await within(position).findByText("IBKR U1")).toBeInTheDocument();
  expect(within(position).getByRole("link", { name: "Ledger →" })).toHaveAttribute("href", `/portfolio/trades?listing_id=${ID}`);
  expect(screen.queryByText(/ref\.listings/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "3M" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/prices?range=3m"), expect.anything()));
  expect(window.location.search).toBe("?range=3m");
});

it("summarizes returns close to close and skips periods without history", () => {
  const summary = summarize(daily)!;
  expect(summary.change).toBe(10);
  expect(Object.fromEntries(summary.returns)).toEqual({ "1M": 200 / 180 - 1, "3M": 200 / 100 - 1, YTD: 200 / 100 - 1, "1Y": null });
  expect(summary.high).toBe(201);
  expect(summarize([bar("2026-10-29", 5)])!.returns.every(([, value]) => value === null)).toBe(true);
});
