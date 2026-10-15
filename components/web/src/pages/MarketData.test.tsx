import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MarketData } from "./MarketData";

const ticker = {
  listing_id: "00000000-0000-0000-0000-000000000001", symbol: "AAPL.US", factorlab_symbol: "AAPL", name: "Apple Inc.",
  source: "schwab", universe: "russell3000", resolution: "daily", series_count: 1,
  rows: 24, trading_days: 24, first_date: "2026-09-16", latest_date: "2026-10-14",
  last_ingested_at: "2026-10-15T00:00:00Z", history_complete: false, recovery_error: null,
};

afterEach(() => vi.unstubAllGlobals());

it("searches the configured universe and opens ticker coverage", async () => {
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes(ticker.listing_id)
      ? { ...ticker, gap_window_from: "2026-09-16", gap_window_to: "2026-10-14", missing_trading_days: 2 }
      : { market: "US", stream: "daily", configured_total: 3000, total: 1,
        limit: 50, offset: 0, configuration_as_of: "2026-10-15T00:00:00Z", items: [ticker] },
  }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search tickers or companies" }),
    { target: { value: "apple" } });
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    expect.stringContaining("search=apple"), expect.anything(),
  ));
  fireEvent.click(await screen.findByRole("button", { name: "View coverage for AAPL.US" }));
  const panel = await screen.findByRole("dialog", { name: "AAPL.US coverage" });
  expect(panel).toHaveFocus();
  expect(panel).toHaveTextContent("Provider ticker");
  expect(panel).toHaveTextContent("FactorLab ticker");
  expect(panel.querySelector(".market-data__identity")).toHaveTextContent("AAPL.US→FactorLab tickerAAPL");
  expect(await screen.findByText("Missing trading days")).toBeInTheDocument();
  expect(screen.getByText("2", { selector: "dd" })).toBeInTheDocument();
  fireEvent.keyDown(panel, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("switches market and history view without showing the previous universe", async () => {
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => {
    const params = new URL(url, "http://localhost").searchParams;
    return { market: params.get("market"), stream: params.get("stream"),
      configured_total: 0, total: 0, limit: 50, offset: 0,
      configuration_as_of: null, items: [] };
  } }));
  vi.stubGlobal("fetch", fetcher);
  render(<MarketData />);
  fireEvent.click(screen.getByRole("button", { name: "India" }));
  fireEvent.click(screen.getByRole("button", { name: "Historical recovery" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    expect.stringContaining("market=IN&stream=historical"), expect.anything(),
  ));
  expect(await screen.findByText("No configured tickers match this search and filter.")).toBeInTheDocument();
});
