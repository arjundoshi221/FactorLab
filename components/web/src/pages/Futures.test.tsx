import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Futures, structure } from "./Futures";
import type { FuturesContract, FuturesResponse } from "../futuresTypes";

const contract = (symbol: string, expiry: string, last: number | null, extra: Partial<FuturesContract> = {}): FuturesContract => ({
  symbol, contract_id: symbol, expiry, last, bid: null, ask: null, settlement_prev: null, change: null, change_pct: null,
  day_volume: null, oi: null, observed_at: last === null ? null : new Date().toISOString(), stale: false, ...extra,
});

const snapshot: FuturesResponse = {
  generated_at: new Date().toISOString(),
  market_open: true,
  items: [
    { root: "/CL", name: "WTI crude oil", product_type: "commodity_future", group: "energy", contracts: [
      contract("/CLZ26", "2026-11-19", 71.5, { settlement_prev: 72, change: -0.5, change_pct: -0.5 / 72 }),
      contract("/CLF27", "2026-12-17", 70.9),
      contract("/CLG27", "2027-01-20", 70.2, { stale: true }),
    ] },
    { root: "/ES", name: "E-mini S&P 500", product_type: "index_future", group: "index", contracts: [
      contract("/ESZ26", "2026-12-18", 6800.25, { bid: 6800, ask: 6800.5, settlement_prev: 6780, change: 20.25, change_pct: 20.25 / 6780, day_volume: 1_200_000, oi: 2_100_000 }),
      contract("/ESH27", "2027-03-19", 6860),
      contract("/ESM27", "2027-06-18", null, { settlement_prev: 6900 }),
    ] },
    { root: "/NQ", name: "E-mini Nasdaq-100", product_type: "index_future", group: "index", contracts: [
      contract("/NQZ26", "2026-12-18", 25000),
    ] },
  ],
};

beforeEach(() => window.history.replaceState({}, "", "/futures"));
afterEach(() => vi.unstubAllGlobals());

const seriesCalls: string[] = [];
function stub(body: unknown, points = 3) {
  seriesCalls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const match = /futures\/([^/]+)\/series\?range=(\w+)/.exec(url);
    if (!match) return { ok: true, json: async () => body };
    seriesCalls.push(url);
    const [, id, range] = match;
    const daily = !["1d", "5d"].includes(range);
    return { ok: true, json: async () => ({
      contract_id: decodeURIComponent(id), range, resolution: daily ? "daily" : range === "1d" ? "1min" : "15min",
      points: Array.from({ length: points }, (_, i) => ({
        t: daily ? `2026-10-2${i}` : `2026-10-29T14:0${i}:00Z`, price: 6800 + i,
      })),
    }) };
  }));
}

it("labels contango and backwardation from front to back", () => {
  expect(structure(snapshot.items[1].contracts)?.label).toBe("Contango");
  expect(structure(snapshot.items[0].contracts)?.label).toBe("Backwardation");
  expect(structure([contract("/X", "2026-12-01", 1)])).toBeNull();
});

it("charts the front contract's own history with the contract table", async () => {
  stub(snapshot);
  render(<Futures />);
  expect(await screen.findByRole("heading", { name: "E-mini S&P 500" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Equity index" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Globex open")).toBeInTheDocument();
  expect(screen.getByText("Contango")).toBeInTheDocument();
  expect(await screen.findByTestId("series-line")).toBeInTheDocument();
  expect(screen.getByText(/\/ESZ26 · expires 2026-12-18 · last trade per 15 minutes/)).toBeInTheDocument();
  expect(seriesCalls).toEqual(["/hub/api/v1/us/futures/%2FESZ26/series?range=5d"]);
  expect(within(screen.getByRole("group", { name: "Expiry" })).getByRole("button", { name: "Dec 26" }))
    .toHaveAttribute("aria-pressed", "true");
  const rows = within(screen.getByRole("table", { name: "Contracts" })).getAllByRole("row");
  expect(rows).toHaveLength(4);
  expect(within(rows[1]).getByText("6,800.25")).toBeInTheDocument();
  expect(within(rows[1]).getByText("+20.25")).toBeInTheDocument();
  expect(within(rows[3]).getByText("No quote")).toBeInTheDocument();
});

it("selects an expiry and range through the URL", async () => {
  stub(snapshot);
  render(<Futures />);
  fireEvent.click(await screen.findByRole("button", { name: "Mar 27" }));
  expect(new URLSearchParams(window.location.search).get("contract")).toBe("/ESH27");
  expect(await screen.findByText(/\/ESH27 · expires 2027-03-19/)).toBeInTheDocument();

  fireEvent.click(within(screen.getByRole("group", { name: "Range" })).getByRole("button", { name: "1M" }));
  expect(await screen.findByText(/daily close/)).toBeInTheDocument();
  expect(seriesCalls.at(-1)).toBe("/hub/api/v1/us/futures/%2FESH27/series?range=1m");

  // A table row picks the same contract as the expiry buttons.
  fireEvent.click(within(screen.getByRole("table", { name: "Contracts" })).getByRole("button", { name: "/ESZ26" }));
  expect(new URLSearchParams(window.location.search).get("contract")).toBe("/ESZ26");
});

it("draws the previous settlement only on the 1D range", async () => {
  window.history.replaceState({}, "", "/futures?range=1d");
  stub(snapshot);
  render(<Futures />);
  expect(await screen.findByTestId("settle-line")).toBeInTheDocument();
  expect(screen.getByText("Dashed line: previous settlement 6,780.00.")).toBeInTheDocument();
});

it("switches group and root through the URL", async () => {
  stub(snapshot, 1);
  render(<Futures />);
  fireEvent.click(await screen.findByRole("tab", { name: "Energy" }));
  expect(window.location.search).toBe("?group=energy");
  expect(screen.getByRole("heading", { name: "WTI crude oil" })).toBeInTheDocument();
  expect(screen.getByText("Backwardation")).toBeInTheDocument();
  expect(screen.getByText("· stale")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Equity index" }));
  fireEvent.click(screen.getByRole("button", { name: /\/NQ/ }));
  expect(window.location.search).toBe("?group=index&root=%2FNQ");
  expect(await screen.findByText(/Not enough recorded prices to chart \/NQZ26/)).toBeInTheDocument();
});

it("explains an empty recorder", async () => {
  stub({ ...snapshot, items: [] });
  render(<Futures />);
  expect(await screen.findByText("No futures recorded yet")).toBeInTheDocument();
});
