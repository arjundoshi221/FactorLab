import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { Portfolio } from "./Portfolio";

const A = "d677331a-a04f-454a-8a1e-02425be2c101";
const B = "d677331a-a04f-454a-8a1e-02425be2c102";
const stamp = "2026-10-15T12:00:00Z";
const accounts = [
  { broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", base_currency: "USD", user_id: A, display_name: "Alice", simulated: true },
  { broker_code: "ibkr", account_id: "SIM_B", account_mode: "paper", base_currency: "USD", user_id: B, display_name: "Bob", simulated: true },
];
const snapshot = (items: unknown[]) => ({ items, snapshot_time: stamp, age_seconds: 60, source_channel: "simulation", simulated: true, freshness: "fresh" });

function response(url: string) {
  const path = new URL(url, "http://localhost");
  if (path.pathname.endsWith("/profiles")) return { items: [{ user_id: A, display_name: "Alice", active: true }, { user_id: B, display_name: "Bob", active: true }] };
  if (path.pathname.endsWith("/accounts")) return { items: accounts.filter(account => !path.searchParams.get("user_id") || account.user_id === path.searchParams.get("user_id")) };
  const account = path.pathname.includes("SIM_B") ? accounts[1] : accounts[0];
  if (path.pathname.endsWith("/summary")) return { account, balances: snapshot([{ metric: "NetLiquidation", value_num: "100000.000000", currency: "USD" }]) };
  if (path.pathname.endsWith("/positions")) return snapshot([{ trading_symbol: account.account_id === "SIM_A" ? "AAPL" : "MSFT", position: 10, currency: "USD" }]);
  if (path.pathname.endsWith("/open-orders")) return snapshot([]);
  if (path.pathname.includes("/catalogs/")) return { items: [] };
  if (path.pathname.endsWith("/orders")) return { items: [], has_more: false };
  return { items: [{ exec_id: "one", trading_symbol: "FILLED", side: "BUY", quantity: 10 }], limit: 50, offset: 0, has_more: false };
}

afterEach(() => vi.unstubAllGlobals());

it("switches profile/account without retaining another user's holdings", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => response(url) })));
  render(<Portfolio />);
  expect(await screen.findByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("Simulated", { selector: "strong" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Test profile"), { target: { value: B } });
  expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
  expect(await screen.findByText("MSFT")).toBeInTheDocument();
  expect(screen.getByLabelText("Broker account")).toHaveValue(JSON.stringify(["ibkr", "SIM_B"]));
  fireEvent.change(screen.getByLabelText("Test profile"), { target: { value: "unassigned" } });
  expect(await screen.findByText("No accounts registered for this profile.")).toBeInTheDocument();
  expect(screen.queryByText("MSFT")).not.toBeInTheDocument();
});

it("retains same-account data on refresh failure and clears a successful empty snapshot", async () => {
  let failed = false;
  let empty = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (failed && url.includes("/positions")) throw new Error("offline");
    return { ok: true, json: async () => empty && url.includes("/positions") ? snapshot([]) : response(url) };
  }));
  render(<Portfolio />);
  expect(await screen.findByText("AAPL")).toBeInTheDocument();
  failed = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh account" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Account refresh failed");
  expect(screen.getByText("AAPL")).toBeInTheDocument();
  failed = false;
  empty = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh account" }));
  expect(await screen.findByText("No open positions in this snapshot.")).toBeInTheDocument();
  expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(within(screen.getByRole("table", { name: "Balances" })).getByText("USD")).toBeInTheDocument();
});
