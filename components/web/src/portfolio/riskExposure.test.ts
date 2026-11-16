import { describe, expect, it } from "vitest";

import { aggregateExposure, splitPositions } from "./riskExposure";
import type { Holding } from "./riskModel";
import type { PnlDetail } from "./types";

const holding = (symbol: string, quantity: number, value: number | null, extra: Partial<Holding> = {}): Holding => ({
  symbol, listingId: null, beta: 1, betaSource: "assumed", currency: "USD", quantity, native: value, fx: 1, value, cashLike: false, maint: null, unrealized: null, realized: null, ...extra,
});
const detail = (symbol: string, quantity: string, theme: string | null, extra: Partial<PnlDetail> = {}): PnlDetail => ({
  broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false, trading_symbol: symbol, currency: "USD", quantity, orders: ["1"],
  realized: "0", unrealized: "0", commissions: "0", total: "0", issues: [], price_time: null, history_start: null,
  theme_id: theme, strategy_id: null, trade_name_id: null, ...extra,
});

describe("splitPositions", () => {
  it("splits a position across tags by open quantity and leaves the rest Untagged", () => {
    const slices = splitPositions([holding("AAPL", 100, 1000, { unrealized: 50 })], [], [detail("AAPL", "60", "ai"), detail("AAPL", "20", "value")], "U1");
    const round = (value: number | null) => value === null ? null : Math.round(value * 1e6) / 1e6;
    expect(slices.map(slice => [slice.theme_id, round(slice.share), round(slice.value), round(slice.unrealized)])).toEqual([
      ["ai", 0.6, 600, 30], ["value", 0.2, 200, 10], [null, 0.2, 200, 10],
    ]);
  });

  it("ignores closed, other-account and opposite-side holdings, and caps over-explained positions", () => {
    const slices = splitPositions([holding("MSFT", 10, 500), holding("XOM", -10, -300)], [], [
      detail("MSFT", "0", "closed"), detail("MSFT", "10", "other", { account_id: "U2" }), detail("MSFT", "8", "a"), detail("MSFT", "12", "b"), detail("XOM", "5", "long"),
    ], "U1");
    expect(slices.map(slice => [slice.symbol, slice.theme_id, slice.share])).toEqual([["MSFT", "a", 0.4], ["MSFT", "b", 0.6], ["XOM", null, 1]]);
  });

  it("carries the API's attribute labels by symbol and currency", () => {
    const tech = { id: "45", label: "Information Technology" };
    const [slice] = splitPositions([holding("AAPL", 1, 10)], [{ trading_symbol: "AAPL", currency: "USD", attributes: tech } as never], [], "U1");
    expect(slice.attributes).toEqual(tech);
  });
});

describe("aggregateExposure", () => {
  it("sums gross, net and beta-weighted exposure, cash equivalents at beta 0", () => {
    const slices = splitPositions([holding("AAPL", 1, 1000, { beta: 1.5, unrealized: 20 }), holding("XOM", -1, -400, { beta: 0.5 }), holding("BOXX", 1, 600, { cashLike: true, beta: 0 })], [], [], "U1");
    expect(aggregateExposure(slices)).toEqual({ gross: 2000, net: 1200, beta: 1300, unrealized: 20, positions: 3, unconverted: [] });
  });

  it("is unavailable, not partial, when a position has no FX rate, and unknown while betas load", () => {
    const blocked = splitPositions([holding("AAPL", 1, 1000), holding("NOVO", 1, null, { currency: "DKK", native: 50, fx: null })], [], [], "U1");
    expect(aggregateExposure(blocked)).toMatchObject({ gross: null, net: null, beta: null, positions: 2, unconverted: ["NOVO"] });
    expect(aggregateExposure(splitPositions([holding("AAPL", 1, 1000, { beta: null })], [], [], "U1")).beta).toBeNull();
  });
});
