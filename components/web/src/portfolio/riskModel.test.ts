import { describe, expect, it } from "vitest";

import { DEFAULT_LIMITS, DEFAULT_STRESS, LIMIT_SPECS, assetClassAllocation, computeRisk, concentrationStats, currencyExposure, grossShare, contributionToReach, deployableCapacity, estimateBeta, explainMetric, hasReconciliationGap, overallStatus, statusOf, stressTests } from "./riskModel";
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
  // Betas loaded with no measurements: every equity beta is assumed 1.0.
  const state = computeRisk("SGD", balances, positions, ["BOXX"], {});

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
    const gapped = computeRisk("SGD", balances.map(row => (row.metric === "GrossPositionValue" ? balance("GrossPositionValue", 197_685) : row)), positions, ["BOXX"], {});
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

  it("nets shorts in net leverage and weights exposure by beta", () => {
    const withShort = [...positions, position("QQQ", -10_000, { listing_id: "qqq" })];
    const longs = [position("LLY", 32_898.46, { listing_id: "lly" }), position("CSPX", 20_442.31), position("BOXX", 50_289.23), position("QQQ", -10_000, { listing_id: "qqq" })];
    const mixed = computeRisk("SGD", balances, withShort, ["BOXX"]);
    expect(mixed.metrics.netLeverage).toBeCloseTo((154_615 - 65_376 - 13_000) / 43_637, 2);
    expect(mixed.metrics.equityLeverage).toBeCloseTo((154_615 - 65_376 + 13_000) / 43_637, 2);
    // Betas still loading: beta-adjusted leverage is unknown rather than guessed.
    expect(mixed.metrics.betaLeverage).toBeNull();
    const betaState = computeRisk("SGD", balances, longs, ["BOXX"], { lly: 0.5, qqq: 1.2 });
    const lly = betaState.holdings.find(holding => holding.symbol === "LLY")!;
    expect([lly.beta, lly.betaSource]).toEqual([0.5, "measured"]);
    expect(betaState.holdings.find(holding => holding.symbol === "CSPX")?.betaSource).toBe("assumed");
    expect(betaState.holdings.find(holding => holding.symbol === "BOXX")?.beta).toBe(0);
    expect(betaState.assumedBetas).toEqual(["CSPX"]);
    expect(betaState.betaExposure).toBeCloseTo(32_898.46 * 1.3 * 0.5 + 20_442.31 * 1.3 - 10_000 * 1.3 * 1.2, 0);
    expect(betaState.metrics.betaLeverage).toBeCloseTo((betaState.betaExposure as number) / 43_637, 6);
  });

  it("estimates beta from shared trading days only", () => {
    const days = Array.from({ length: 80 }, (_, index) => `2026-01-${String(1 + (index % 28)).padStart(2, "0")}T00:00:00Z`.replace("-01-", `-${String(1 + Math.floor(index / 28)).padStart(2, "0")}-`));
    let market = 100, asset = 50;
    const bench = [], stock = [];
    for (const [index, t] of days.entries()) {
      const move = ((index * 7) % 11 - 5) / 500;
      market *= 1 + move; asset *= 1 + 1.5 * move;
      bench.push({ t, close: market }); stock.push({ t, close: asset });
    }
    expect(estimateBeta(stock, bench)?.beta).toBeCloseTo(1.5, 6);
    expect(estimateBeta(stock.slice(0, 30), bench)).toBeNull();
  });

  it("scales market shocks by beta", () => {
    const betaState = computeRisk("SGD", balances, [position("LLY", 10_000, { listing_id: "lly" })], [], { lly: 0.5 });
    expect(stressTests(betaState, DEFAULT_LIMITS).find(row => row.id === "eq-0.1")?.navImpact).toBeCloseTo(-0.1 * 0.5 * 13_000, 6);
  });

  it("shocks equities but holds cash equivalents flat", () => {
    const rows = stressTests(state, DEFAULT_LIMITS);
    const ten = rows.find(row => row.id === "eq-0.1")!;
    expect(ten.navImpact).toBeCloseTo(-0.1 * (154_615 - 65_376), 0);
    expect(rows.find(row => row.id === "gap")?.label).toBe("LLY gaps −30%");
    expect(rows.find(row => row.id === "eq-0.35")?.result).toBe("call");
  });

  it("splits unrealized from realized PnL, preferring the broker's account-wide unrealized figure", () => {
    const withPnl = positions.map((row, index) => ({ ...row, unrealized_pnl: String(100 * (index + 1)), realized_pnl_ytd: index === 1 ? "-50" : null }));
    const summed = computeRisk("SGD", balances, withPnl, ["BOXX"], {});
    expect(summed.unrealizedSource).toBe("positions");
    expect(summed.unrealized).toBeCloseTo(1_500 * 1.3, 6);
    expect(summed.realized).toBeCloseTo(-50 * 1.3, 6);
    expect(summed.holdings.find(holding => holding.symbol === "LLY")?.realized).toBeCloseTo(-65, 6);
    const broker = computeRisk("SGD", [...balances, balance("UnrealizedPnL", 777)], withPnl, ["BOXX"], {});
    expect(broker.unrealizedSource).toBe("broker");
    expect(broker.unrealized).toBe(777);
    // Gateway: the base-currency ledger row covers SGD positions only; the "BASE" ledger tag is account-wide.
    const ledger = computeRisk("SGD", [...balances, balance("$LEDGER-UnrealizedPnL", 5, "SGD"), balance("$LEDGER-UnrealizedPnL", 888, "BASE")], withPnl, ["BOXX"], {});
    expect(ledger.unrealized).toBe(888);
    expect(computeRisk("SGD", balances, positions, ["BOXX"], {}).realized).toBeNull();
    // An unconvertible position makes the position sums unavailable rather than partial.
    const missing = computeRisk("SGD", balances.filter(row => row.metric !== "ExchangeRate"), withPnl, ["BOXX"], {});
    expect(missing.unrealized).toBeNull();
    expect(missing.realized).toBeNull();
  });

  it("explains every metric with terms that reproduce the displayed value", () => {
    for (const spec of LIMIT_SPECS) {
      const calc = explainMetric(state, spec.key);
      expect(calc.result).toBe(state.metrics[spec.key]);
      expect(calc.denominator.value).toBe(43_637);
      if (calc.result !== null && calc.numerator.value !== null) expect(calc.numerator.value / 43_637).toBeCloseTo(calc.result, 9);
      if (calc.numerator.terms.length) {
        const sum = calc.numerator.terms.reduce((total, term) => total + (term.value ?? 0), 0);
        expect(calc.numerator.transform === "abs" ? Math.abs(sum) : sum).toBeCloseTo(calc.numerator.value as number, 6);
      }
    }
    const gross = explainMetric(state, "grossLeverage");
    expect(gross.formula).toBe("Gross leverage = Gross position value ÷ Net liquidation value");
    expect(gross.numerator.terms.find(term => term.label === "LLY")?.native).toEqual({ amount: 32_898.46, currency: "USD", fx: 1.3 });
    expect(explainMetric(state, "betaLeverage").numerator.terms[0].beta).toMatchObject({ value: 1, source: "assumed" });
    expect(explainMetric(state, "borrowing").numerator.terms).toEqual([{ label: "USD cash debit", value: 124_040 * 1.3, native: { amount: 124_040, currency: "USD", fx: 1.3 } }]);
  });

  it("names why a metric is unavailable", () => {
    const missing = computeRisk("SGD", balances.filter(row => row.metric !== "ExchangeRate"), positions, ["BOXX"]);
    expect(explainMetric(missing, "equityLeverage").notes[0]).toMatch(/no USD → SGD rate/);
    expect(explainMetric(missing, "betaLeverage").notes).toContain("Betas are still being measured.");
  });
});

describe("concentration, allocation and currency exposure", () => {
  const state = computeRisk("SGD", [...balances, balance("AvailableFunds", 13_309)], positions, ["BOXX"], {});
  const sgd = { BOXX: 50_289.23 * 1.3, LLY: 32_898.46 * 1.3, CSPX: 20_442.31 * 1.3, CRWD: 11_573.08 * 1.3, GRID: 3_731.54 * 1.3 };
  const gross = Object.values(sgd).reduce((sum, value) => sum + value, 0);

  it("reads available funds", () => {
    expect(state.availableFunds).toBe(13_309);
  });

  it("measures gross-weight concentration over every priced position", () => {
    const stats = concentrationStats(state)!;
    expect(stats.count).toBe(5);
    expect(stats.largest?.symbol).toBe("BOXX");
    expect(stats.largest?.grossShare).toBeCloseTo(sgd.BOXX / gross, 6);
    expect(stats.largest?.navShare).toBeCloseTo(sgd.BOXX / 43_637, 6);
    expect(stats.top3).toBeCloseTo((sgd.BOXX + sgd.LLY + sgd.CSPX) / gross, 6);
    expect(stats.top5).toBeCloseTo(1, 9);
    const sumSquares = Object.values(sgd).reduce((sum, value) => sum + (value / gross) ** 2, 0);
    expect(stats.effectiveN).toBeCloseTo(1 / sumSquares, 6);
    expect(grossShare(state, state.holdings[1])).toBeCloseTo(sgd.LLY / gross, 6);
  });

  it("returns no weights when a position cannot be converted", () => {
    const missing = computeRisk("SGD", balances.filter(row => row.metric !== "ExchangeRate"), positions, ["BOXX"], {});
    expect(concentrationStats(missing)).toBeNull();
    expect(assetClassAllocation(missing)).toBeNull();
    expect(grossShare(missing, missing.holdings[0])).toBeNull();
  });

  it("splits asset classes with cash equivalents separate from the product type", () => {
    const typed = computeRisk("SGD", balances, positions.map(row => ({ ...row, product_type: row.trading_symbol === "LLY" || row.trading_symbol === "CRWD" ? "common" : "etf" })), ["BOXX"], {});
    const rows = assetClassAllocation(typed)!;
    expect(rows.map(row => row.label)).toEqual(["Cash equivalents", "Stocks", "ETFs"]);
    expect(rows[1].value).toBeCloseTo(sgd.LLY + sgd.CRWD, 6);
    expect(rows.reduce((sum, row) => sum + row.grossShare, 0)).toBeCloseTo(1, 9);
  });

  it("nets USD positions against the USD margin debit once", () => {
    const rows = currencyExposure(state);
    expect(rows.map(row => row.currency)).toEqual(["SGD", "USD"]);
    const usd = rows[1];
    expect(usd.positions).toBeCloseTo(gross, 6);
    expect(usd.cash).toBeCloseTo(-124_040 * 1.3, 6);
    expect(usd.net).toBeCloseTo(gross - 124_040 * 1.3, 6);
    expect(rows[0]).toMatchObject({ positions: 0, cash: 4_620, net: 4_620 });
  });
});

describe("extended stress scenarios", () => {
  const state = computeRisk("SGD", balances, positions, ["BOXX"], {});
  const rows = stressTests(state, DEFAULT_LIMITS);
  const usdPositions = 154_615, usdCash = -124_040 * 1.3, boxx = 50_289.23 * 1.3, equities = usdPositions - boxx;

  it("reprices cash equivalents in their own scenario only", () => {
    expect(rows.find(row => row.id === "cash-like")?.navImpact).toBeCloseTo(-0.02 * boxx, 6);
    expect(rows.find(row => row.id === "eq-0.1")?.navImpact).toBeCloseTo(-0.1 * equities, 0);
  });

  it("moves USD positions and the USD debit with the dollar", () => {
    const usd = rows.find(row => row.id === "usd")!;
    expect(usd.label).toBe("USD −5% vs SGD");
    expect(usd.navImpact).toBeCloseTo(-0.05 * (usdPositions + usdCash), 0);
    expect(usd.navImpact).toBeGreaterThan(0); // the debit is larger than the USD assets
  });

  it("compounds price and FX moves in the combined scenario", () => {
    const combined = rows.find(row => row.id === "combined")!;
    const expected = equities * (0.9 * 0.97 - 1) + boxx * (0.99 * 0.97 - 1) + usdCash * -0.03;
    expect(combined.navImpact).toBeCloseTo(expected, 0);
  });

  it("skips the USD shock for a USD-based account", () => {
    const usdBase = computeRisk("USD", [balance("NetLiquidation", 10_000, "USD")], [position("LLY", 5_000)], [], {});
    const ids = stressTests(usdBase, DEFAULT_LIMITS).map(row => row.id);
    expect(ids).not.toContain("usd");
    expect(ids).not.toContain("cash-like");
    expect(ids).toContain("combined");
  });

  it("shocks the largest sector once every position is labelled", () => {
    const tech = { sector: { id: "45", label: "Information Technology" } }, health = { sector: { id: "35", label: "Health Care" } };
    const labelled = positions.map(row => ({ ...row, attributes: row.trading_symbol === "LLY" ? health : tech })) as unknown as BrokerRow[];
    const sectorRows = stressTests(computeRisk("SGD", balances, labelled, ["BOXX"], {}), DEFAULT_LIMITS);
    const sector = sectorRows.find(row => row.id === "sector")!;
    // Tech (CSPX + CRWD + GRID, BOXX excluded) is larger than Health Care (LLY).
    expect(sector.label).toBe("Information Technology −15%");
    expect(sector.navImpact).toBeCloseTo(-0.15 * (20_442.31 + 11_573.08 + 3_731.54) * 1.3, 6);
    expect(rows.find(row => row.id === "sector")).toBeUndefined();
  });

  it("uses edited shock sizes", () => {
    const custom = stressTests(state, DEFAULT_LIMITS, { ...DEFAULT_STRESS, market: [0.15], usd: 0.1 });
    expect(custom.filter(row => row.id.startsWith("eq-")).map(row => row.label)).toEqual(["Market −15%"]);
    expect(custom.find(row => row.id === "usd")?.navImpact).toBeCloseTo(-0.1 * (usdPositions + usdCash), 0);
  });
});
