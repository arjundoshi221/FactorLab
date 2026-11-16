/**
 * One display currency for the PnL report. The API sends each account's latest broker
 * `ExchangeRate` rows (1 `currency` = `rate` `base`); any two currencies quoted in the
 * same base convert through it. A holding whose currency has no rate is never guessed:
 * its values become unavailable and it is flagged `fx_unavailable`, so it is listed as
 * excluded like any other holding that cannot be valued.
 */
import { sumDecimals } from "./pivot";
import type { PnlDetail, PnlGroup } from "./types";

export interface FxRate { currency: string; base: string; rate: string; as_of: string | null }

export const FX_UNAVAILABLE = "fx_unavailable";
export const DISPLAY_CURRENCIES = ["USD", "SGD", "GBP", "EUR"];

/** Units of `to` per unit of `from`; null when no base quotes both. */
export function fxRate(from: string, to: string, rates: FxRate[]): number | null {
  if (from === to) return 1;
  const bases = new Map<string, Map<string, number>>();
  for (const row of rates) {
    const value = Number(row.rate);
    if (!Number.isFinite(value) || value <= 0) continue;
    const quotes = bases.get(row.base) ?? new Map<string, number>([[row.base, 1]]);
    quotes.set(row.currency, value);
    bases.set(row.base, quotes);
  }
  for (const quotes of bases.values()) {
    const source = quotes.get(from), target = quotes.get(to);
    if (source && target) return source / target;
  }
  return null;
}

/** A decimal string times a rate; the original text when the rate is 1. */
function scale(value: string | null | undefined, rate: number | null): string | null {
  if (value === null || value === undefined || rate === null) return null;
  if (rate === 1) return value;
  const number = Number(value);
  return Number.isFinite(number) ? (number * rate).toFixed(6) : null;
}

const MONEY = ["realized", "unrealized", "commissions", "total", "ibkr_realized", "ibkr_unrealized", "ibkr_total"] as const;

function convert<T extends PnlDetail | PnlGroup>(row: T, to: string, rates: FxRate[]): T {
  const rate = fxRate(row.currency, to, rates);
  const values = Object.fromEntries(MONEY.filter(field => field in row).map(field => [field, scale(row[field], rate)]));
  const issues = rate === null ? [...new Set([...row.issues, FX_UNAVAILABLE])].sort() : row.issues;
  return { ...row, ...values, currency: to, issues };
}

/**
 * Every group and holding in `to`, with groups of the same tag merged across currencies.
 * A merged group sums its members' converted API totals and lists their excluded
 * instruments; a member with no rate adds nothing and its holdings are listed as excluded.
 * Deeper pivot levels re-aggregate the converted holdings themselves.
 */
export function inCurrency(groups: PnlGroup[], to: string, rates: FxRate[]): PnlGroup[] {
  const merged = new Map<string, PnlGroup[]>();
  for (const group of groups) {
    const key = JSON.stringify([group.id, group.account_mode, group.simulated]);
    merged.set(key, [...(merged.get(key) ?? []), group]);
  }
  return [...merged.values()].map(members => {
    const converted = members.map(group => convert(group, to, rates));
    const usable = converted.filter(group => !group.issues.includes(FX_UNAVAILABLE));
    const lost = members.filter((_, index) => converted[index].issues.includes(FX_UNAVAILABLE));
    const sum = (field: (typeof MONEY)[number]) => {
      const present = usable.filter(group => field in group);
      return present.length ? sumDecimals(present.map(group => group[field])) : usable.length ? undefined : null;
    };
    const money = Object.fromEntries(MONEY.map(field => [field, sum(field)]).filter(([, value]) => value !== undefined));
    const unpriced = (group: PnlGroup) => group.details.map(row => ({ trading_symbol: row.trading_symbol, account_id: row.account_id, issues: [FX_UNAVAILABLE], attributes: row.attributes }));
    const excluded = [...usable.flatMap(group => group.excluded ?? []), ...lost.flatMap(unpriced)];
    return {
      ...converted[0], ...money,
      issues: [...new Set(converted.flatMap(group => group.issues))].sort(),
      excluded: excluded.length || usable.some(group => group.excluded) ? excluded : undefined,
      ibkr_excluded: usable.some(group => group.ibkr_excluded) || lost.length ? [...usable.flatMap(group => group.ibkr_excluded ?? []), ...lost.flatMap(unpriced)] : undefined,
      currency: to,
      details: members.flatMap(group => group.details.map(detail => convert(detail, to, rates))),
    };
  });
}
