import { describe, expect, it } from "vitest";
import { FX_UNAVAILABLE, fxRate, inCurrency, type FxRate } from "./fx";
import type { PnlDetail, PnlGroup } from "./types";

// An SGD-base account: 1 USD = 1.3 SGD, 1 GBP = 1.7 SGD.
const RATES: FxRate[] = [
  { currency: "SGD", base: "SGD", rate: "1", as_of: null },
  { currency: "USD", base: "SGD", rate: "1.3", as_of: "t" },
  { currency: "GBP", base: "SGD", rate: "1.7", as_of: "t" },
];

function detail(symbol: string, currency: string, total: string): PnlDetail {
  return {
    broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false, trading_symbol: symbol, currency,
    quantity: "1", orders: ["1"], realized: "0", unrealized: total, commissions: "0", total,
    issues: [], price_time: null, history_start: null, theme_id: "t1", strategy_id: null, trade_name_id: null,
  };
}

function group(currency: string, details: PnlDetail[], total = details[0]?.total ?? "0"): PnlGroup {
  return {
    id: "t1", name: "Earnings", account_mode: "live", simulated: false, currency,
    realized: "0", unrealized: total, commissions: "0", total, issues: [], details,
  };
}

describe("fxRate", () => {
  it("converts through a shared base, both ways", () => {
    expect(fxRate("USD", "USD", [])).toBe(1);
    expect(fxRate("USD", "SGD", RATES)).toBeCloseTo(1.3);
    expect(fxRate("SGD", "USD", RATES)).toBeCloseTo(1 / 1.3);
    expect(fxRate("GBP", "USD", RATES)).toBeCloseTo(1.7 / 1.3);
    expect(fxRate("JPY", "USD", RATES)).toBeNull();
  });
});

describe("inCurrency", () => {
  it("merges one tag's currencies into a single group in the display currency", () => {
    const groups = [group("USD", [detail("AMAT", "USD", "100")]), group("GBP", [detail("BATS", "GBP", "-13")])];
    const [merged, ...rest] = inCurrency(groups, "USD", RATES);
    expect(rest).toHaveLength(0);
    expect(merged.currency).toBe("USD");
    expect(merged.details.map(row => [row.trading_symbol, row.currency, Number(row.total).toFixed(2)])).toEqual([
      ["AMAT", "USD", "100.00"], ["BATS", "USD", "-17.00"],
    ]);
    expect(Number(merged.total)).toBeCloseTo(83);
  });

  it("excludes a holding without a rate instead of guessing", () => {
    const [merged] = inCurrency([group("USD", [detail("AMAT", "USD", "100")]), group("JPY", [detail("SONY", "JPY", "500")])], "USD", RATES);
    expect(merged.total).toBe("100");
    expect(merged.issues).toContain(FX_UNAVAILABLE);
    expect(merged.excluded?.map(row => row.trading_symbol)).toEqual(["SONY"]);
  });

  it("keeps fees-only groups' totals", () => {
    const [merged] = inCurrency([group("USD", [detail("AMAT", "USD", "100")]), { ...group("SGD", []), commissions: "13", total: "-13" }], "USD", RATES);
    expect(Number(merged.commissions)).toBeCloseTo(10);
    expect(Number(merged.total)).toBeCloseTo(90);
  });
});
