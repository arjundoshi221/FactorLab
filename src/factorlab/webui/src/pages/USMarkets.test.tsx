import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { USMarkets } from "./USMarkets";

const dashboard = { trading_date: "2026-09-04", market_status: "closed", instruments: 1,
  source: { source: "eodhd", status: "ready", detail: "Ready" },
  sources: [{ source: "eodhd", status: "ready", detail: "Daily ready" },
    { source: "schwab", status: "ready", detail: "Minute ready" }],
  universe: { active: 1, daily_configured: 1, minute_configured: 1,
    daily_with_data: 1, minute_with_data: 1, no_daily_data: 0 }, resolutions: {
    "1min": { actual: 390, expected: 390, missing: 0, coverage_percent: 100 },
    daily: { actual: 1, expected: 1, missing: 0, coverage_percent: 100 },
  } };
const instrument = { instrument_id: "id", symbol: "AAPL", name: "Apple", exchange_code: "NASDAQ",
  series: ["1min", "daily"].map(resolution => ({ resolution,
    source: resolution === "daily" ? "eodhd" : "schwab", status: "complete", expected: 1, actual: 1,
    missing: 0, available_from: "1985-01-02T05:00:00Z", last_bar: "2026-09-04T05:00:00Z", error: null })) };
afterEach(() => vi.unstubAllGlobals());
it("shows coverage, searches instruments, and loads daily history", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes("dashboard") ? dashboard : url.includes("/candles/") ? { items: [{ trade_date: "2026-09-04", open: 10, high: 12, low: 9, close: 11, volume: 100 }], next_cursor: null } :
      url.includes("/days") ? { items: [] } : url.includes("search=MSFT") ?
        { items: [], total: 0, offset: 0 } : { items: [instrument], total: 1, offset: 0 } })));
  render(<USMarkets />);
  expect(screen.getByText("Loading US markets…")).toBeInTheDocument();
  await screen.findByRole("button", { name: "AAPL" });
  fireEvent.change(screen.getByPlaceholderText("Symbol or company"), { target: { value: "MSFT" } });
  expect(await screen.findByText("No US instruments match this view.")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("Symbol or company"), { target: { value: "" } });
  fireEvent.click(await screen.findByRole("button", { name: "AAPL" }));
  await screen.findByText("11.0000");
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
