import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

const dashboard = {
  trading_date: "2026-08-27",
  market_status: "open",
  reference_instruments: 5103,
  expected_series: 10,
  series_with_data: 10,
  data_points: 2000,
  expected_data_points: 2000,
  coverage_percent: 100,
  collected_on_date: 2000,
  backfilled_on_date: 0,
  last_bar_time: "2026-08-27T07:00:00Z",
  last_ingested_at: "2026-08-27T07:01:00Z",
  freshness_seconds: 60,
  anomaly_count: 0,
};

const tcs = {
  listing_id: "11111111-1111-1111-1111-111111111111",
  symbol: "TCS",
  name: "Tata Consultancy Services",
  exchange_code: "NSE",
  instrument_type: "EQ",
  status: "active",
  source: "upstox",
  collection_status: "collecting",
  data_points: 7500,
  trading_days: 20,
  unique_series: 1,
  first_bar_time: "2026-08-01T03:45:00Z",
  last_bar_time: "2026-08-27T07:00:00Z",
  last_ingested_at: "2026-08-27T07:01:00Z",
  expected_series: 1,
  selected_data_points: 200,
  selected_unique_series: 1,
  expected_data_points: 200,
  coverage_percent: 100,
  ohlc_violations: 0,
  null_ohlc_values: 0,
  outside_session: 0,
  duplicate_versions: 100,
  quality_issue_count: 0,
  check_status: "healthy",
  check_reason: "Coverage and value checks passed.",
};

const instruments = {
  trading_date: "2026-08-27",
  market_status: "open",
  expected_points_per_series: 200,
  scope: "all",
  items: [tcs],
  total: 1,
  reference_total: 5103,
  collecting_total: 5,
  historical_total: 0,
  not_configured_total: 5098,
  limit: 100,
  offset: 0,
  data_as_of: "2026-08-27T07:01:00Z",
};

const days = {
  listing_id: tcs.listing_id,
  date_from: "2026-07-23",
  date_to: "2026-08-27",
  items: [
    {
      trading_date: "2026-08-27",
      market_status: "open",
      data_points: 200,
      unique_series: 1,
      expected_series: 1,
      expected_data_points: 200,
      coverage_percent: 100,
      first_bar_time: "2026-08-27T03:45:00Z",
      last_bar_time: "2026-08-27T07:00:00Z",
      last_ingested_at: "2026-08-27T07:01:00Z",
      ohlc_violations: 0,
      null_ohlc_values: 0,
      outside_session: 0,
      duplicate_versions: 100,
      quality_issue_count: 0,
      check_status: "healthy",
      check_reason: "Coverage and value checks passed.",
    },
    {
      trading_date: "2026-08-26",
      market_status: "closed",
      data_points: 0,
      unique_series: 0,
      expected_series: 1,
      expected_data_points: 375,
      coverage_percent: 0,
      first_bar_time: null,
      last_bar_time: null,
      last_ingested_at: null,
      ohlc_violations: 0,
      null_ohlc_values: 0,
      outside_session: 0,
      duplicate_versions: 0,
      quality_issue_count: 0,
      check_status: "missing",
      check_reason: "No expected candles were found.",
    },
  ],
};

describe("India Markets", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/india");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((request: string) => {
        const body = request.includes("/dashboard")
          ? dashboard
          : request.includes("/days")
            ? days
            : instruments;
        return Promise.resolve({ ok: true, json: async () => body } as Response);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders instrument coverage and links each instrument to its own page", async () => {
    render(<App />);

    expect(await screen.findByText("Inspect every instrument. Verify every session.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "India markets" })).toHaveClass("active");
    const selectedDate = (screen.getByLabelText("Trading date") as HTMLInputElement).value;
    expect(screen.getByRole("link", { name: /TCS/ })).toHaveAttribute(
      "href",
      `/india/instruments/${tcs.listing_id}?date=${selectedDate}`,
    );
    expect(screen.queryByText("Session health")).not.toBeInTheDocument();
  });

  it("sends instrument searches to the India hub API", async () => {
    render(<App />);
    await screen.findByRole("link", { name: /TCS/ });
    fireEvent.change(screen.getByPlaceholderText(/Search symbol/), { target: { value: "Tata" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("search=Tata"))).toBe(true),
    );
  });

  it("requests full-universe collection views from the API", async () => {
    render(<App />);
    await screen.findByRole("link", { name: /TCS/ });

    fireEvent.click(screen.getByRole("button", { name: /Not configured/ }));

    await waitFor(() =>
      expect(
        vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("scope=not_configured")),
      ).toBe(true),
    );
  });
});
