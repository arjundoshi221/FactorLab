import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { PortfolioApp } from "./PortfolioApp";

const account = { broker_code: "ibkr", account_id: "U1", account_mode: "live", base_currency: "SGD", user_id: "u1", display_name: "Me", simulated: false };
const snapshot = (items: unknown[], freshness = "fresh") => ({ items, snapshot_time: "2026-11-12T05:00:00Z", age_seconds: 30, source_channel: "gateway", simulated: false, freshness });
const row = (metric: string, value: number, currency = "SGD") => ({ metric, segment: "", currency, value_num: String(value) });
const balances = [
  row("NetLiquidation", 100_000), row("GrossPositionValue", 200_000), row("InitMarginReq", 50_000), row("MaintMarginReq", 40_000),
  row("ExcessLiquidity", 60_000), row("TotalCashValue", -100_000), row("CashBalance", -100_000, "SGD"),
];

function api(url: string, freshness = "fresh") {
  const path = new URL(url, "http://hub.test").pathname;
  if (path.endsWith("/profiles")) return { items: [{ user_id: "u1", display_name: "Me", active: true }] };
  if (path.endsWith("/accounts")) return { items: [account] };
  if (path.endsWith("/summary")) return { account, balances: snapshot(balances, freshness) };
  if (path.endsWith("/positions")) return snapshot([
    { trading_symbol: "BOXX", currency: "SGD", position: 1, market_value: "80000" },
    { trading_symbol: "LLY", currency: "SGD", position: 1, market_value: "70000" },
    { trading_symbol: "CSPX", currency: "SGD", position: 1, market_value: "50000" },
  ], freshness);
  return { items: [] };
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/portfolio/risk?user_id=u1");
});
afterEach(() => { vi.unstubAllGlobals(); });

it("renders the risk board in the portfolio shell with limits, concentration and capacity", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => api(url) })));
  render(<PortfolioApp path="/portfolio/risk" />);
  expect(screen.getByRole("heading", { level: 1, name: "Risk" })).toBeInTheDocument();
  const limits = await screen.findByRole("table", { name: "Risk limits" });
  // 2.0x gross is within the 2.5x target; LLY at 70% of NAV breaches the 40% single-name hard limit.
  const gross = within(limits).getByRole("row", { name: /Gross leverage/ });
  expect(within(gross).getByText("2.00×")).toBeInTheDocument();
  expect(within(gross).getByText("Within target")).toBeInTheDocument();
  const single = within(limits).getByRole("row", { name: /Largest single name/ });
  expect(within(single).getByText("70.0%")).toBeInTheDocument();
  expect(within(single).getByText("Hard limit breached")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Risk status" })).getByText("Red")).toBeInTheDocument();
  expect(within(screen.getByRole("table", { name: "Positions and concentration" })).getByText("Cash equivalent")).toBeInTheDocument();
  expect(screen.getByText("Additional investment permitted")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Risk" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/portfolio?user_id=u1");
});

it("shows net and beta-adjusted leverage with betas measured against SPY", async () => {
  // LLY moves half as much as SPY every day: a measured beta of 0.5. CSPX has no listing, so it is assumed 1.0.
  const bars = (scale: number) => Array.from({ length: 90 }, (_, index) => ({ t: new Date(Date.UTC(2026, 0, 1 + index)).toISOString(), open: 1, high: 1, low: 1, volume: null,
    close: 100 * Array.from({ length: index }, (_, step) => 1 + scale * (((step * 7) % 11) - 5) / 500).reduce((product, factor) => product * factor, 1) }));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://hub.test").pathname;
    if (path.endsWith("/market-data/instruments")) return { ok: true, json: async () => ({ items: [{ id: "spy", listing_id: "spy", symbol: "SPY", active: true }], total: 1, limit: 20, offset: 0 }) };
    if (path.endsWith("/listings/spy/prices")) return { ok: true, json: async () => ({ bars: bars(1) }) };
    if (path.endsWith("/listings/lly/prices")) return { ok: true, json: async () => ({ bars: bars(0.5) }) };
    if (path.endsWith("/positions")) return { ok: true, json: async () => snapshot([
      { trading_symbol: "BOXX", currency: "SGD", position: 1, market_value: "80000" },
      { trading_symbol: "LLY", listing_id: "lly", currency: "SGD", position: 1, market_value: "70000" },
      { trading_symbol: "CSPX", currency: "SGD", position: 1, market_value: "50000" },
    ]) };
    return { ok: true, json: async () => api(url) };
  }));
  render(<PortfolioApp path="/portfolio/risk" />);
  const leverage = await screen.findByRole("region", { name: "Leverage" });
  // Net: (70k + 50k) / 100k. Beta-adjusted: (70k × 0.5 + 50k × 1.0) / 100k.
  expect(within(leverage).getByText("Net leverage").nextSibling).toHaveTextContent("1.20×");
  await waitFor(() => expect(within(leverage).getByText("Beta-adjusted leverage").nextSibling).toHaveTextContent("0.85×"));
  expect(within(leverage).getByText(/measured for LLY 0.50/)).toBeInTheDocument();
  expect(within(leverage).getByText(/No usable price history for CSPX; assumed 1.0/)).toBeInTheDocument();
  const concentration = within(screen.getByRole("table", { name: "Positions and concentration" }));
  expect(within(concentration.getByRole("row", { name: /CSPX/ })).getByText("assumed")).toBeInTheDocument();
});

it("saves edited limits per account and rejects a target looser than the hard limit", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => api(url) })));
  render(<PortfolioApp path="/portfolio/risk" />);
  await screen.findByRole("table", { name: "Risk limits" });
  fireEvent.click(screen.getByRole("button", { name: "Edit limits" }));
  fireEvent.change(screen.getByLabelText("Largest single name operating target"), { target: { value: "90" } });
  expect(screen.getByRole("alert")).toHaveTextContent("Largest single name");
  expect(screen.getByRole("button", { name: "Save limits" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Largest single name hard limit"), { target: { value: "95" } });
  fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
  const single = within(screen.getByRole("table", { name: "Risk limits" })).getByRole("row", { name: /Largest single name/ });
  expect(within(single).getByText("Within target")).toBeInTheDocument();
  expect(JSON.parse(window.localStorage.getItem("factorlab.portfolio.risk.v1:ibkr:U1") ?? "{}").limits.singleName).toEqual({ target: 0.9, hard: 0.95 });
});

it("never shows green on a stale snapshot", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => api(url, "stale") })));
  render(<PortfolioApp path="/portfolio/risk" />);
  const status = await screen.findByRole("region", { name: "Risk status" });
  expect(within(status).getByText(/The broker snapshot is stale/)).toBeInTheDocument();
  expect(within(status).queryByText("Green")).not.toBeInTheDocument();
});

it("toggles and remembers the portfolio theme", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => api(url) })));
  const { container } = render(<PortfolioApp path="/portfolio/risk" />);
  const app = container.querySelector(".pf-app")!;
  expect(app).toHaveAttribute("data-pf-theme", "dark");
  expect(document.documentElement.dataset.pfTheme).toBe("dark");
  fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
  expect(app).toHaveAttribute("data-pf-theme", "light");
  expect(document.documentElement.dataset.pfTheme).toBe("light");
  expect(window.localStorage.getItem("factorlab.portfolio.theme")).toBe("light");
  expect(screen.getByRole("link", { name: /Terminal/ })).toHaveAttribute("href", "/");
  await screen.findByRole("table", { name: "Risk limits" });
});
