import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountTrades, TradesPage, holding } from "./TradeHistory";

const USER = "d677331a-a04f-454a-8a1e-02425be2c101";
const trade = (symbol: string, status: "open" | "closed", extra = {}) => ({
  key: `ibkr:U1:${symbol}:${status}`, broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false,
  trading_symbol: symbol, listing_id: null, security_id: null, contract_id: null, product_type: "STK", currency: "USD",
  direction: "long", status, opened_at: "2026-10-01T14:00:00Z", closed_at: status === "closed" ? "2026-10-11T14:00:00Z" : null,
  last_fill_at: "2026-10-11T14:00:00Z", quantity: status === "open" ? "4" : "0", max_quantity: "20", entry_quantity: "20",
  exit_quantity: status === "open" ? "16" : "20", avg_entry_price: "105", avg_exit_price: "112.5", open_cost: status === "open" ? "420" : "0",
  market_price: status === "open" ? "110" : null, price_time: status === "open" ? "2026-10-20T14:30:00Z" : null,
  realized: "150", unrealized: status === "open" ? "20" : "0", commissions: [{ currency: "USD", amount: "4" }], net: "146",
  return_pct: "0.071429", theme_id: "t1", strategy_id: "s1", trade_name_id: "n1", theme_name: "AI", strategy_name: "Momentum",
  trade_name_name: "NVDA swing", tags_mixed: false, orders: ["1", "2"], fill_count: 2,
  fills: [
    { exec_id: "a", exec_time: "2026-10-01T14:00:00Z", perm_id: "1", side: "BUY", role: "open", quantity: "20", price: "105", commission: "2", commission_ccy: "USD" },
    { exec_id: "b", exec_time: "2026-10-11T14:00:00Z", perm_id: "2", side: "SELL", role: "close", quantity: "20", price: "112.5", commission: "2", commission_ccy: "USD" },
  ],
  fills_truncated: false, issues: [], ...extra,
});
const page = (items: ReturnType<typeof trade>[]) => ({
  items, total: items.length, limit: 50, offset: 0, counts: { open: 1, closed: 1 }, generated_at: "2026-10-20T00:00:00Z",
  summary: [{ currency: "USD", account_mode: "live", simulated: false, closed: 1, valued: 1, wins: 1, losses: 0, realized: "150", net: "146" }],
  accounts: [{ broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false }],
});

function serve() {
  const fetcher = vi.fn(async (url: string) => {
    const status = new URL(url, "http://x").searchParams.get("status");
    const body = url.includes("/profiles") ? { items: [{ user_id: USER, display_name: "Jai", active: true }] }
      : url.includes("/catalogs/") ? { items: [] }
      : url.includes("/trades") ? page(status === "open" ? [trade("AMD", "open")] : status === "closed" ? [trade("NVDA", "closed")] : [trade("AMD", "open"), trade("NVDA", "closed")])
      : { items: [], total: 0, limit: 50, offset: 0, counts: { tagged: 0, untagged: 0, not_taggable: 0 }, accounts: [], generated_at: "" };
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

beforeEach(() => window.history.replaceState(null, "", "/portfolio/trades"));
afterEach(() => vi.unstubAllGlobals());

it("switches the Trades page to open and closed trades and filters by status", async () => {
  const fetcher = serve();
  render(<TradesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Open & closed" }));
  expect(window.location.search).toContain("view=positions");
  const table = await screen.findByRole("table", { name: "Open and closed trades" });
  expect(within(table).getByText("AMD")).toBeInTheDocument();
  expect(within(table).getByText("Open · " + holding("2026-10-01T14:00:00Z", null))).toBeInTheDocument();
  expect(within(table).getAllByText("+7.1%")).toHaveLength(2);
  expect(screen.getByLabelText("Closed trade results")).toHaveTextContent("100% won (1W / 0L)");
  fireEvent.click(screen.getByRole("button", { name: "Closed 1" }));
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringMatching(/\/trades\?.*status=closed/), expect.anything()));
  expect(window.location.search).toContain("status=closed");
});

it("expands a trade into its fills with open/close actions", async () => {
  serve();
  window.history.replaceState(null, "", "/portfolio/trades?view=positions");
  render(<TradesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Show NVDA closed trade details" }));
  const fills = screen.getByRole("table", { name: /Fills for NVDA trade/ });
  expect(within(fills).getByText("Open")).toBeInTheDocument();
  expect(within(fills).getByText("Close")).toBeInTheDocument();
  expect(screen.getByText("Held").nextElementSibling).toHaveTextContent("10d");
});

it("shows an account's open trades on the overview and links to the full view", async () => {
  const fetcher = serve();
  render(<AccountTrades account={{ broker_code: "ibkr", account_id: "U1", account_mode: "live", user_id: USER, display_name: "Jai", simulated: false }} />);
  const table = await screen.findByRole("table", { name: "Open trades" });
  expect(within(table).getByText("AMD")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith(expect.stringMatching(/broker_code=ibkr&account_id=U1&status=open/), expect.anything());
  expect(screen.getByRole("link", { name: /Open the trades view/ })).toHaveAttribute("href", expect.stringContaining("view=positions"));
  fireEvent.click(screen.getByRole("button", { name: "Closed 1" }));
  expect(await screen.findByRole("table", { name: "Closed trades" })).toBeInTheDocument();
});

it("formats holding periods", () => {
  expect(holding("2026-10-01T00:00:00Z", "2026-10-01T00:30:00Z")).toBe("<1h");
  expect(holding("2026-10-01T00:00:00Z", "2026-10-02T05:00:00Z")).toBe("29h");
  expect(holding("2026-10-01T00:00:00Z", "2026-10-11T00:00:00Z")).toBe("10d");
});
