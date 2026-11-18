/**
 * The Overview's realized/unrealized split. Unrealized is the broker's open mark; realized
 * year to date comes from the account's recorded FIFO PnL (`/pnl/themes?since=`), so it
 * includes instruments that were closed out this year. Values are converted to the account's
 * base currency at the broker's rates; a group that cannot be valued or converted makes the
 * figure partial (and is named), never silently zero.
 */
import { inCurrency } from "./fx";
import { sumDecimals } from "./pivot";
import type { PnlReport } from "./types";

export interface RealizedYtd {
  /** Realized PnL from closing fills since 1 January, before fees. */
  realized: string | null;
  /** Commissions on fills since 1 January. */
  fees: string | null;
  /** Realized − fees: the Themes page's "Net realized YTD". */
  net: string | null;
  /** Instruments left out because they cannot be valued or converted. */
  excluded: string[];
  /** True when some group or instrument is left out of the sums. */
  partial: boolean;
}

/** First New York trading date of the current year, as the API's `since`. */
export function ytdStart(now = new Date()): string {
  const year = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric" }).format(now);
  return `${year}-01-01`;
}

/** Sum the YTD report's groups in `currency`; null when there is nothing recorded to sum. */
export function realizedYtd(report: PnlReport | null, currency: string | null | undefined): RealizedYtd | null {
  if (!report || !currency || !report.items.length) return null;
  const groups = inCurrency(report.items, currency, report.fx_rates ?? []);
  const valued = groups.filter(group => group.realized != null && group.commissions != null);
  const excluded = [...new Set(groups.flatMap(group => [
    ...(group.excluded ?? []).map(row => row.trading_symbol),
    ...(group.realized == null || group.commissions == null ? group.details.map(row => row.trading_symbol) : []),
  ]))].sort();
  if (!valued.length) return { realized: null, fees: null, net: null, excluded, partial: true };
  const realized = sumDecimals(valued.map(group => group.realized));
  const fees = sumDecimals(valued.map(group => group.commissions));
  const net = realized === null || fees === null ? null : sumDecimals([realized, [fees, -1]]);
  return { realized, fees, net, excluded, partial: valued.length < groups.length || excluded.length > 0 };
}

/** Each side's share of the combined absolute PnL, for the split bar; null when either is unknown or both are 0. */
export function splitShares(unrealized: number | null, realized: number | null): { unrealized: number; realized: number } | null {
  if (unrealized === null || realized === null) return null;
  const total = Math.abs(unrealized) + Math.abs(realized);
  if (!total) return null;
  return { unrealized: Math.abs(unrealized) / total, realized: Math.abs(realized) / total };
}
