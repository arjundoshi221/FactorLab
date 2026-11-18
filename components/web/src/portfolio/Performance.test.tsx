import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { Performance, sinceFor } from "./Performance";
import type { PerformanceReport } from "./types";

const A = "d677331a-a04f-454a-8a1e-02425be2c101";
const account = { broker_code: "ibkr", account_id: "U1", account_mode: "live", base_currency: "SGD", user_id: A, display_name: "A", simulated: false };

const report: PerformanceReport = {
  currency: "SGD", since: "2026-01-01", start: "2025-12-31", end: "2026-11-12",
  opening_nav: 1993.7, closing_nav: 46301.33, deposits: 39500, withdrawals: 0, net_contributions: 39500,
  net_profit: 4807.63, return_on_capital: 0.1159, twr: 0.2067, ibkr_twr: 0.2067, xirr: 0.2432, financing_cost: -305.68,
  attribution_source: "ibkr",
  attribution: [
    { key: "investment", label: "Investment gains", amount: 5328.13, note: "" },
    { key: "interest", label: "Interest", amount: -124.79, note: "" },
    { key: "interest_accruals", label: "Interest accrual change", amount: -180.89, note: "" },
    { key: "commissions", label: "Commissions", amount: -191.35, note: "" },
    { key: "net_profit", label: "Net profit", amount: 4807.63, note: "" },
  ],
  nav: [
    { date: "2026-01-02", nav: 2000, contributions: 1993.7, twr: 0.003, ret: 0.003 },
    { date: "2026-11-12", nav: 46301.33, contributions: 41493.7, twr: 0.2067, ret: 0.01 },
  ],
  monthly: [
    { month: "2026-01", start_nav: 1993.7, end_nav: 2000, flows: 0, profit: 6.3, twr: 0.003 },
    { month: "2026-11", start_nav: 44000, end_nav: 46301.33, flows: 0, profit: 2301.33, twr: 0.052 },
  ],
  warnings: ["Net liquidation history starts on 2025-12-31; the period starts there."],
  basis: ["Net profit = closing NAV - opening NAV - net deposits."],
  risk: {
    observations: 228, daily_vol: 0.0209, annual_vol: 0.3311, var95: 0.0295, es95: 0.0564, var95_amount: 1225.23, es95_amount: 2344.05,
    max_drawdown: -0.2104, drawdown_peak: "2026-06-22", drawdown_trough: "2026-10-22", current_drawdown: -0.1958,
    beta: 1.46, correlation: 0.56, benchmark: "S&P 500", enough_history: false,
  },
  benchmarks: [{ code: "$SPX", label: "S&P 500", note: "Price index", twr: 0.0857, excess: 0.121, points: [{ date: "2026-01-02", value: 0.01 }, { date: "2026-11-12", value: 0.0857 }] }],
};

const calls: string[] = [];
function respond(url: string) {
  calls.push(url);
  const path = new URL(url, "http://localhost");
  if (path.pathname.endsWith("/profiles")) return { items: [{ user_id: A, display_name: "A", active: true }] };
  if (path.pathname.endsWith("/accounts")) return { items: [account] };
  if (path.pathname.endsWith("/performance")) return report;
  return { items: [] };
}

afterEach(() => { vi.unstubAllGlobals(); calls.length = 0; window.history.replaceState(null, "", "/portfolio/performance"); });

it("computes period start dates in New York terms", () => {
  const noon = new Date("2026-11-19T17:00:00Z");
  expect(sinceFor("ytd", noon)).toBe("2026-01-01");
  expect(sinceFor("1y", noon)).toBe("2025-11-19");
  expect(sinceFor("all", noon)).toBe("2000-01-01");
});

it("shows returns, reconciliation with IBKR, attribution, risk and benchmarks", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => respond(url) })));
  render(<Performance />);
  const summary = await screen.findByRole("region", { name: "Summary" });
  expect(within(summary).getByText("+4,807.63")).toBeInTheDocument();
  expect(within(summary).getByText("+20.67%")).toBeInTheDocument();
  expect(within(summary).getByText("Matches IBKR")).toBeInTheDocument();
  expect(within(summary).getByText("−305.68")).toBeInTheDocument();
  const attribution = screen.getByRole("region", { name: "Where the profit came from" });
  expect(within(attribution).getByText("IBKR change in NAV · SGD")).toBeInTheDocument();
  expect(within(attribution).getByText("Interest accrual change")).toBeInTheDocument();
  const risk = screen.getByRole("region", { name: "Statistical risk" });
  expect(within(risk).getByText("33.1%")).toBeInTheDocument();
  expect(within(risk).getByText("−21.0%")).toBeInTheDocument();
  expect(within(screen.getByRole("table", { name: "Benchmarks" })).getByText("+12.10%")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Monthly performance" })).toBeInTheDocument();
  expect(screen.getByText(/history starts on 2025-12-31/)).toBeInTheDocument();
  expect(calls.some(url => url.includes("/accounts/ibkr/U1/performance") && url.includes("since="))).toBe(true);
});

it("switches period through the URL", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => respond(url) })));
  render(<Performance />);
  await screen.findByRole("region", { name: "Summary" });
  fireEvent.click(screen.getByRole("button", { name: "Since start" }));
  expect(window.location.search).toContain("period=all");
  await vi.waitFor(() => expect(calls.some(url => url.includes("since=2000-01-01"))).toBe(true));
});
