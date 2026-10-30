import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { curveShape, rankMovers } from "./Cockpit";

const returns = (value: number) => ({ "1d": value, "1w": value * 2, "1m": value * 3, "3m": null, ytd: value * 5, "1y": value * 6 });
const performance = {
  as_of: "2026-10-29", weighting: "cw", periods: ["1d", "1w", "1m", "3m", "ytd", "1y"],
  sectors: [
    { sector_id: "45", name: "Information Technology", code: "us_gics_45_cw", level: 120, complete: true, cw_etf: "XLK", ew_etf: "RSPT",
      returns: { ew: returns(0.01), cw: returns(0.012), cw_etf: returns(0.011), ew_etf: returns(0.009) },
      movers: { top: [{ symbol: "NVDA", ret_1d: 0.041, contribution: 0.002 }], bottom: [{ symbol: "INTC", ret_1d: -0.032, contribution: -0.0004 }] } },
    { sector_id: "10", name: "Energy", code: "us_gics_10_cw", level: 90, complete: true, cw_etf: "XLE", ew_etf: "RSPG",
      returns: { ew: returns(-0.004), cw: returns(-0.006), cw_etf: returns(-0.005), ew_etf: returns(-0.004) },
      movers: { top: [{ symbol: "XOM", ret_1d: 0.004, contribution: 0.0003 }], bottom: [{ symbol: "NVDA", ret_1d: 0.041, contribution: 0 }] } },
  ],
  benchmarks: { composite_ew: returns(0.002), composite_cw: returns(0.003), $SPX: returns(0.0042), RSP: returns(0.001) },
};
const futures = {
  generated_at: "2026-10-30T10:00:00Z", market_open: true,
  items: [{ root: "/ES", name: "E-mini S&P 500", product_type: "future", group: "index", contracts: [
    { symbol: "/ESZ26", contract_id: "c1", expiry: "2026-12-18", last: 6010.25, bid: 6010, ask: 6010.5, settlement_prev: 6000, change: 10.25, change_pct: 0.0017, day_volume: 1000, oi: 2000, observed_at: null, stale: false },
    { symbol: "/ESH27", contract_id: "c2", expiry: "2027-03-19", last: 6065.5, bid: null, ask: null, settlement_prev: null, change: null, change_pct: null, day_volume: null, oi: null, observed_at: null, stale: false },
  ] }],
};
const indexes = { tracked: [{ code: "$SPX", name: "S&P 500", value: 6000.5, change: 0.0042, trade_date: "2026-10-29", complete: true, group: "index" }], constructed: [], sectors: [], base_date: "2020-01-02", universe: "IWV" };
const summary = { generated_at: "2026-10-30T10:00:00Z", verdict: "attention", counts: { critical: 0, warning: 1, info: 0, ok: 4, unknown: 0 },
  checks: [{ id: "x", area: "sources", severity: "warning", title: "Schwab quotes are late", detail: "", count: null, observed_at: null, href: "/monitoring#x", link_label: "Open" }] };

function respond(routes: Record<string, unknown>, failing: string[] = []) {
  return vi.fn(async (request: RequestInfo | URL) => {
    const url = String(request);
    if (failing.some((prefix) => url.startsWith(prefix))) return { ok: false, status: 503, json: async () => ({ detail: "Down" }) } as Response;
    const match = Object.keys(routes).filter((prefix) => url.startsWith(prefix)).sort((a, b) => b.length - a.length)[0];
    return { ok: true, json: async () => (match ? routes[match] : { items: [] }) } as Response;
  });
}

const routes = {
  "/hub/api/v1/us/indexes": indexes,
  "/hub/api/v1/us/indexes/series": { items: [{ code: "$SPX", name: "S&P 500", kind: "tracked", points: [{ trade_date: "2026-08-01", value: 5800, complete: true }, { trade_date: "2026-10-29", value: 6000, complete: true }] }] },
  "/hub/api/v1/us/sectors/performance": performance,
  "/hub/api/v1/us/futures": futures,
  "/hub/api/v1/monitoring/summary": summary,
  "/hub/api/v1/portfolio/profiles": { items: [] },
  "/hub/api/v1/overview": {
    generated_at: "2026-10-30T10:00:00Z", summary: { database: "factorlab", table_count: 2, populated_tables: 2, stored_rows: 18605, bytes_on_disk: 1, healthy_tables: 1, attention_tables: 1 },
    india: { coverage_percent: 97.5 }, political: { fresh_datasets: 3, datasets_with_data: 4 }, tables: [], warnings: [],
  },
  "/hub/api/v1/political/trades": { items: [], next_cursor: null },
  "/hub/api/v1/political/metrics/timeseries": { metric: "trades", date_basis: "transaction", group_by: "month", points: [] },
};

describe("Cockpit", () => {
  beforeEach(() => window.history.replaceState({}, "", "/"));
  afterEach(() => vi.unstubAllGlobals());

  it("renders the market, futures, movers and system panels from their feeds", async () => {
    vi.stubGlobal("fetch", respond(routes));
    render(<App />);
    const indexPanel = await screen.findByRole("table", { name: "Tracked indexes" });
    expect(within(indexPanel).getByText("6,000.50")).toBeInTheDocument();
    expect(within(indexPanel).getByText("▲ +0.42%")).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Sector returns, cap weight" })).toHaveTextContent("Information Technology");
    expect(screen.getByTitle("Information Technology 1D +1.20%")).toBeInTheDocument();
    const board = screen.getByRole("table", { name: "Front-month futures" });
    expect(within(board).getByText("Contango")).toBeInTheDocument();
    expect(within(board).getByText("▲ +0.17%")).toBeInTheDocument();
    expect(within(board).getByRole("link", { name: "/ES" })).toHaveAttribute("href", "/market-data?view=futures&group=index&root=%2FES");
    expect(screen.getByRole("list", { name: "Top gainers" })).toHaveTextContent("NVDA");
    expect(screen.getByRole("list", { name: "Top decliners" })).toHaveTextContent("INTC");
    expect(await screen.findByRole("link", { name: "Schwab quotes are late" })).toHaveAttribute("href", "/monitoring#x");
    expect(screen.getByText("No broker accounts registered")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "India 1-min: 97.5%, target 95%" })).toBeInTheDocument();
    expect(screen.getByText("3/4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Home" })).toHaveClass("active");
  });

  it("keeps the other panels working when one feed is down", async () => {
    vi.stubGlobal("fetch", respond(routes, ["/hub/api/v1/us/sectors/performance"]));
    render(<App />);
    expect(await screen.findByRole("table", { name: "Front-month futures" })).toBeInTheDocument();
    const sectors = screen.getByRole("region", { name: "Sector heat" });
    expect(await within(sectors).findByRole("alert")).toHaveTextContent("Feed unavailable");
    expect(screen.getByRole("table", { name: "Tracked indexes" })).toBeInTheDocument();
  });

  it("ranks movers across sectors without duplicates", () => {
    const { top, bottom } = rankMovers(performance as never);
    expect(top.map((mover) => mover.symbol)).toEqual(["NVDA", "XOM"]);
    expect(bottom.map((mover) => mover.symbol)).toEqual(["INTC"]);
  });

  it("labels the curve from front to back contract", () => {
    expect(curveShape(futures.items[0] as never)).toBe("Contango");
    const inverted = { ...futures.items[0], contracts: [...futures.items[0].contracts].reverse() };
    expect(curveShape(inverted as never)).toBe("Backwardation");
  });
});

describe("Command bar", () => {
  beforeEach(() => window.history.replaceState({}, "", "/roadmap"));
  afterEach(() => vi.unstubAllGlobals());

  it("opens with Ctrl+K, filters pages and closes with Escape", async () => {
    vi.stubGlobal("fetch", respond({ "/hub/api/v1/market-data/instruments": { items: [{ kind: "listing", id: "abc", symbol: "AAPL", name: "Apple Inc.", country: "US", exchange: "NASDAQ", instrument_type: "equity" }], total: 1, limit: 50, offset: 0 } }));
    render(<App />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "Command bar" });
    const input = within(dialog).getByRole("combobox", { name: "Command" });
    fireEvent.change(input, { target: { value: "futures" } });
    expect(within(dialog).getAllByRole("option")[0]).toHaveTextContent("Futures");
    fireEvent.change(input, { target: { value: "aapl" } });
    await waitFor(() => expect(within(dialog).getByRole("option", { name: /Apple Inc\./ })).toBeInTheDocument());
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("market-data/instruments?search=aapl"))).toBe(true);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Command bar" })).not.toBeInTheDocument();
  });

  it("opens from the toolbar button", () => {
    vi.stubGlobal("fetch", respond({}));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Open command bar" }));
    expect(screen.getByRole("dialog", { name: "Command bar" })).toBeInTheDocument();
  });
});
