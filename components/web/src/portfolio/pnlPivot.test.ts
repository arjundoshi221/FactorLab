import { describe, expect, it } from "vitest";

import { exposureBy } from "./exposurePivot";
import { aggregatePnl, attributeLevel, pnlSections, tagLevel, TAG_LEVELS } from "./pnlPivot";
import type { PnlDetail, PnlGroup } from "./types";

const tech = { id: "45", label: "Information Technology" };
const energy = { id: "10", label: "Energy" };
const order = [energy, tech, { id: null, label: "Unclassified" }];

const detail = (symbol: string, sector: typeof tech, values: Partial<PnlDetail> = {}): PnlDetail => ({
  broker_code: "ibkr", account_id: "U1", account_mode: "live", simulated: false, trading_symbol: symbol, currency: "USD",
  quantity: "10", orders: ["1"], realized: "0", unrealized: "0", commissions: "1", total: "-1", issues: [],
  price_time: null, history_start: null, theme_id: "t1", strategy_id: null, trade_name_id: null,
  attributes: { sector }, ...values,
});

describe("aggregatePnl", () => {
  it("matches the API's group rules: unvalued holdings are excluded but their fees count", () => {
    const rows = [
      detail("AAPL", tech, { realized: "100.5", unrealized: "20", commissions: "1.25" }),
      detail("OLD", tech, { realized: null, unrealized: null, commissions: "0.75", issues: ["incomplete_history"] }),
    ];
    const totals = aggregatePnl(rows, false);
    expect(totals).toMatchObject({ realized: "100.5", unrealized: "20", commissions: "2", total: "118.5" });
    expect(totals.excluded?.map(row => row.trading_symbol)).toEqual(["OLD"]);
    expect(totals.issues).toEqual(["incomplete_history"]);
  });

  it("makes fees and total unavailable when a fill cost is unknown", () => {
    const totals = aggregatePnl([detail("AAPL", tech, { realized: "5", unrealized: "0", commissions: null, issues: ["missing_commission"] })], false);
    expect(totals.commissions).toBeNull();
    expect(totals.total).toBeNull();
    expect(totals.realized).toBe("5");
  });

  it("YTD totals are net realized and ignore a missing unrealized", () => {
    expect(aggregatePnl([detail("AAPL", tech, { realized: "10", unrealized: null, commissions: "2" })], true).total).toBe("8");
  });
});

describe("pnlSections", () => {
  const groups: PnlGroup[] = [
    { id: "t1", name: "AI", account_mode: "live", simulated: false, currency: "USD", realized: "3", unrealized: "0", commissions: "2", total: "1", issues: [],
      details: [detail("AAPL", tech, { realized: "1", total: "0" }), detail("XOM", energy, { realized: "2", total: "1" })] },
    { id: "t2", name: "Value", account_mode: "live", simulated: false, currency: "USD", realized: "4", unrealized: "0", commissions: "1", total: "3", issues: [],
      details: [detail("MSFT", tech, { realized: "4", total: "3", theme_id: "t2", strategy_id: "s1" })] },
    { id: "t2", name: "Value", account_mode: "live", simulated: false, currency: "EUR", realized: "9", unrealized: "0", commissions: "1", total: "8", issues: [],
      details: [detail("SAP", tech, { realized: "9", total: "8", theme_id: "t2", currency: "EUR" })] },
  ];
  const names = new Map([["t1", "AI"], ["t2", "Value"], ["s1", "Momentum"]]);
  const theme = tagLevel<PnlDetail>(TAG_LEVELS[0], names);
  const strategy = tagLevel<PnlDetail>(TAG_LEVELS[1], names);
  const sector = attributeLevel<PnlDetail>({ id: "sector", label: "Sector", buckets: order });

  it("keeps currencies apart and uses the API's own group totals at the first level", () => {
    const sections = pnlSections(groups, [theme, sector], "theme", false);
    expect(sections.map(section => [section.currency, section.totals.realized, section.nodes.map(node => [node.value.label, node.totals.total])])).toEqual([
      ["EUR", "9", [["Value", "8"]]],
      ["USD", "7", [["AI", "1"], ["Value", "3"]]],
    ]);
    const ai = sections[1].nodes[0];
    expect(ai.children.map(child => [child.value.label, child.totals.total, child.rows.map(row => row.trading_symbol)])).toEqual([
      ["Energy", "1", ["XOM"]], ["Information Technology", "0", ["AAPL"]],
    ]);
  });

  it("regroups every holding when an attribute comes first, three levels deep", () => {
    const [, usd] = pnlSections(groups, [sector, theme, strategy], "theme", false);
    expect(usd.nodes.map(node => [node.value.label, node.totals.realized])).toEqual([["Energy", "2"], ["Information Technology", "5"]]);
    const tech = usd.nodes[1];
    expect(tech.children.map(node => node.value.label)).toEqual(["AI", "Value"]);
    expect(tech.children[1].children.map(node => node.value.label)).toEqual(["Momentum"]);
    expect(tech.children[0].children.map(node => node.value.label)).toEqual(["Untagged"]);
  });

  it("lists a group without holdings and sums the section from the groups", () => {
    const feesOnly: PnlGroup = { id: "t3", name: "Old", account_mode: "live", simulated: false, currency: "USD", realized: "0", unrealized: "0", commissions: "5", total: "-5", issues: ["incomplete_history"], details: [] };
    const [, usd] = pnlSections([...groups, feesOnly], [sector], "theme", false);
    expect(usd.nodes.map(node => node.value.label)).toEqual(["Energy", "Information Technology", "Old (no holdings)"]);
    expect(usd.totals).toMatchObject({ realized: "7", commissions: "8", total: "-1", issues: ["incomplete_history"] });
  });
});

describe("exposureBy", () => {
  const balances = [{ metric: "ExchangeRate", segment: "", currency: "EUR", value_num: 2 }];
  const position = (symbol: string, currency: string, value: string, pnl: string, sector?: typeof tech) =>
    ({ trading_symbol: symbol, currency, market_value: value, unrealized_pnl: pnl, ...(sector ? { attributes: { sector } } : {}) }) as never;

  it("converts to base, shares gross and puts unlabelled rows in Unclassified", () => {
    const buckets = exposureBy([position("AAPL", "USD", "300", "100", tech), position("SAP", "EUR", "50", "0", tech), position("XOM", "USD", "-100", "-20", energy), position("ZZZ", "USD", "100", "0")], balances, "USD", "sector", order);
    expect(buckets.map(b => [b.value.label, b.gross, b.net, b.unrealized, b.share])).toEqual([
      ["Energy", 100, -100, -20, 100 / 600], ["Information Technology", 400, 400, 100, 400 / 600], ["Unclassified", 100, 100, 0, 100 / 600],
    ]);
  });

  it("an unconvertible position makes its bucket unavailable, not partial", () => {
    const [bucket] = exposureBy([position("AAPL", "USD", "300", "100", tech), position("NOK", "NOK", "50", "0", tech)], balances, "USD", "sector", order);
    expect(bucket).toMatchObject({ gross: null, net: null, unrealized: null, share: null, size: 300, unconverted: ["NOK"] });
  });
});
