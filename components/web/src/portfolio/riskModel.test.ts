import { describe, expect, it } from "vitest";

import { DEFAULT_LIMITS, computeRisk, contributionToReach, deployableCapacity, hasReconciliationGap, overallStatus, statusOf, stressTests } from "./riskModel";
import type { BrokerRow } from "./types";

const balance = (metric: string, value: number, currency = "SGD", segment = ""): BrokerRow => ({ metric, segment, currency, value_num: String(value) });

// Roughly the reference IBKR account: SGD base, USD-funded positions, BOXX as the cash equivalent.
const balances: BrokerRow[] = [
  balance("NetLiquidation", 43_637), balance("GrossPositionValue", 154_615), balance("InitMarginReq", 30_328),
  balance("MaintMarginReq", 27_230), balance("ExcessLiquidity", 16_407), balance("TotalCashValue", -154_051),
  balance("NetLiquidation", 999, "SGD", "S"), // a segment row must never be read as the account figure
  balance("CashBalance", 4_620, "SGD"), balance("CashBalance", -124_040, "USD"), balance("CashBalance", -154_051, "BASE"),
  balance("ExchangeRate", 1.3, "USD"), balance("ExchangeRate", 1, "SGD"),
];
const position = (symbol: string, usd: number, extra: BrokerRow = {}): BrokerRow => ({ trading_symbol: symbol, currency: "USD", position: 1, market_value: String(usd), ...extra });
const positions = [position("BOXX", 50_289.23), position("LLY", 32_898.46), position("CSPX", 20_442.31), position("CRWD", 11_573.08), position("GRID", 3_731.54)];

describe("risk engine", () => {
  const state = computeRisk("SGD", balances, positions, ["BOXX"]);

  it("reads account-wide base-currency figures and converts positions with the broker FX rate", () => {
    expect(state.nav).toBe(43_637);
    expect(state.metrics.grossLeverage).toBeCloseTo(154_615 / 43_637, 6);
    expect(state.holdings[0].symbol).toBe("BOXX");
    expect(state.holdings[1].value).toBeCloseTo(42_768, 0);
    expect(state.metrics.singleName).toBeCloseTo(42_768 / 43_637, 3);
    expect(state.metrics.equityLeverage).toBeCloseTo((154_615 - 65_376) / 43_637, 2);
    expect(state.borrowing).toBeCloseTo(124_040 * 1.3, 0);
    expect(state.cash.map(line => line.currency)).toEqual(["SGD", "USD"]);
    expect(hasReconciliationGap(state)).toBe(false);
  });

  it("flags unreconciled exposure and never reports green on it", () => {
    const gapped = computeRisk("SGD", balances.map(row => (row.metric === "GrossPositionValue" ? balance("GrossPositionValue", 197_685) : row)), positions, ["BOXX"]);
    expect(gapped.unreconciled).toBeCloseTo(43_070, 0);
    expect(hasReconciliationGap(gapped)).toBe(true);
    const relaxed = Object.fromEntries(Object.entries(DEFAULT_LIMITS).map(([key, limit]) => [key, key === "excessLiquidity" ? { target: 0, hard: 0 } : { target: 99, hard: 99 }]));
    expect(overallStatus(gapped, relaxed as typeof DEFAULT_LIMITS, false).status).toBe("unknown");
    expect(overallStatus(state, relaxed as typeof DEFAULT_LIMITS, false).status).toBe("green");
    expect(overallStatus(state, relaxed as typeof DEFAULT_LIMITS, true).status).toBe("unknown");
  });

  it("marks positions without an FX rate as unconverted instead of guessing", () => {
    const missing = computeRisk("SGD", balances.filter(row => row.metric !== "ExchangeRate"), positions, ["BOXX"]);
    expect(missing.unconverted).toEqual(["USD"]);
    expect(missing.metrics.equityLeverage).toBeNull();
    expect(missing.metrics.grossLeverage).toBeCloseTo(154_615 / 43_637, 6);
  });

  it("grades limits in both directions", () => {
    expect(statusOf(2, { target: 2.5, hard: 3 }, "max")).toBe("green");
    expect(statusOf(2.8, { target: 2.5, hard: 3 }, "max")).toBe("amber");
    expect(statusOf(3.1, { target: 2.5, hard: 3 }, "max")).toBe("red");
    expect(statusOf(0.35, { target: 0.4, hard: 0.3 }, "min")).toBe("amber");
    expect(statusOf(0.2, { target: 0.4, hard: 0.3 }, "min")).toBe("red");
    expect(statusOf(null, { target: 0.4, hard: 0.3 }, "min")).toBe("unknown");
    expect(overallStatus(state, DEFAULT_LIMITS, false).status).toBe("red");
  });

  it("sizes capacity by the tightest constraint", () => {
    const capacity = deployableCapacity(state, DEFAULT_LIMITS);
    expect(capacity.binding?.key).toBe("borrowing");
    expect(capacity.amount).toBe(0);
    const gross = capacity.constraints.find(item => item.key === "grossLeverage");
    expect(gross?.amount).toBeCloseTo(2.5 * 43_637 - 154_615, 6);
    expect(contributionToReach(state, 2.5)).toBeCloseTo(154_615 / 2.5 - 43_637, 6);
  });

  it("shocks equities but holds cash equivalents flat", () => {
    const rows = stressTests(state, DEFAULT_LIMITS);
    const ten = rows.find(row => row.id === "eq-0.1")!;
    expect(ten.navImpact).toBeCloseTo(-0.1 * (154_615 - 65_376), 0);
    expect(rows.find(row => row.id === "gap")?.label).toBe("LLY gaps −30%");
    expect(rows.find(row => row.id === "eq-0.35")?.result).toBe("call");
  });
});
