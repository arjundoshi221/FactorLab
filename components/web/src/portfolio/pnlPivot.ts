/**
 * Re-aggregate tagged PnL holdings under any pivot. The rules mirror the API's
 * `summarize` (portfolio_pnl.py), so pivot subtotals reconcile with the group totals:
 * an instrument that cannot be valued is left out of realized/unrealized and listed
 * in `excluded` while its fees still count; an unknown fill cost makes fees and total
 * unavailable; IBKR figures are net of commissions.
 */
import { attributeOf, pivotRows, sumDecimals, type PivotBucket } from "./pivot";
import type { AttrValue, PnlDetail, PnlGroup } from "./types";

const COST_UNKNOWN = ["missing_commission", "missing_commission_currency"];

export type PnlTotals = Pick<PnlGroup,
  "realized" | "unrealized" | "commissions" | "total" | "issues" | "excluded" |
  "ibkr_realized" | "ibkr_unrealized" | "ibkr_total" | "ibkr_excluded" | "ibkr_issues">;

const held = (row: PnlDetail) => row.orders.length > 0 || sumDecimals([row.quantity]) !== "0";
const valued = (realized: string | null | undefined, unrealized: string | null | undefined, ytd: boolean) =>
  realized != null && (ytd || unrealized != null);

export function aggregatePnl(details: PnlDetail[], ytd: boolean): PnlTotals {
  const ours = details.filter(row => valued(row.realized, row.unrealized, ytd));
  const broker = details.filter(row => valued(row.ibkr_realized, row.ibkr_unrealized, ytd));
  const realized = ours.length ? sumDecimals(ours.map(row => row.realized)) : null;
  const unrealized = ours.length ? sumDecimals(ours.map(row => row.unrealized ?? "0")) : null;
  const costsUnknown = details.some(row => row.issues.some(issue => COST_UNKNOWN.includes(issue)));
  const commissions = costsUnknown ? null : sumDecimals(details.flatMap(row => row.commissions == null ? [] : [row.commissions]));
  const ibkrRealized = broker.length ? sumDecimals(broker.map(row => row.ibkr_realized)) : null;
  const ibkrUnrealized = broker.length ? sumDecimals(broker.map(row => row.ibkr_unrealized ?? "0")) : null;
  const total = realized === null || unrealized === null || commissions === null ? null
    : sumDecimals([realized, ytd ? "0" : unrealized, [commissions, -1]]);
  const excluded = (rows: PnlDetail[], issues: (row: PnlDetail) => string[]) => rows
    .map(row => ({ trading_symbol: row.trading_symbol, account_id: row.account_id, issues: issues(row), attributes: row.attributes }))
    .sort((a, b) => a.trading_symbol.localeCompare(b.trading_symbol) || a.account_id.localeCompare(b.account_id));
  return {
    realized, unrealized, commissions, total,
    issues: [...new Set(details.flatMap(row => row.issues))].sort(),
    excluded: excluded(details.filter(row => !valued(row.realized, row.unrealized, ytd) && held(row)), row => row.issues),
    ibkr_realized: ibkrRealized, ibkr_unrealized: ibkrUnrealized,
    ibkr_total: ibkrRealized === null || ibkrUnrealized === null ? null : sumDecimals([ibkrRealized, ytd ? "0" : ibkrUnrealized]),
    ibkr_excluded: excluded(details.filter(row => !valued(row.ibkr_realized, row.ibkr_unrealized, ytd) && row.ibkr_total !== undefined && held(row)), row => row.ibkr_issues ?? []),
    ibkr_issues: [...new Set(details.flatMap(row => row.ibkr_issues ?? []))].sort(),
  };
}

/**
 * Attribute-first rows: one synthetic group per attribute bucket, account mode, simulation
 * and currency (currencies and modes never merge), holding every tagged group's holdings.
 */
export function groupsByAttribute(groups: PnlGroup[], dim: string, order: AttrValue[], ytd: boolean): PnlGroup[] {
  const result: PnlGroup[] = [];
  const split = new Map<string, PnlDetail[]>();
  for (const group of groups) for (const detail of group.details) {
    const key = JSON.stringify([group.account_mode, group.simulated, group.currency]);
    split.set(key, [...(split.get(key) ?? []), detail]);
  }
  for (const [key, details] of [...split.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const [accountMode, simulated, currency] = JSON.parse(key) as [string, boolean, string];
    for (const bucket of pivotRows(details, row => attributeOf(row, dim), order)) {
      result.push({ id: bucket.value.id, name: bucket.value.label, account_mode: accountMode, simulated, currency, details: bucket.rows, ...aggregatePnl(bucket.rows, ytd) });
    }
  }
  return result.sort((a, b) => order.findIndex(v => v.id === a.id) - order.findIndex(v => v.id === b.id)
    || a.currency.localeCompare(b.currency) || a.account_mode.localeCompare(b.account_mode));
}

export interface PnlSubgroup extends PivotBucket<PnlDetail> { totals: PnlTotals }

/** Split one row's holdings by the other axis, each with its own subtotals. */
export function subgroups(details: PnlDetail[], valueOf: (row: PnlDetail) => AttrValue, order: AttrValue[], ytd: boolean): PnlSubgroup[] {
  return pivotRows(details, valueOf, order).map(bucket => ({ ...bucket, totals: aggregatePnl(bucket.rows, ytd) }));
}
