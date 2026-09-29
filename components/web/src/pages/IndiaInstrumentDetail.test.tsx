import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

const listingId = "11111111-1111-1111-1111-111111111111";
const profile = {
  listing_id: listingId, symbol: "TCS", name: "Tata Consultancy Services",
  exchange_code: "NSE", instrument_type: "common", status: "active",
  source: "upstox", collection_status: "collecting", data_points: 7500, trading_days: 20,
  unique_series: 1, first_bar_time: "2026-08-01T03:45:00Z", last_bar_time: "2026-08-27T07:00:00Z",
  last_ingested_at: "2026-08-27T07:01:00Z", expected_series: 1, selected_data_points: 2,
  selected_unique_series: 1, expected_data_points: 2, coverage_percent: 100,
  ohlc_violations: 0, null_ohlc_values: 0, outside_session: 0, duplicate_versions: 0,
  quality_issue_count: 0, check_status: "healthy", check_reason: "Coverage and value checks passed.",
};
const days = { listing_id: listingId, date_from: "2026-07-23", date_to: "2026-08-27", items: [
  { trading_date: "2026-08-27", market_status: "closed", data_points: 2, unique_series: 1, expected_series: 1, expected_data_points: 2, coverage_percent: 100, first_bar_time: "2026-08-27T03:45:00Z", last_bar_time: "2026-08-27T03:46:00Z", last_ingested_at: "2026-08-27T03:47:00Z", ohlc_violations: 0, null_ohlc_values: 0, outside_session: 0, duplicate_versions: 0, quality_issue_count: 0, check_status: "healthy", check_reason: "Coverage and value checks passed." },
  { trading_date: "2026-08-26", market_status: "closed", data_points: 0, unique_series: 0, expected_series: 1, expected_data_points: 375, coverage_percent: 0, first_bar_time: null, last_bar_time: null, last_ingested_at: null, ohlc_violations: 0, null_ohlc_values: 0, outside_session: 0, duplicate_versions: 0, quality_issue_count: 0, check_status: "missing", check_reason: "No expected candles were found." },
] };
const candle = (minute: string, open: string, close: string) => ({
  listing_id: listingId, contract_id: null,
  symbol: "TCS", country_code: "IN", bar_time: `2026-08-27T${minute}:00Z`, open,
  high: String(Math.max(Number(open), Number(close)) + 1), low: String(Math.min(Number(open), Number(close)) - 1),
  close, volume: 1000, oi: 500, source: "upstox", as_of_time: "2026-08-27T04:00:00Z",
  ingested_at: "2026-08-27T04:01:00Z",
});
const candles = { items: [candle("03:46", "3502", "3505"), candle("03:45", "3500", "3502")], next_cursor: "older", limit: 240, data_as_of: "2026-08-27T04:00:00Z" };

describe("India instrument detail", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", `/india/instruments/${listingId}?date=2026-08-27`);
    vi.stubGlobal("scrollTo", vi.fn());
    vi.stubGlobal("fetch", vi.fn((request: RequestInfo | URL) => {
      const url = String(request);
      const body = url.includes("/candles?") ? candles : url.includes("/days?") ? days : profile;
      return Promise.resolve({ ok: true, json: async () => body } as Response);
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders actual candles, prices, and session quality on a dedicated route", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "TCS" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "India markets" })).toHaveClass("active");
    expect(screen.getByRole("img", { name: /TCS one-minute candlestick chart/ })).toBeInTheDocument();
    expect(screen.getByText("One-minute candles")).toBeInTheDocument();
    expect(screen.getAllByText("₹3,505.00").length).toBeGreaterThan(0);
    expect(screen.getByText("2026-08-26")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Older candles" })).toBeEnabled();
  });

  it("queries the selected instrument and session when a history date is chosen", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "2026-08-26" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([request]) => {
      const url = String(request);
      return url.includes(`/${listingId}/candles?`) && url.includes("trading_date=2026-08-26");
    })).toBe(true));
    expect(window.location.search).toBe("?date=2026-08-26");
  });
});
