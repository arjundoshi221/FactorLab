/**
 * Open positions grouped by a pivot dimension, in the account's base currency. A position
 * that cannot be converted makes its bucket's value, share and unrealized unavailable
 * (never a partial or cross-currency sum); it still counts as a position in the bucket.
 */
import { attributeOf, pivotRows } from "./pivot";
import { positionRate } from "./riskModel";
import type { AttrValue, Attributed, BrokerRow } from "./types";

export interface ExposureHolding { row: BrokerRow; symbol: string; value: number | null; unrealized: number | null }
export interface ExposureBucket {
  key: string; value: AttrValue; holdings: ExposureHolding[];
  /** Σ|converted value|: the treemap area, also when some positions are unconverted. */
  size: number;
  /** Σ|value| and its share of the account's gross; null if any position is unconverted. */
  gross: number | null; share: number | null;
  /** Net Σ value; null if any position is unconverted. */
  net: number | null;
  unrealized: number | null;
  /** Unrealized ÷ |cost| (cost = value − unrealized); null when unknown. */
  ret: number | null;
  unconverted: string[];
}

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export function exposureBy(positions: BrokerRow[], balances: BrokerRow[], base: string, dim: string, order: AttrValue[]): ExposureBucket[] {
  const holdings = positions.map((row): ExposureHolding => {
    const fx = positionRate(base, balances, String(row.currency ?? ""), row);
    const toBase = (value: number | null) => value === null || fx === null ? null : value * fx;
    return { row, symbol: String(row.trading_symbol ?? "—"), value: toBase(num(row.market_value)), unrealized: toBase(num(row.unrealized_pnl)) };
  });
  const complete = holdings.every(holding => holding.value !== null || num(holding.row.market_value) === null);
  const accountGross = complete ? holdings.reduce((total, holding) => total + Math.abs(holding.value ?? 0), 0) : null;
  return pivotRows(holdings, holding => attributeOf(holding.row as Attributed, dim), order).map(bucket => {
    const unconverted = bucket.rows.filter(holding => holding.value === null && num(holding.row.market_value) !== null).map(holding => holding.symbol);
    const size = bucket.rows.reduce((total, holding) => total + Math.abs(holding.value ?? 0), 0);
    const gross = unconverted.length ? null : size;
    const net = unconverted.length ? null : bucket.rows.reduce((total, holding) => total + (holding.value ?? 0), 0);
    const reported = bucket.rows.filter(holding => holding.unrealized !== null);
    const unrealized = unconverted.length || reported.length === 0 ? null : reported.reduce((total, holding) => total + (holding.unrealized ?? 0), 0);
    const cost = reported.reduce((total, holding) => total + Math.abs((holding.value ?? 0) - (holding.unrealized ?? 0)), 0);
    return {
      key: bucket.key, value: bucket.value, holdings: bucket.rows, size, gross, net, unrealized, unconverted,
      share: gross === null || !accountGross ? null : gross / accountGross,
      ret: unrealized === null || !cost ? null : unrealized / cost,
    };
  });
}
