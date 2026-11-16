/**
 * Re-aggregate tagged PnL holdings under any pivot. The rules mirror the API's
 * `summarize` (portfolio_pnl.py), so pivot subtotals reconcile with the group totals:
 * an instrument that cannot be valued is left out of realized/unrealized and listed
 * in `excluded` while its fees still count; an unknown fill cost makes fees and total
 * unavailable; IBKR figures are net of commissions.
 */
import { attributeOf, pivotTree, sumDecimals, type Level, type TreeNode } from "./pivot";
import type { AttrValue, Attributed, CatalogKind, Dimension, OrderAssignment, PnlDetail, PnlGroup } from "./types";

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

export type PnlNode = TreeNode<PnlDetail, PnlTotals>;

/** The three order tags as grouping levels; their ids are what `group=` carries. */
export const TAG_LEVELS = [
  { id: "theme", kind: "themes", field: "theme_id", label: "Theme" },
  { id: "strategy", kind: "strategies", field: "strategy_id", label: "Strategy" },
  { id: "trade_name", kind: "trade-names", field: "trade_name_id", label: "Trade name" },
] as const satisfies readonly { id: string; kind: CatalogKind; field: keyof OrderAssignment; label: string }[];

export const UNTAGGED: AttrValue = { id: null, label: "Untagged" };

/** A tag level; `names` maps entry id to name (null while the catalog loads). */
export function tagLevel<T extends OrderAssignment>(tag: (typeof TAG_LEVELS)[number], names: Map<string, string> | null): Level<T> {
  return {
    id: tag.id, label: tag.label,
    valueOf: row => {
      const id = row[tag.field];
      return id === null ? UNTAGGED : { id, label: names?.get(id) ?? (names ? "Archived entry" : "…") };
    },
  };
}

/** A pivot dimension the API labels rows with (`dims=`), e.g. Sector. */
export function attributeLevel<T extends Attributed>(dimension: Dimension): Level<T> {
  return { id: dimension.id, label: dimension.label, valueOf: row => attributeOf(row, dimension.id), order: dimension.buckets };
}

function totalsOf(group: PnlGroup): PnlTotals {
  const { realized, unrealized, commissions, total, issues, excluded, ibkr_realized, ibkr_unrealized, ibkr_total, ibkr_excluded, ibkr_issues } = group;
  return { realized, unrealized, commissions, total, issues, excluded, ibkr_realized, ibkr_unrealized, ibkr_total, ibkr_excluded, ibkr_issues };
}

/** One currency, account mode and simulation flag: values in different sections never add up. */
export interface PnlSection { key: string; currency: string; account_mode: string; simulated: boolean; totals: PnlTotals; nodes: PnlNode[] }

/**
 * The report as a tree per section. When the first level is the page's own tag (`groupLevel`),
 * its rows are the API's groups with the API's own totals; deeper levels re-aggregate holdings.
 * Otherwise every holding is regrouped, and a group with no holdings is listed on its own.
 */
export function pnlSections(groups: PnlGroup[], levels: Level<PnlDetail>[], groupLevel: string, ytd: boolean): PnlSection[] {
  const aggregate = (rows: PnlDetail[]) => aggregatePnl(rows, ytd);
  const split = new Map<string, PnlGroup[]>();
  for (const group of groups) {
    const key = JSON.stringify([group.currency, group.account_mode, group.simulated]);
    split.set(key, [...(split.get(key) ?? []), group]);
  }
  return [...split.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, members]) => {
    const [currency, account_mode, simulated] = JSON.parse(key) as [string, string, boolean];
    const details = members.flatMap(group => group.details);
    const empty = members.filter(group => group.details.length === 0);
    const own = (group: PnlGroup, prefix: string): PnlNode => {
      const nodeKey = `${key}${prefix}:${group.id ?? ""}`;
      return { key: nodeKey, value: { id: group.id, label: group.name }, depth: 0, rows: group.details, totals: totalsOf(group),
        children: prefix.startsWith(`/${groupLevel}`) ? pivotTree(group.details, levels.slice(1), aggregate, 1, nodeKey) : [] };
    };
    const nodes = levels[0]?.id === groupLevel ? members.map(group => own(group, `/${groupLevel}`))
      : [...pivotTree(details, levels, aggregate, 0, key), ...empty.map(group => ({ ...own(group, "/no-holdings"), value: { id: group.id, label: `${group.name} (no holdings)` } }))];
    // Fees-only groups carry totals that no holding explains, so then the section sums the groups.
    const totals = empty.length === 0 ? aggregate(details) : {
      ...aggregate(details),
      ...Object.fromEntries((["realized", "unrealized", "commissions", "total", "ibkr_realized", "ibkr_unrealized", "ibkr_total"] as const)
        .map(field => [field, sumDecimals(members.map(group => group[field]))])),
      issues: [...new Set(members.flatMap(group => group.issues))].sort(),
    };
    return { key, currency, account_mode, simulated, totals, nodes };
  });
}
