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

function stub(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => body })));
}

it("labels contango and backwardation from front to back", () => {
  expect(structure(snapshot.items[1].contracts)?.label).toBe("Contango");
  expect(structure(snapshot.items[0].contracts)?.label).toBe("Backwardation");
  expect(structure([contract("/X", "2026-12-01", 1)])).toBeNull();
});

it("draws the first group's first root curve and its contract table", async () => {
  stub(snapshot);
  render(<Futures />);
  expect(await screen.findByRole("heading", { name: "E-mini S&P 500" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Equity index" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Globex open")).toBeInTheDocument();
  expect(screen.getByText("Contango")).toBeInTheDocument();
  // The unquoted June contract is drawn at its previous settlement.
  expect(screen.getAllByTestId("curve-point")).toHaveLength(3);
  const rows = within(screen.getByRole("table", { name: "Contracts" })).getAllByRole("row");
  expect(rows).toHaveLength(4);
  expect(within(rows[1]).getByText("6,800.25")).toBeInTheDocument();
  expect(within(rows[1]).getByText("+20.25")).toBeInTheDocument();
  expect(within(rows[3]).getByText("No quote")).toBeInTheDocument();
});

it("switches group and root through the URL", async () => {
  stub(snapshot);
  render(<Futures />);
  fireEvent.click(await screen.findByRole("tab", { name: "Energy" }));
  expect(window.location.search).toBe("?group=energy");
  expect(screen.getByRole("heading", { name: "WTI crude oil" })).toBeInTheDocument();
  expect(screen.getByText("Backwardation")).toBeInTheDocument();
  expect(screen.getByText("· stale")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Equity index" }));
  fireEvent.click(screen.getByRole("button", { name: /\/NQ/ }));
  expect(window.location.search).toBe("?group=index&root=%2FNQ");
  expect(screen.getByText(/Not enough priced contracts/)).toBeInTheDocument();
});

it("explains an empty recorder", async () => {
  stub({ ...snapshot, items: [] });
  render(<Futures />);
  expect(await screen.findByText("No futures recorded yet")).toBeInTheDocument();
});
