import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { BrokerAccount } from "../portfolioTypes";
import { OrderTagging, PortfolioGroups } from "./PortfolioBook";

const A = "d677331a-a04f-454a-8a1e-02425be2c101";
const B = "d677331a-a04f-454a-8a1e-02425be2c102";
const account: BrokerAccount = { broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", user_id: A, display_name: "Alice", simulated: true };
const entries = { themes: [{ id: "theme", user_id: A, name: "AI", description: "", active: true }], strategies: [{ id: "strategy", user_id: A, name: "Momentum", description: "", active: true }], "trade-names": [{ id: "trade", user_id: A, name: "Chip basket", description: "", active: true }] };
const group = { id: "theme", name: "AI", currency: "USD", account_mode: "paper", simulated: true, realized: "400", unrealized: "50", commissions: "3", total: "447", issues: ["stale_price"], details: [] };

afterEach(() => vi.unstubAllGlobals());

it("saves three independent order tags, expands fills, and clears the assignment", async () => {
  let tags = { theme_id: null, strategy_id: null, trade_name_id: null };
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    let result: unknown;
    if (init?.method === "PUT") { tags = JSON.parse(String(init.body)); writes.push(tags); result = tags; }
    else if (path.pathname.includes("/catalogs/")) { result = { items: entries[path.pathname.split("/").pop() as keyof typeof entries] }; }
    else { result = { items: [{ key: "100", perm_id: "100", trading_symbol: "ABC", currency: "USD", side: "BUY", status: "Executed", filled_quantity: "20", taggable: true, ...tags,
      fills: [{ exec_id: "one", quantity: 10, price: 100, commission: 1, commission_ccy: "USD" }, { exec_id: "two", quantity: 10, price: 110, commission: 1, commission_ccy: "USD" }] }], has_more: false }; }
    return { ok: true, json: async () => result };
  }));
  render(<OrderTagging account={account} />);
  expect(await screen.findByText("ABC · BUY · Order 100")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Themes for order 100"), { target: { value: "theme" } });
  fireEvent.change(screen.getByLabelText("Strategies for order 100"), { target: { value: "strategy" } });
  fireEvent.change(screen.getByLabelText("Trade names for order 100"), { target: { value: "trade" } });
  fireEvent.click(screen.getByRole("button", { name: "Save tags" }));
  await waitFor(() => expect(writes).toEqual([{ theme_id: "theme", strategy_id: "strategy", trade_name_id: "trade" }]));
  fireEvent.click(screen.getByText("2 fills · inherit these order tags"));
  expect(screen.getByRole("table", { name: "Fills for order 100" })).toHaveTextContent("one");
  await waitFor(() => expect(screen.getByRole("button", { name: "Clear tags" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Clear tags" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).toEqual({ theme_id: null, strategy_id: null, trade_name_id: null });
});

it("creates, edits and archives catalog names and displays PnL freshness", async () => {
  let catalog = [...entries.themes];
  const writes: Array<{ method: string; body: { name: string; description: string; active: boolean } }> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    let result: unknown;
    if (init?.method) {
      const body = JSON.parse(String(init.body)); writes.push({ method: init.method, body });
      const id = init.method === "POST" ? "new" : path.pathname.split("/").pop()!;
      catalog = [...catalog.filter(row => row.id !== id), { ...body, id, user_id: A }]; result = body;
    } else if (path.pathname.endsWith("/profiles")) { result = { items: [{ user_id: A, display_name: "Alice" }] }; }
    else if (path.pathname.endsWith("/accounts")) { result = { items: [account] }; }
    else if (path.pathname.includes("/pnl/")) { result = { items: [group] }; }
    else { result = { items: catalog }; }
    return { ok: true, json: async () => result };
  }));
  render(<PortfolioGroups kind="themes" />);
  expect(await screen.findByText("447")).toBeInTheDocument();
  expect(screen.getByText("Stale price")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Energy" } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Thesis" } });
  fireEvent.click(screen.getByRole("button", { name: "Create theme" }));
  expect(await screen.findByText("Energy", { selector: "strong" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Energy supply" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("Energy supply", { selector: "strong" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Archive" })[1]);
  expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
  expect(writes.map(row => row.method)).toEqual(["POST", "PUT", "PUT"]);
  expect(writes[2].body.active).toBe(false);
});

it("clears another profile's and another account's report even when their replacement fails", async () => {
  let failRefresh = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    const second = path.searchParams.get("user_id") === B;
    if (path.pathname.includes("/pnl/") && (second || path.searchParams.has("account_id") || failRefresh)) throw new Error("offline");
    const result = path.pathname.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }, { user_id: B, display_name: "Bob" }] }
      : path.pathname.endsWith("/accounts") ? { items: [account] }
      : path.pathname.includes("/pnl/") ? { items: [group] } : { items: [] };
    return { ok: true, json: async () => result };
  }));
  render(<PortfolioGroups kind="themes" />);
  expect(await screen.findByText("447")).toBeInTheDocument();
  failRefresh = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh PnL" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("PnL refresh failed");
  expect(screen.getByText("447")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("PnL account"), { target: { value: JSON.stringify(["ibkr", "SIM_A"]) } });
  await waitFor(() => expect(screen.queryByText("447")).not.toBeInTheDocument());
  expect(await screen.findByRole("alert")).toHaveTextContent("PnL refresh failed");
  fireEvent.change(screen.getByLabelText("Test profile"), { target: { value: B } });
  expect(screen.queryByText("447")).not.toBeInTheDocument();
});
