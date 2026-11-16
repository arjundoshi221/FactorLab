/**
 * Open positions split by their order tags, for the Risk page's exposure tree. Snapshot
 * positions carry no tags, so each position is divided across the tagged holdings
 * (`/pnl/*` details: net open quantity per account, instrument, currency and tag set) in
 * proportion to quantity; whatever the tagged quantities do not explain is Untagged.
 * Values are the base-currency figures of `computeRisk`; an unconvertible position makes
 * every total that includes it unavailable, never a partial sum.
 */
import type { Holding } from "./riskModel";
import type { Attributed, AttrValue, BrokerRow, OrderAssignment, PnlDetail } from "./types";

export interface ExposureSlice extends OrderAssignment, Attributed {
  key: string; symbol: string; currency: string;
  /** Fraction of the position this slice holds (1 when the whole position has one tag set). */
  share: number;
  value: number | null; unrealized: number | null; beta: number | null; cashLike: boolean; unconverted: boolean;
}

export interface ExposureTotals {
  /** Σ|value|; null when any slice cannot be converted. */
  gross: number | null;
  /** Σ value (long +, short −); null when any slice cannot be converted. */
  net: number | null;
  /** Σ value × beta, cash equivalents 0; null while betas load or a slice is unconverted. */
  beta: number | null;
  /** Σ unrealized over slices that report it; null when none do or a slice is unconverted. */
  unrealized: number | null;
  positions: number; unconverted: string[];
}

const UNTAGGED: OrderAssignment = { theme_id: null, strategy_id: null, trade_name_id: null };
const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const identity = (symbol: string, currency: string) => JSON.stringify([symbol.toUpperCase(), currency.toUpperCase()]);

export function splitPositions(holdings: Holding[], labelled: BrokerRow[], details: PnlDetail[], accountId: string): ExposureSlice[] {
  const attributes = new Map<string, Record<string, AttrValue> | undefined>();
  for (const row of labelled) attributes.set(identity(String(row.trading_symbol ?? ""), String(row.currency ?? "")), (row as Attributed).attributes);
  const tagged = new Map<string, PnlDetail[]>();
  for (const detail of details) {
    if (detail.account_id !== accountId || !num(detail.quantity)) continue;
    const key = identity(detail.trading_symbol, detail.currency);
    tagged.set(key, [...(tagged.get(key) ?? []), detail]);
  }
  return holdings.flatMap((holding, index) => {
    const key = identity(holding.symbol, holding.currency);
    const quantity = holding.quantity;
    // Only holdings on the same side as the position explain part of it.
    const parts = quantity ? (tagged.get(key) ?? []).map(detail => ({ tags: detail as OrderAssignment, share: (num(detail.quantity) ?? 0) / quantity })).filter(part => part.share > 0) : [];
    const explained = parts.reduce((total, part) => total + part.share, 0);
    const scaled = explained > 1 ? parts.map(part => ({ ...part, share: part.share / explained })) : parts;
    const rest = 1 - Math.min(1, explained);
    const slices = [...scaled, ...(rest > 1e-9 || scaled.length === 0 ? [{ tags: UNTAGGED, share: scaled.length ? rest : 1 }] : [])];
    const unconverted = holding.native !== null && holding.fx === null;
    return slices.map((slice, part) => ({
      key: `${key}/${index}/${part}`, symbol: holding.symbol, currency: holding.currency, share: slice.share,
      theme_id: slice.tags.theme_id, strategy_id: slice.tags.strategy_id, trade_name_id: slice.tags.trade_name_id,
      attributes: attributes.get(key),
      value: holding.value === null ? null : holding.value * slice.share,
      unrealized: holding.unrealized === null ? null : holding.unrealized * slice.share,
      beta: holding.beta, cashLike: holding.cashLike, unconverted,
    }));
  });
}

export function aggregateExposure(slices: ExposureSlice[]): ExposureTotals {
  const unconverted = [...new Set(slices.filter(slice => slice.unconverted).map(slice => slice.symbol))];
  const blocked = unconverted.length > 0;
  const reported = slices.filter(slice => slice.unrealized !== null);
  const betaKnown = slices.every(slice => slice.cashLike || slice.beta !== null);
  return {
    gross: blocked ? null : slices.reduce((total, slice) => total + Math.abs(slice.value ?? 0), 0),
    net: blocked ? null : slices.reduce((total, slice) => total + (slice.value ?? 0), 0),
    beta: blocked || !betaKnown ? null : slices.reduce((total, slice) => total + (slice.cashLike ? 0 : (slice.value ?? 0) * (slice.beta ?? 0)), 0),
    unrealized: blocked || reported.length === 0 ? null : reported.reduce((total, slice) => total + (slice.unrealized ?? 0), 0),
    positions: new Set(slices.map(slice => identity(slice.symbol, slice.currency))).size,
    unconverted,
  };
}
