import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PoliticalData } from "./PoliticalData";

const dashboard = {
  as_of_date: "2026-09-18", current_legislators: 535, current_committees: 220,
  filings: 100, parsed_filings: 90, trades: 450, unique_legislators: 40,
  unique_tickers: 120, unparsed_filings: 10, legislator_match_rate: 95.5,
  ticker_resolution_rate: 80.1, late_disclosures: 3, anomaly_count: 15,
  latest_filing_date: "2026-09-17", latest_transaction_date: "2026-09-12",
  last_ingested_at: "2026-09-18T02:00:00Z", collected_on_date: 25,
};

const coverage = { items: [{
  chamber: "house", filings: 100, parsed_filings: 90, unparsed_filings: 10,
  filing_parse_rate: 90, trades: 450, unique_legislators: 40,
  matched_legislator_trades: 430, legislator_match_rate: 95.5,
  unique_tickers: 120, ticker_resolution_rate: 80.1,
  first_filing_date: "2026-01-01", latest_filing_date: "2026-09-17",
  last_ingested_at: "2026-09-18T02:00:00Z",
}] };

const trades = { items: [{
  trade_key: "a".repeat(64), chamber: "house", filing_id: "filing-1",
  filing_date: "2026-09-17", filing_url: "https://example.test/filing-1",
  bioguide_id: "P000197", legislator_name: "Nancy Pelosi", state: "CA", district: 11,
  owner_code: "SP", filer_type: "self", asset_name_raw: "Apple Inc.", ticker: "AAPL",
  asset_type_code: "ST", transaction_type: "purchase", transaction_date: "2026-09-12",
  notification_date: null, amount_str: "$1,001 - $15,000", amount_min: 1001,
  amount_max: 15000, source: "house_clerk_ptr", as_of_time: "2026-09-18T02:00:00Z",
  ingested_at: "2026-09-18T02:00:00Z",
}], next_cursor: "next-page", limit: 50, data_as_of: "2026-09-18T02:00:00Z" };

function responseFor(url: string): object {
  if (url.includes("/dashboard")) return dashboard;
  if (url.includes("/coverage")) return coverage;
  if (url.includes("/metrics/")) return { metric: "trades", date_basis: "transaction", group_by: "month", points: [{ bucket: "2026-08-01", value: 32 }, { bucket: "2026-09-01", value: 45 }] };
  return trades;
}

describe("PoliticalData", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve({ ok: true, json: async () => responseFor(String(input)) } as Response)));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("visualizes totals, monthly activity, coverage, and disclosures", async () => {
    render(<PoliticalData />);
    expect(await screen.findByText("Political trading activity, made legible.")).toBeInTheDocument();
    expect(screen.getByText("450")).toBeInTheDocument();
    expect(screen.getByText("Disclosure coverage")).toBeInTheDocument();
    expect(screen.getByText("Nancy Pelosi")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/us?symbol=AAPL");
    expect(screen.getByText("45")).toBeInTheDocument();
  });

  it("applies ticker and chamber filters to the trade request", async () => {
    render(<PoliticalData />);
    await screen.findByText("Nancy Pelosi");
    fireEvent.change(screen.getByPlaceholderText("Filter by ticker, e.g. AAPL"), { target: { value: "msft" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.change(screen.getByLabelText("Chamber"), { target: { value: "house" } });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => {
      const value = String(url);
      return value.includes("/political/trades?") && value.includes("ticker=MSFT") && value.includes("chamber=house");
    })).toBe(true));
  });
});
