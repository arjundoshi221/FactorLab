import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { Portfolio } from "./Portfolio";
import { PortfolioShell } from "./PortfolioShell";

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

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/portfolio"); });

it("switches profile/account without retaining another user's holdings", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => response(url) })));
  render(<Portfolio />);
  expect(within(await screen.findByRole("table", { name: "Positions" })).getByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("Simulated", { selector: "strong" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Profile"), { target: { value: B } });
  expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
  expect(within(await screen.findByRole("table", { name: "Positions" })).getByText("MSFT")).toBeInTheDocument();
  expect(screen.getByLabelText("Broker account")).toHaveValue(JSON.stringify(["ibkr", "SIM_B"]));
  fireEvent.change(screen.getByLabelText("Profile"), { target: { value: "unassigned" } });
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
  expect(within(await screen.findByRole("table", { name: "Positions" })).getByText("AAPL")).toBeInTheDocument();
  failed = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh account" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Account refresh failed");
  expect(within(screen.getByRole("table", { name: "Positions" })).getByText("AAPL")).toBeInTheDocument();
  failed = false;
  empty = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh account" }));
  expect((await screen.findAllByText("No open positions in this snapshot.")).length).toBeGreaterThan(0);
  expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  fireEvent.click(screen.getByText("Account activity"));
  expect(within(screen.getByRole("table", { name: "Balances" })).getByText("USD")).toBeInTheDocument();
});

it("keeps the account in the shell's navigation links and clears it when switching profiles", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => response(url) })));
  render(<PortfolioShell><Portfolio /></PortfolioShell>);
  const selector = await screen.findByLabelText("Broker account");
  const selected = JSON.stringify(["ibkr", "SIM_A"]);
  fireEvent.change(selector, { target: { value: selected } });
  const nav = within(screen.getByRole("navigation", { name: "Portfolio navigation" }));
  let link = new URL(nav.getByRole("link", { name: "Themes" }).getAttribute("href")!, "http://localhost");
  expect(link.searchParams.get("user_id")).toBe(A);
  expect(link.searchParams.get("account")).toBe(selected);
  fireEvent.change(screen.getByLabelText("Profile"), { target: { value: B } });
  await waitFor(() => expect(new URL(nav.getByRole("link", { name: "Themes" }).getAttribute("href")!, "http://localhost").searchParams.get("user_id")).toBe(B));
  link = new URL(nav.getByRole("link", { name: "Themes" }).getAttribute("href")!, "http://localhost");
  expect(link.searchParams.get("user_id")).toBe(B);
  expect(link.searchParams.has("account")).toBe(false);
  expect(new URL(window.location.href).searchParams.has("account")).toBe(false);
});

it("charts net liquidation by range and aggregates base-currency positions only", async () => {
  const fetcher = vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    if (path.pathname.endsWith("/equity")) return { ok: true, json: async () => ({ currency: "USD", basis: "Last broker-reported net liquidation per bucket", points: [
      { t: "2026-10-01T00:00:00Z", value: "100000.00" }, { t: "2026-10-02T00:00:00Z", value: "103000.00" }] }) };
    if (path.pathname.endsWith("/positions")) return { ok: true, json: async () => snapshot([
      { trading_symbol: "AAPL", position: 10, market_value: "2000.00", unrealized_pnl: "150.00", currency: "USD" },
      { trading_symbol: "TCS", position: 5, market_value: "-90000.00", unrealized_pnl: "-5000.00", currency: "INR" }]) };
    return { ok: true, json: async () => response(url) };
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Portfolio />);
  expect(await screen.findByRole("img", { name: /Net liquidation history: 2 points/ })).toBeInTheDocument();
  expect(screen.getByText("▲ +3.00%")).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/equity?user_id=") && String(url).endsWith("range=3m"))).toBe(true);
  fireEvent.click(within(screen.getByRole("group", { name: "Equity range" })).getByRole("button", { name: "1Y" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("range=1y"))).toBe(true));
  const overview = within(screen.getByLabelText("Account overview"));
  expect(overview.getByText("2K")).toBeInTheDocument();
  expect(overview.getByText("150.00")).toHaveClass("portfolio-amount--positive");
  expect(screen.getByRole("group", { name: "Holdings by market value" })).toBeInTheDocument();
});

it("prefers broker-reported base-currency unrealized and gross values over position sums", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    if (path.pathname.endsWith("/summary")) return { ok: true, json: async () => ({ account: accounts[0], balances: snapshot([
      { metric: "NetLiquidation", value_num: "100000.000000", currency: "USD" },
      { metric: "UnrealizedPnL", value_num: "-321.500000", currency: "USD" },
      { metric: "GrossPositionValue", value_num: "71000.000000", currency: "USD" }]) }) };
    if (path.pathname.endsWith("/positions")) return { ok: true, json: async () => snapshot([
      { trading_symbol: "TCS", position: 5, market_value: "-90000.00", unrealized_pnl: "-5000.00", currency: "INR" }]) };
    return { ok: true, json: async () => response(url) };
  }));
  render(<Portfolio />);
  const overview = within(await screen.findByLabelText("Account overview"));
  expect(await overview.findByText("−321.50")).toHaveClass("portfolio-amount--negative");
  expect(overview.getByText("71K")).toBeInTheDocument();
  expect(overview.getAllByText("Broker-reported, all positions")).toHaveLength(2);
});

it("reads the Gateway's account-wide unrealized PnL from the BASE ledger row, not the base-currency ledger row", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    if (path.pathname.endsWith("/summary")) return { ok: true, json: async () => ({ account: accounts[0], balances: snapshot([
      { metric: "NetLiquidation", value_num: "44403.85", currency: "USD" },
      { metric: "$LEDGER-UnrealizedPnL", value_num: "0", currency: "USD" },
      { metric: "$LEDGER-UnrealizedPnL", value_num: "2737.06", currency: "GBP" },
      { metric: "$LEDGER-UnrealizedPnL", value_num: "3526.10", currency: "BASE" }]) }) };
    if (path.pathname.endsWith("/positions")) return { ok: true, json: async () => snapshot([
      { trading_symbol: "TCS", position: 5, market_value: "-90000.00", unrealized_pnl: "-5000.00", currency: "INR" }]) };
    return { ok: true, json: async () => response(url) };
  }));
  render(<Portfolio />);
  const overview = within(await screen.findByLabelText("Account overview"));
  expect(await overview.findByText(/3,526\.10/)).toHaveClass("portfolio-amount--positive");
  expect(overview.getByText("Unrealized").parentElement).toHaveTextContent("Broker-reported, all positions");
});

it("shows unknown, not zero, when no position is in the base currency", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    if (path.pathname.endsWith("/positions")) return { ok: true, json: async () => snapshot([
      { trading_symbol: "TCS", position: 5, market_value: "-90000.00", unrealized_pnl: "-5000.00", currency: "INR" }]) };
    return { ok: true, json: async () => response(url) };
  }));
  render(<Portfolio />);
  const overview = within(await screen.findByLabelText("Account overview"));
  await waitFor(() => expect(overview.getByText("Open positions").nextElementSibling).toHaveTextContent("1"));
  expect(overview.getByText("Unrealized").nextElementSibling).toHaveTextContent("—");
  expect(overview.getByText("Gross exposure").nextElementSibling).toHaveTextContent("—");
  expect(overview.queryByText("0.00")).not.toBeInTheDocument();
});

it("syncs IBKR Flex history for a live account and refreshes it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const live = { ...accounts[0], account_id: "U123", account_mode: "live", simulated: false };
  let latest = { state: "running" };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost").pathname;
    if (path.endsWith("/flex/runs") && init?.method === "POST") return { ok: true, json: async () => ({ state: "running", job_id: "j1" }) };
    if (path.endsWith("/flex/runs/latest")) return { ok: true, json: async () => latest };
    if (path.endsWith("/accounts")) return { ok: true, json: async () => ({ items: [live] }) };
    return { ok: true, json: async () => response(url) };
  });
  vi.stubGlobal("fetch", fetchMock);
  try {
    render(<Portfolio />);
    fireEvent.click(await screen.findByRole("button", { name: "Sync IBKR history" }));
    expect(await screen.findByRole("button", { name: "Syncing history…" })).toBeDisabled();
    latest = { state: "success", rows_written: 7 } as typeof latest;
    const calls = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5_000);
    expect(await screen.findByText(/History synced · 7 rows/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.slice(calls).some(([url]) => String(url).includes("/positions"))).toBe(true));
  } finally {
    vi.useRealTimers();
  }
});

it("hides the Flex sync for simulated accounts", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => response(url) })));
  render(<Portfolio />);
  await screen.findByRole("table", { name: "Positions" });
  expect(screen.queryByRole("button", { name: "Sync IBKR history" })).not.toBeInTheDocument();
});

it("pivots the allocation by sector in the base currency, rows expanding to positions", async () => {
  const tech = { id: "45", label: "Information Technology" };
  const energy = { id: "10", label: "Energy" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    if (path.pathname.endsWith("/dimensions")) return { ok: true, json: async () => ({ items: [{ id: "sector", label: "Sector", buckets: [energy, tech, { id: null, label: "Unclassified" }] }] }) };
    if (path.pathname.endsWith("/summary")) return { ok: true, json: async () => ({ account: accounts[0], balances: snapshot([{ metric: "ExchangeRate", segment: "", currency: "EUR", value_num: "2" }]) }) };
    if (path.pathname.endsWith("/positions")) {
      const labelled = path.searchParams.get("dims") === "sector";
      const row = (symbol: string, currency: string, value: string, pnl: string, sector: typeof tech) =>
        ({ trading_symbol: symbol, position: 1, currency, market_value: value, unrealized_pnl: pnl, ...(labelled ? { attributes: { sector } } : {}) });
      return { ok: true, json: async () => snapshot([row("AAPL", "USD", "300", "60", tech), row("SAP", "EUR", "50", "10", tech), row("XOM", "USD", "200", "-20", energy)]) };
    }
    return { ok: true, json: async () => response(url) };
  }));
  render(<Portfolio />);
  fireEvent.change(await screen.findByLabelText("Pivot by"), { target: { value: "sector" } });
  expect(window.location.search).toContain("pivot=sector");
  const table = await screen.findByRole("table", { name: "Exposure by sector" });
  const rows = within(table).getAllByRole("row").slice(1);
  // EUR 50 converts at 2 → USD 100, so tech gross is 400 of 600.
  expect(rows[0]).toHaveTextContent("Energy");
  expect(rows[0]).toHaveTextContent("200.00");
  expect(rows[1]).toHaveTextContent("Information Technology");
  expect(rows[1]).toHaveTextContent("400.00");
  expect(rows[1]).toHaveTextContent("66.7%");
  expect(rows[1]).toHaveTextContent("80.00");
  fireEvent.click(within(rows[1]).getByRole("button", { name: /Information Technology/ }));
  expect(within(table).getByText("SAP")).toBeInTheDocument();
  // The same pivot groups the open positions table and labels the account's trades.
  const positions = screen.getByRole("table", { name: "Positions by sector" });
  const energyGroup = within(positions).getByRole("rowgroup", { name: "Energy" });
  expect(energyGroup).toHaveTextContent("1 position");
  expect(within(energyGroup).getByText("XOM")).toBeInTheDocument();
  expect(within(within(positions).getByRole("rowgroup", { name: "Information Technology" })).getByText("SAP")).toBeInTheDocument();
  expect(vi.mocked(fetch)).toHaveBeenCalledWith(expect.stringMatching(/\/trades\?.*dims=sector/), expect.anything());
});
