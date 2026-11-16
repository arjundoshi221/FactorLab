import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { BrokerAccount } from "./types";
import { OrderTagging, PortfolioGroups } from "./PortfolioBook";

const A = "d677331a-a04f-454a-8a1e-02425be2c101";
const B = "d677331a-a04f-454a-8a1e-02425be2c102";
const account: BrokerAccount = { broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", user_id: A, display_name: "Alice", simulated: true };
const entries = { themes: [{ id: "theme", user_id: A, name: "AI", description: "", active: true }], strategies: [{ id: "strategy", user_id: A, name: "Momentum", description: "", active: true }], "trade-names": [{ id: "trade", user_id: A, name: "Chip basket", description: "", active: true }] };
const group = { id: "theme", name: "AI", currency: "USD", account_mode: "paper", simulated: true, realized: "400", unrealized: "50", commissions: "3", total: "447", issues: ["stale_price"], details: [] };

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/portfolio"); });

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
  expect(await screen.findByRole("article", { name: "ABC order 100" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save tags" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Assign tags" }));
  expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Themes for order 100"), { target: { value: "theme" } });
  fireEvent.change(screen.getByLabelText("Strategies for order 100"), { target: { value: "strategy" } });
  fireEvent.change(screen.getByLabelText("Trade names for order 100"), { target: { value: "trade" } });
  fireEvent.click(screen.getByRole("button", { name: "Save tags" }));
  await waitFor(() => expect(writes).toEqual([{ theme_id: "theme", strategy_id: "strategy", trade_name_id: "trade" }]));
  fireEvent.click(screen.getByText("2 fills"));
  expect(screen.getByRole("table", { name: "Fills for order 100" })).toHaveTextContent("one");
  fireEvent.click(await screen.findByRole("button", { name: "Edit tags" }));
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
  expect(await screen.findAllByText("447.00")).not.toHaveLength(0);
  expect(screen.getAllByText("Stale price")).not.toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "New theme" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Energy" } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Thesis" } });
  fireEvent.click(screen.getByRole("button", { name: "Create theme" }));
  expect(await screen.findByText("Energy", { selector: "strong" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Energy supply" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("Energy supply", { selector: "strong" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Archive" })[1]);
  const archivedToggle = await screen.findByLabelText("Show archived (1)");
  expect(screen.queryByText("Energy supply", { selector: "strong" })).not.toBeInTheDocument();
  fireEvent.click(archivedToggle);
  fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Restore" })).not.toBeInTheDocument());
  expect(writes.map(row => row.method)).toEqual(["POST", "PUT", "PUT", "PUT"]);
  expect(writes[2].body.active).toBe(false);
  expect(writes[3].body.active).toBe(true);
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
  expect(await screen.findAllByText("447.00")).not.toHaveLength(0);
  failRefresh = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh PnL" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("PnL refresh failed");
  expect(screen.getAllByText("447.00")).not.toHaveLength(0);
  fireEvent.change(screen.getByLabelText("PnL account"), { target: { value: JSON.stringify(["ibkr", "SIM_A"]) } });
  await waitFor(() => expect(screen.queryAllByText("447.00")).toHaveLength(0));
  expect(await screen.findByRole("alert")).toHaveTextContent("PnL refresh failed");
  fireEvent.change(screen.getByLabelText("Profile"), { target: { value: B } });
  expect(screen.queryAllByText("447.00")).toHaveLength(0);
});

it("keeps an unfinished tag selection during refresh and lets users filter the current page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const kind = new URL(url, "http://localhost").pathname.split("/").pop() as keyof typeof entries;
    const result = url.includes("/catalogs/") ? { items: entries[kind] } : { items: [
      { key: "100", perm_id: "100", trading_symbol: "ABC", currency: "USD", side: "BUY", status: "Executed", filled_quantity: "20", taggable: true, theme_id: null, strategy_id: null, trade_name_id: null, fills: [] },
      { key: "200", perm_id: "200", trading_symbol: "XYZ", currency: "USD", side: "SELL", status: "Executed", filled_quantity: "2", taggable: true, theme_id: "theme", strategy_id: "strategy", trade_name_id: "trade", fills: [] },
    ], has_more: false };
    return { ok: true, json: async () => result };
  }));
  const { rerender } = render(<OrderTagging account={account} />);
  fireEvent.click(await screen.findByRole("button", { name: "Assign tags" }));
  fireEvent.change(screen.getByLabelText("Themes for order 100"), { target: { value: "theme" } });
  fireEvent.click(screen.getByRole("button", { name: "Refresh orders and tags" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh orders and tags" })).not.toBeDisabled());
  expect(screen.getByLabelText("Themes for order 100")).toHaveValue("theme");
  rerender(<OrderTagging account={account} revision={1} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh orders and tags" })).not.toBeDisabled());
  expect(screen.getByLabelText("Themes for order 100")).toHaveValue("theme");
  fireEvent.change(screen.getByLabelText("Order tag status"), { target: { value: "tagged" } });
  expect(screen.queryByRole("article", { name: "ABC order 100" })).not.toBeInTheDocument();
  expect(screen.getByRole("article", { name: "XYZ order 200" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search orders on this page"), { target: { value: "does-not-exist" } });
  expect(screen.getByText("No matching orders")).toBeInTheDocument();
});

it("puts each currency in its own section and expands holdings inline with the account link", async () => {
  const detail = { broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", simulated: true, currency: "USD", trading_symbol: "AAPL", quantity: "4", realized: "0", unrealized: "10", commissions: ".35", total: "9.65", issues: [], price_time: "2026-10-15T12:00:00Z", history_start: "2026-10-15T10:00:00Z", orders: ["123"], theme_id: "theme", strategy_id: null, trade_name_id: null };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }] }
    : url.includes("/accounts") ? { items: [account] } : url.includes("/pnl/") ? { items: [{ ...group, details: [detail] }, { ...group, currency: "EUR", total: null, realized: null, issues: ["missing_price"], details: [] }] } : { items: [] } })));
  render(<PortfolioGroups kind="themes" />);
  const table = await screen.findByRole("treegrid", { name: "PnL by theme" });
  const sections = within(table).getAllByRole("rowgroup").slice(1);
  expect(sections.map(section => section.getAttribute("aria-label"))).toEqual(["EUR, paper, simulated", "USD, paper, simulated"]);
  expect(within(sections[1]).getByText("447.00")).toBeInTheDocument();
  // The EUR group has no holdings: nothing to expand, and its badge names the missing price.
  expect(within(sections[0]).getByText("Price unavailable")).toBeInTheDocument();
  expect(within(sections[0]).queryByRole("button", { name: /Expand/ })).not.toBeInTheDocument();
  expect(within(table).queryByText("AAPL")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Expand AI (USD, paper, simulated)" }));
  expect(within(table).getByText("AAPL")).toBeInTheDocument();
  const link = new URL(screen.getByRole("link", { name: "1 contributing order" }).getAttribute("href")!, "http://localhost");
  expect(link.searchParams.get("user_id")).toBe(A);
  expect(link.searchParams.get("account")).toBe(JSON.stringify(["ibkr", "SIM_A"]));
  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(within(table).queryByText("AAPL")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
  expect(within(table).getByText("AAPL")).toBeInTheDocument();
});

it("creates a catalog entry from the tag dropdown and selects it", async () => {
  const created: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    let result: unknown;
    if (init?.method === "POST") { created.push({ path: path.pathname, body: JSON.parse(String(init.body)) }); result = { id: "fresh", name: "Energy" }; }
    else if (path.pathname.includes("/catalogs/")) { result = { items: entries[path.pathname.split("/").pop() as keyof typeof entries] }; }
    else { result = { items: [{ key: "100", perm_id: "100", trading_symbol: "ABC", currency: "USD", side: "BUY", status: "Executed", filled_quantity: "20", taggable: true,
      theme_id: null, strategy_id: null, trade_name_id: null, fills: [] }], has_more: false }; }
    return { ok: true, json: async () => result };
  }));
  render(<OrderTagging account={account} />);
  fireEvent.click(await screen.findByRole("button", { name: "Assign tags" }));
  fireEvent.change(screen.getByLabelText("Themes for order 100"), { target: { value: "__new__" } });
  const input = screen.getByLabelText("New theme name for order 100");
  fireEvent.change(input, { target: { value: " Energy " } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("Themes for order 100")).toHaveValue("fresh"));
  expect(created).toEqual([{ path: "/hub/api/v1/portfolio/catalogs/themes", body: { name: "Energy", description: "", active: true } }]);
  expect(within(screen.getByLabelText("Themes for order 100")).getByRole("option", { name: "Energy" })).toBeInTheDocument();
});

it("switches PnL to YTD with a New York year start and lists account fees apart", async () => {
  const requests: string[] = [];
  const fee = { broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", simulated: true, currency: "USD", fees: "10.5", commission_adjustments: "-1.25", total: "9.25" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    requests.push(url);
    const ytd = url.includes("since=");
    return { ok: true, json: async () => url.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }] }
      : url.includes("/accounts") ? { items: [account] }
      : url.includes("/pnl/") ? { items: [{ ...group, total: ytd ? "397" : "447" }], account_fees: ytd ? [fee] : [] } : { items: [] } };
  }));
  render(<PortfolioGroups kind="strategies" />);
  expect(await screen.findByText(/No account-level fees/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^YTD / }));
  const table = await screen.findByRole("table", { name: "Account fees YTD" });
  expect(within(table).getByText("9.25")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Net realized YTD" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Fees YTD" })).toBeInTheDocument();
  const pnl = new URL(requests.filter(url => url.includes("/pnl/")).at(-1)!, "http://localhost");
  expect(pnl.searchParams.get("since")).toMatch(/^\d{4}-01-01$/);
  expect(new URLSearchParams(window.location.search).get("period")).toBe("ytd");
});

it("names the real blocker, lists excluded instruments and hides charts with no values", async () => {
  const partial = { ...group, issues: ["incomplete_history", "stale_price", "unassigned"], total: "100", excluded: [{ trading_symbol: "CRWD", account_id: "U1", issues: ["incomplete_history"] }] };
  const blank = { ...group, id: "other", currency: "SGD", total: null, realized: null, issues: ["unsupported_instrument"] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }] }
    : url.includes("/accounts") ? { items: [account] } : url.includes("/pnl/") ? { items: [partial, blank], account_fees: [] } : { items: [] } })));
  render(<PortfolioGroups kind="strategies" />);
  const table = await screen.findByRole("treegrid", { name: "PnL by strategy" });
  expect(within(table).getAllByText("History incomplete")).not.toHaveLength(0);
  expect(within(table).getAllByText("Unsupported instrument")).not.toHaveLength(0);
  expect(within(table).getByText("Excludes CRWD (fees included)")).toBeInTheDocument();
});

it("shortens a long excluded list to a count and sorts by any column", async () => {
  const many = ["AMAT", "ASND", "BN", "CSCO", "META"].map(trading_symbol => ({ trading_symbol, account_id: "U1", issues: ["incomplete_history"] }));
  const loser = { ...group, id: "loser", name: "Loser", total: "-50", realized: "900", excluded: many };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }] }
    : url.includes("/accounts") ? { items: [account] } : url.includes("/pnl/") ? { items: [loser, { ...group, issues: [] }], account_fees: [] } : { items: [] } })));
  render(<PortfolioGroups kind="themes" />);
  const table = await screen.findByRole("treegrid", { name: "PnL by theme" });
  const note = within(table).getByText("Excludes 5 instruments (fees included)");
  expect(note).toHaveAttribute("title", expect.stringContaining("META"));
  const names = () => within(table).getAllByRole("row").filter(row => row.getAttribute("aria-level") === "1").map(row => row.querySelector("strong")!.textContent);
  expect(names()).toEqual(["AI", "Loser"]);
  fireEvent.click(within(table).getByRole("button", { name: "Realized" }));
  expect(names()).toEqual(["Loser", "AI"]);
  expect(within(table).getByRole("columnheader", { name: "Realized" })).toHaveAttribute("aria-sort", "descending");
});

it("shows IBKR figures beside ours and reconciles instruments, problems first", async () => {
  const withIbkr = { ...group, ibkr_realized: "598", ibkr_unrealized: "0", ibkr_total: "598", ibkr_issues: [] };
  const split = { ...group, id: "other", name: "Earnings", ibkr_realized: "0", ibkr_unrealized: null, ibkr_total: null, ibkr_issues: ["ibkr_split_position"], ibkr_excluded: [{ trading_symbol: "MSFT", account_id: "U1", issues: ["ibkr_split_position"] }] };
  const row = (symbol: string, status: string, difference: string | null, reasons: string[] = []) => ({ broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false, trading_symbol: symbol, currency: "USD", quantity: "10", fills: 3,
    ours: { realized: "100", unrealized: "700", commissions: "3", total: "797" }, ibkr: { realized: "598", unrealized: "199", total: difference === null ? null : "797" }, difference, status, reasons });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith("/profiles") ? { items: [{ user_id: A, display_name: "Alice" }] }
    : url.includes("/accounts") ? { items: [account] } : url.includes("/pnl/") ? { items: [withIbkr, split], account_fees: [],
      reconciliation: [row("NVDA", "mismatch", "-100"), row("TSLA", "ibkr_unavailable", null, ["ibkr_awaiting_statement"]), row("AAPL", "match", "0")],
      statements: [{ broker_code: "ibkr", account_id: "U1", generated_at: "2026-11-13T13:46:47Z", statement_to: "2026-11-12", lot_detail: false }] } : { items: [] } })));
  render(<PortfolioGroups kind="themes" />);
  const table = await screen.findByRole("treegrid", { name: "PnL by theme" });
  expect(within(table).getByRole("columnheader", { name: "IBKR" })).toBeInTheDocument();
  expect(within(table).getAllByText("598.00")).not.toHaveLength(0);
  expect(within(table).getAllByLabelText("Differs from ours by 151")).not.toHaveLength(0);
  expect(within(table).getByText("Excludes MSFT")).toBeInTheDocument();

  // Folded under the report; the summary carries the counts.
  expect(screen.getByText("Reconciliation with IBKR").closest("details")).not.toHaveAttribute("open");
  const reconciliation = screen.getByRole("table", { name: "Reconciliation with IBKR" });
  expect(screen.getByText(/1 of 3 match/)).toBeInTheDocument();
  expect(within(reconciliation).getByText("Differs")).toBeInTheDocument();
  expect(within(reconciliation).getByText(/Traded or changed since the latest IBKR Flex statement/)).toBeInTheDocument();
  expect(within(reconciliation).queryByText("AAPL")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show matches" }));
  expect(within(reconciliation).getByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText(/Add “Lot” level of detail/)).toBeInTheDocument();
});

it("pivots tagged PnL by sector both ways, keeping the choice in the URL", async () => {
  const tech = { id: "45", label: "Information Technology" };
  const energy = { id: "10", label: "Energy" };
  const holding = (symbol: string, sector: typeof tech, theme: string, realized: string) => ({
    broker_code: "ibkr", account_id: "SIM_A", account_mode: "paper", simulated: true, trading_symbol: symbol, currency: "USD",
    quantity: "1", orders: ["1"], realized, unrealized: "0", commissions: "1", total: String(Number(realized) - 1), issues: [],
    price_time: null, history_start: null, theme_id: theme, strategy_id: null, trade_name_id: null, attributes: { sector },
  });
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    requests.push(path.pathname + path.search);
    let result: unknown;
    if (path.pathname.endsWith("/dimensions")) result = { items: [{ id: "sector", label: "Sector", buckets: [energy, tech, { id: null, label: "Unclassified" }] }] };
    else if (path.pathname.endsWith("/profiles")) result = { items: [{ user_id: A, display_name: "Alice", active: true }] };
    else if (path.pathname.endsWith("/accounts")) result = { items: [account] };
    else if (path.pathname.includes("/pnl/")) {
      const dims = path.searchParams.get("dims") === "sector";
      const strip = (row: ReturnType<typeof holding>) => dims ? row : { ...row, attributes: undefined };
      result = { items: [
        { ...group, issues: [], realized: "30", unrealized: "0", commissions: "2", total: "28", details: [holding("AAPL", tech, "theme", "10"), holding("XOM", energy, "theme", "20")].map(strip) },
        { ...group, id: "value", name: "Value", issues: [], realized: "5", unrealized: "0", commissions: "1", total: "4", details: [holding("MSFT", tech, "value", "5")].map(strip) },
      ] };
    } else result = { items: entries.themes };
    return { ok: true, json: async () => result };
  }));
  render(<PortfolioGroups kind="themes" />);
  const add = await screen.findByLabelText("Add grouping level");
  await waitFor(() => expect(within(add).getByRole("option", { name: "Sector" })).toBeInTheDocument());
  fireEvent.change(add, { target: { value: "sector" } });
  expect(new URLSearchParams(window.location.search).get("group")).toBe("theme,sector");
  await waitFor(() => expect(requests.some(request => request.includes("/pnl/themes") && request.includes("dims=sector"))).toBe(true));

  // Theme › Sector: each theme opens into sector subtotals.
  const table = await screen.findByRole("treegrid", { name: "PnL by theme" });
  await waitFor(() => expect(within(table).getByRole("columnheader", { name: "Theme › Sector" })).toBeInTheDocument());
  const level2 = () => within(table).getAllByRole("row").filter(row => row.getAttribute("aria-level") === "2").map(row => row.querySelector("strong")!.textContent);
  expect(level2()).toEqual(["Energy", "Information Technology", "Information Technology"]);

  // Sector › Theme: sectors become the rows, combining both themes' tech holdings.
  fireEvent.click(screen.getByRole("button", { name: "Move Sector earlier" }));
  expect(new URLSearchParams(window.location.search).get("group")).toBe("sector,theme");
  const bySector = await screen.findByRole("treegrid", { name: "PnL by sector" });
  const techRow = within(bySector).getAllByRole("row").find(row => row.getAttribute("aria-level") === "1" && row.textContent?.startsWith("Information Technology"))!;
  expect(techRow).toHaveTextContent("15.00");
  expect(within(bySector).getAllByRole("row").filter(row => row.getAttribute("aria-level") === "2").map(row => row.querySelector("strong")!.textContent)).toEqual(["AI", "AI", "Value"]);

  // Strategy as a third level; then remove Sector to return to the default (no group= in the URL).
  fireEvent.change(screen.getByLabelText("Add grouping level"), { target: { value: "strategy" } });
  expect(new URLSearchParams(window.location.search).get("group")).toBe("sector,theme,strategy");
  fireEvent.click(screen.getByRole("button", { name: "Remove Strategy" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove Sector" }));
  expect(window.location.search).not.toContain("group");
  expect(screen.getByRole("button", { name: "Remove Theme" })).toBeDisabled();
});

it("shows the report unpivoted and says so when sector classification fails", async () => {
  window.history.replaceState(null, "", "/portfolio/themes?pivot=sector");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    let result: unknown;
    if (path.pathname.endsWith("/dimensions")) result = { items: [{ id: "sector", label: "Sector", buckets: [] }] };
    else if (path.pathname.endsWith("/profiles")) result = { items: [{ user_id: A, display_name: "Alice", active: true }] };
    else if (path.pathname.endsWith("/accounts")) result = { items: [account] };
    else if (path.pathname.includes("/pnl/")) result = { items: [group], unavailable_dimensions: ["sector"] };
    else result = { items: entries.themes };
    return { ok: true, json: async () => result };
  }));
  render(<PortfolioGroups kind="themes" />);
  expect(await screen.findByText(/Sector classification is unavailable right now/)).toBeInTheDocument();
  expect(screen.getByRole("treegrid", { name: "PnL by theme" })).toBeInTheDocument();
});
