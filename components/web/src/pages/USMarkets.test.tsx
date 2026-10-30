import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { candleBars, USMarkets } from "./USMarkets";

const dashboard = { trading_date: "2026-09-04", market_status: "closed", instruments: 1,
  source: { source: "schwab", status: "ready", detail: "Ready" },
  sources: [{ source: "universe", status: "ready", detail: "1 configured stock resolved" },
    { source: "schwab", status: "ready", detail: "Daily and minute ready" }],
  universe: { active: 1, daily_configured: 1, minute_configured: 1,
    daily_with_data: 1, minute_with_data: 1, no_daily_data: 0 }, resolutions: {
    "1min": { actual: 390, expected: 390, missing: 0, coverage_percent: 100 },
    daily: { actual: 1, expected: 1, missing: 0, coverage_percent: 100 },
  } };
const instrument = { listing_id: "id", symbol: "AAPL", name: "Apple", exchange_code: "NASDAQ",
  series: ["1min", "daily"].map(resolution => ({ resolution,
    source: "schwab", status: "complete", expected: 1, actual: 1,
    missing: 0, available_from: "1985-01-02T05:00:00Z", last_bar: "2026-09-04T05:00:00Z", error: null })) };
afterEach(() => vi.unstubAllGlobals());
it("shows coverage, searches instruments, and loads daily history", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes("dashboard") ? dashboard : url.includes("/candles/") ? { items: [{ trade_date: "2026-09-04", open: 10, high: 12, low: 9, close: 11, volume: 100 },
      { trade_date: null, bar_time: "2026-09-04T13:30:00Z", open: 0.5, high: 0.5, low: 0.5, close: 0.1234, volume: 5 }], next_cursor: null } :
      url.includes("/days") ? { items: [] } : url.includes("search=MSFT") ?
        { items: [], total: 0, offset: 0 } : { items: [instrument], total: 1, offset: 0 } })));
  render(<USMarkets />);
  expect(screen.getByText("Loading US markets…")).toBeInTheDocument();
  await screen.findByRole("button", { name: "AAPL" });
  fireEvent.change(screen.getByPlaceholderText("Symbol or company"), { target: { value: "MSFT" } });
  expect(await screen.findByText("No US instruments match this view.")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("Symbol or company"), { target: { value: "" } });
  fireEvent.click(await screen.findByRole("button", { name: "AAPL" }));
  await screen.findByText("$11.00");
  expect(screen.getByText("$0.1234")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "2026-09-04 13:30 UTC" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Older prices" })).toBeDisabled();
});
it("renders an empty collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("dashboard") ? dashboard : { items: [], total: 0, offset: 0 } })));
  render(<USMarkets />);
  await screen.findByText("No US instruments match this view.");
});
it("renders request errors with a usable retry", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503 })));
  render(<USMarkets />);
  expect(await screen.findByRole("alert")).toHaveTextContent("503");
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh data" })).not.toBeDisabled());
});

describe("candleBars", () => {
  it("orders newest-first API candles oldest first and drops unparseable rows", () => {
    const bars = candleBars([
      { trade_date: "2026-10-02", open: "11", high: "12", low: "10", close: "11.5", volume: 200 },
      { trade_date: "2026-10-01", open: 10, high: 11, low: 9, close: 10.5, volume: 100 },
      { trade_date: "2026-09-30", open: "x", high: "1", low: "1", close: "1", volume: 1 },
    ]);
    expect(bars.map((bar) => bar.t)).toEqual(["2026-10-01", "2026-10-02"]);
    expect(bars[1]).toMatchObject({ open: 11, high: 12, low: 10, close: 11.5, volume: 200 });
  });
});
