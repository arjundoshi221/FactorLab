import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { USDashboardPanel } from "./USDashboardPanel";

const dashboard = {
  trading_date: "2026-09-15", market_status: "open", instruments: 6100,
  source: { source: "eodhd", status: "ready", detail: "Daily collection ready" },
  sources: [
    { source: "universe", status: "ready", detail: "500 configured stocks resolved" },
    { source: "eodhd", status: "ready", detail: "Daily collection ready" },
    { source: "schwab", status: "auth_required", detail: "Authenticate Schwab" },
  ],
  universe: { active: 6100, daily_configured: 6100, minute_configured: 250,
    daily_with_data: 6000, minute_with_data: 240, no_daily_data: 100 },
  resolutions: {
    "1min": { actual: 85000, expected: 90000, missing: 5000, coverage_percent: 94.44 },
    daily: { actual: 6000, expected: 6100, missing: 100, coverage_percent: 98.36 },
  },
};
const instrument = { instrument_id: "id", symbol: "AAPL", name: "Apple Inc.", exchange_code: "XNAS",
  series: [{ resolution: "1min", source: "schwab", status: "complete", expected: 390, actual: 390,
    missing: 0, available_from: null, last_bar: null, error: null },
  { resolution: "daily", source: "eodhd", status: "complete", expected: 1, actual: 1,
    missing: 0, available_from: null, last_bar: null, error: null }] };
const run = { run_id: "run", pipeline: "us_bulk_daily", source: "eodhd", status: "success",
  started_at: "2026-09-15T20:00:00Z", completed_at: "2026-09-15T20:01:00Z",
  successful_series: 6000, failed_series: 100, rows_written: 6000, error: null };

afterEach(() => vi.unstubAllGlobals());

it("integrates universe, coverage, sources, instruments, and runs", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes("/dashboard") ? dashboard : url.includes("/instruments?")
      ? { items: [instrument], total: 6100, offset: 0, next_cursor: null }
      : { items: [run], total: null, offset: 0, next_cursor: null } })));
  render(<USDashboardPanel refreshKey="one" />);
  expect(await screen.findByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("6,100 stocks")).toBeInTheDocument();
  expect(screen.getByText("us bulk daily")).toBeInTheDocument();
  expect(screen.getByText("500 configured stocks resolved")).toBeInTheDocument();
  expect(screen.getByText("Authenticate Schwab")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Explore all US data" })).toHaveAttribute("href", "/us");
});

it("shows the provider-safe empty state", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes("/dashboard") ? { ...dashboard, universe: { ...dashboard.universe, active: 0 } }
      : { items: [], total: 0, offset: 0, next_cursor: null } })));
  render(<USDashboardPanel refreshKey="one" />);
  expect(await screen.findByText(/provider master has not populated/i)).toBeInTheDocument();
  expect(screen.getByText(/No US ingestion runs/i)).toBeInTheDocument();
});
