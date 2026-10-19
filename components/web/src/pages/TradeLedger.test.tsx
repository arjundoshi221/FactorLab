import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TradeLedger, runPool } from "./TradeLedger";

const USER = "d677331a-a04f-454a-8a1e-02425be2c101";
const LISTING = "44444444-4444-4444-4444-444444444444";
const row = (perm: string, symbol: string, status: "tagged" | "untagged" | "not_taggable", extra = {}) => ({
  key: `ibkr:U1:${perm}`, broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false, perm_id: perm,
  taggable: status !== "not_taggable", trading_symbol: symbol, listing_id: LISTING, security_id: null, contract_id: null,
  product_type: "STK", currency: "USD", side: "BUY", quantity: "3", avg_price: "100.2", first_fill_at: "2026-10-15T14:00:00Z",
  last_fill_at: "2026-10-15T14:01:00Z", fill_count: 2, commissions: [{ currency: "USD", amount: "2" }], broker_realized_pnl: null,
  theme_id: status === "tagged" ? "t1" : null, strategy_id: status === "tagged" ? "s1" : null, trade_name_id: status === "tagged" ? "n1" : null,
  theme_name: status === "tagged" ? "AI" : null, strategy_name: status === "tagged" ? "Momentum" : null, trade_name_name: status === "tagged" ? "NVDA swing" : null,
  tag_status: status, fills: [{ exec_id: `${perm}-a`, exec_time: "2026-10-15T14:00:00Z", quantity: "1", price: "100" }], fills_truncated: false, ...extra,
});
const page = {
  items: [row("1", "NVDA", "tagged"), row("2", "AAPL", "untagged"), row("3", "MSFT", "untagged"), row("0", "TSLA", "not_taggable")],
  total: 4, limit: 50, offset: 0, counts: { tagged: 1, untagged: 2, not_taggable: 1 }, generated_at: "2026-10-17T00:00:00Z",
  accounts: [{ broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false }],
};
const catalog = (id: string, name: string) => ({ items: [{ id, user_id: USER, name, description: "", active: true }] });

function serve(put: (url: string) => boolean = () => true) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      const ok = put(url);
      return { ok, status: ok ? 200 : 422, json: async () => ok ? { perm_id: "x" } : { detail: "Archived entry" } };
    }
    const body = url.includes("/profiles") ? { items: [{ user_id: USER, display_name: "Jai", active: true }] }
      : url.includes("/catalogs/themes") ? catalog("t1", "AI") : url.includes("/catalogs/strategies") ? catalog("s1", "Momentum")
      : url.includes("/catalogs/trade-names") ? catalog("n1", "NVDA swing") : page;
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

beforeEach(() => window.history.replaceState(null, "", "/portfolio/trades"));
afterEach(() => vi.unstubAllGlobals());

it("lists every order with its tags and filters on the server", async () => {
  const fetcher = serve();
  render(<TradeLedger />);
  const table = await screen.findByRole("table", { name: "Trades" });
  expect(within(table).getByText("AI")).toBeInTheDocument();
  expect(within(table).getByText("NVDA swing")).toBeInTheDocument();
  expect(within(table).getAllByText("Needs tags")).toHaveLength(2);
  expect(within(table).getByText("Not taggable")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "NVDA" })).toHaveAttribute("href", `/market-data?view=matrix&kind=listing&id=${LISTING}`);
  expect(screen.getByRole("button", { name: "Needs tags 2" })).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(screen.getByRole("button", { name: "Needs tags 2" }));
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringMatching(/\/ledger\?.*tag=untagged/), expect.anything()));
  expect(window.location.search).toContain("tag=untagged");
  fireEvent.change(screen.getByLabelText("theme"), { target: { value: "t1" } });
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringMatching(/theme_id=t1/), expect.anything()));
  fireEvent.change(screen.getByLabelText("Account"), { target: { value: JSON.stringify(["ibkr", "U1"]) } });
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.stringMatching(/broker_code=ibkr&account_id=U1/), expect.anything()));
});

it("bulk-tags selected orders two at a time and reports partial failures", async () => {
  const puts: string[] = [];
  serve(url => { puts.push(url); return !url.includes("/orders/3/"); });
  render(<TradeLedger />);
  await screen.findByRole("table", { name: "Trades" });
  expect(screen.getByRole("checkbox", { name: "Select TSLA order 0" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select AAPL order 2" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select MSFT order 3" }));
  const bar = screen.getByRole("region", { name: "Tag selected orders" });
  expect(within(bar).getByText("2 selected")).toBeInTheDocument();
  fireEvent.change(within(bar).getByLabelText("Themes for selected orders"), { target: { value: "t1" } });
  fireEvent.change(within(bar).getByLabelText("Strategies for selected orders"), { target: { value: "s1" } });
  fireEvent.change(within(bar).getByLabelText("Trade names for selected orders"), { target: { value: "n1" } });
  fireEvent.click(within(bar).getByRole("button", { name: "Apply to 2 orders" }));
  expect(await screen.findByText("1 of 2 orders tagged; 1 failed.")).toBeInTheDocument();
  expect(screen.getByRole("alert", { name: "Orders that failed" })).toHaveTextContent("MSFT 3: Archived entry");
  expect(puts).toHaveLength(2);
  expect(puts[0]).toContain(`/accounts/ibkr/U1/orders/2/assignment?user_id=${USER}`);
  // Only the failed order stays selected for a retry.
  expect(screen.getByRole("checkbox", { name: "Select MSFT order 3" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select AAPL order 2" })).not.toBeChecked();
});

it("edits one order's tags inline", async () => {
  const puts: string[] = [];
  serve(url => { puts.push(url); return true; });
  render(<TradeLedger />);
  await screen.findByRole("table", { name: "Trades" });
  fireEvent.click(screen.getAllByRole("button", { name: "Tag" })[0]);
  fireEvent.change(screen.getByLabelText("Themes for order 2"), { target: { value: "t1" } });
  fireEvent.change(screen.getByLabelText("Strategies for order 2"), { target: { value: "s1" } });
  fireEvent.change(screen.getByLabelText("Trade names for order 2"), { target: { value: "n1" } });
  fireEvent.click(screen.getByRole("button", { name: "Save tags" }));
  expect(await screen.findByText("Tags saved for AAPL order 2.")).toBeInTheDocument();
  expect(puts).toEqual([expect.stringContaining("/orders/2/assignment")]);
});

it("never runs more tasks at once than the pool allows", async () => {
  let active = 0; let peak = 0;
  await runPool([1, 2, 3, 4, 5], 2, async () => { active++; peak = Math.max(peak, active); await new Promise(r => setTimeout(r, 1)); active--; });
  expect(peak).toBe(2);
});
