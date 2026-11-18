import { expect, it } from "vitest";

import { realizedYtd, splitShares, ytdStart } from "./pnlSplit";
import type { PnlGroup, PnlReport } from "./types";

const group = (extra: Partial<PnlGroup>): PnlGroup => ({
  id: "t1", name: "AI", account_mode: "live", simulated: false, currency: "USD",
  realized: "100", unrealized: "0", commissions: "4", total: "96", issues: [], details: [], ...extra,
});
const report = (items: PnlGroup[], extra: Partial<PnlReport> = {}): PnlReport => ({ items, fx_rates: [{ currency: "GBP", base: "USD", rate: "1.25", as_of: null }], ...extra });

it("sums every group's realized YTD in the base currency, net of fees", () => {
  const value = realizedYtd(report([group({}), group({ id: "t2", currency: "GBP", realized: "40", commissions: "2" })]), "USD");
  expect(value).toEqual({ realized: "150", fees: "6.5", net: "143.5", excluded: [], partial: false });
});

it("marks the figure partial and names instruments that cannot be valued or converted", () => {
  const detail = { trading_symbol: "SAP", currency: "EUR", issues: [] } as unknown as PnlGroup["details"][number];
  const value = realizedYtd(report([
    group({ excluded: [{ trading_symbol: "OLD", account_id: "U1", issues: ["incomplete_history"] }] }),
    group({ id: "t2", currency: "EUR", details: [detail] }),
  ]), "USD");
  expect(value).toMatchObject({ realized: "100", fees: "4", net: "96", excluded: ["OLD", "SAP"], partial: true });
});

it("is unavailable, never zero, with no base currency or no recorded groups", () => {
  expect(realizedYtd(report([]), "USD")).toBeNull();
  expect(realizedYtd(report([group({})]), null)).toBeNull();
  expect(realizedYtd(report([group({ realized: null })]), "USD")).toMatchObject({ net: null, partial: true });
});

it("splits absolute PnL between unrealized and realized", () => {
  expect(splitShares(-300, 100)).toEqual({ unrealized: 0.75, realized: 0.25 });
  expect(splitShares(null, 100)).toBeNull();
  expect(splitShares(0, 0)).toBeNull();
});

it("starts YTD on 1 January in New York", () => {
  expect(ytdStart(new Date("2027-01-01T03:00:00Z"))).toBe("2026-01-01");
  expect(ytdStart(new Date("2027-01-01T06:00:00Z"))).toBe("2027-01-01");
});
