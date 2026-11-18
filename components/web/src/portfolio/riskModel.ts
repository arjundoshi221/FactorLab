/** Portfolio risk engine: pure functions over one broker snapshot (balances + positions).
 *
 * Everything is in the account's base currency. Values come from the broker's account-wide
 * figures (IBKR account values, segment "" and currency = base); positions are converted with
 * the Flex `fx_rate_to_base`, else the broker's `ExchangeRate` for that currency, else 1 for
 * base-currency positions. Anything that cannot be converted is reported, never guessed. */
import type { AttrValue, Attributed, BrokerRow } from "./types";

export type Direction = "max" | "min";
export type LimitKey = "grossLeverage" | "equityLeverage" | "netLeverage" | "betaLeverage" | "singleName" | "cashLike" | "initUtil" | "maintUtil" | "excessLiquidity" | "borrowing";
export type Status = "green" | "amber" | "red" | "unknown";

export interface Limit { target: number; hard: number }
export type Limits = Record<LimitKey, Limit>;

export interface LimitSpec { key: LimitKey; label: string; direction: Direction; unit: "x" | "pct"; help: string }

export const LIMIT_SPECS: LimitSpec[] = [
  { key: "grossLeverage", label: "Gross leverage", direction: "max", unit: "x", help: "Gross position value ÷ net liquidation" },
  { key: "equityLeverage", label: "Equity leverage", direction: "max", unit: "x", help: "|Positions| excluding cash-equivalent ETFs ÷ net liquidation" },
  { key: "netLeverage", label: "Net leverage", direction: "max", unit: "x", help: "Long minus short positions, excluding cash equivalents, ÷ net liquidation" },
  { key: "betaLeverage", label: "Beta-adjusted leverage", direction: "max", unit: "x", help: "Σ exposure × 1-year beta to SPY, excluding cash equivalents, ÷ net liquidation" },
  { key: "singleName", label: "Largest single name", direction: "max", unit: "pct", help: "Largest non-cash-equivalent position ÷ net liquidation" },
  { key: "cashLike", label: "Cash-equivalent ETFs", direction: "max", unit: "x", help: "Box-spread / T-bill ETFs ÷ net liquidation" },
  { key: "initUtil", label: "Initial margin use", direction: "max", unit: "pct", help: "Initial margin requirement ÷ net liquidation" },
  { key: "maintUtil", label: "Maintenance margin use", direction: "max", unit: "pct", help: "Maintenance margin requirement ÷ net liquidation" },
  { key: "excessLiquidity", label: "Excess liquidity", direction: "min", unit: "pct", help: "Excess liquidity ÷ net liquidation (a floor)" },
  { key: "borrowing", label: "Borrowing", direction: "max", unit: "x", help: "Negative cash balances ÷ net liquidation" },
];

/** Proposed governance limits, not statistically calibrated thresholds; editable per account. */
export const DEFAULT_LIMITS: Limits = {
  grossLeverage: { target: 2.5, hard: 3 },
  equityLeverage: { target: 2, hard: 2.5 },
  netLeverage: { target: 2, hard: 2.5 },
  betaLeverage: { target: 2, hard: 2.5 },
  singleName: { target: 0.3, hard: 0.4 },
  cashLike: { target: 1, hard: 1.5 },
  initUtil: { target: 0.6, hard: 0.7 },
  maintUtil: { target: 0.5, hard: 0.6 },
  excessLiquidity: { target: 0.4, hard: 0.3 },
  borrowing: { target: 0.75, hard: 1.5 },
};

/** Symbols treated as cash equivalents (box-spread and T-bill ETFs) by default. */
export const DEFAULT_CASH_LIKE = ["BOXX", "SGOV", "BIL", "SHV", "USFR", "TFLO"];

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Betas: "measured" from price history, "assumed" (1.0) when no history, "cash" (0) for cash equivalents. */
export type BetaSource = "measured" | "assumed" | "cash";
/** Measured betas keyed by listing ID. */
export type BetaMap = Record<string, number>;
export const ASSUMED_BETA = 1;

export interface Holding {
  symbol: string; listingId: string | null;
  /** Broker product type (common, etf, option, future, forex…); null when unreported. */
  productType?: string | null;
  beta: number | null; betaSource: BetaSource | null; currency: string; quantity: number | null; native: number | null;
  /** Native → base rate used for this position; null when no rate is known. */
  fx: number | null;
  value: number | null; cashLike: boolean; maint: number | null;
  /** Broker-reported unrealized PnL on the open position, in base currency. */
  unrealized: number | null;
  /** Broker-reported realized PnL year to date for this instrument, in base currency. */
  realized: number | null;
  /** Pivot labels from the API (`dims=sector`), when requested. */
  attributes?: Record<string, AttrValue>;
}
export interface CashLine { currency: string; native: number; fx: number | null; value: number | null }

/** Where the account's unrealized PnL comes from: the broker's account-wide figure, or the sum of open positions. */
export type PnlSource = "broker" | "positions";

export interface RiskState {
  base: string;
  nav: number | null; gross: number | null; initMargin: number | null; maintMargin: number | null; excess: number | null;
  /** Broker AvailableFunds: equity with loan value less initial margin (headroom for new positions). */
  availableFunds: number | null;
  totalCash: number | null; cash: CashLine[]; borrowing: number | null;
  holdings: Holding[]; positionsGross: number; equityExposure: number; equityNet: number; cashLikeValue: number;
  /** Σ value × beta over non-cash positions; null while betas are not yet known. */
  betaExposure: number | null;
  /** Non-cash symbols whose beta is assumed (no usable price history). */
  assumedBetas: string[];
  unconverted: string[];
  /** Broker gross position value minus the sum of converted |positions|; null when either is unknown. */
  unreconciled: number | null;
  /** Unrealized PnL in base currency; null when unreported or a position cannot be converted. */
  unrealized: number | null; unrealizedSource: PnlSource | null;
  /** Σ realized PnL year to date over the open positions, in base currency; null when unreported or unconvertible. */
  realized: number | null;
  metrics: Record<LimitKey, number | null>;
}

function balance(rows: BrokerRow[], metric: string, currency: string): number | null {
  const row = rows.find(item => item.metric === metric && !item.segment && item.currency === currency);
  return num(row?.value_num);
}

/** Native → base rate: the row's Flex `fx_rate_to_base`, else 1 in the base currency, else the broker's `ExchangeRate`; null when unknown. */
export function positionRate(base: string, balances: BrokerRow[], currency: string, row?: BrokerRow): number | null {
  const flex = num(row?.fx_rate_to_base);
  if (flex) return flex;
  if (currency === base) return 1;
  return balance(balances, "ExchangeRate", currency);
}

/** `betas` is null while betas are loading: beta-adjusted leverage is then unknown, not guessed. */
export function computeRisk(base: string, balances: BrokerRow[], positions: BrokerRow[], cashLikeSymbols: string[], betas: BetaMap | null = null): RiskState {
  const cashLikeSet = new Set(cashLikeSymbols.map(symbol => symbol.trim().toUpperCase()).filter(Boolean));
  const rate = (currency: string, row?: BrokerRow): number | null => positionRate(base, balances, currency, row);
  const nav = balance(balances, "NetLiquidation", base);
  const gross = balance(balances, "GrossPositionValue", base);
  const initMargin = balance(balances, "InitMarginReq", base);
  const maintMargin = balance(balances, "MaintMarginReq", base);
  const excess = balance(balances, "ExcessLiquidity", base);
  const availableFunds = balance(balances, "AvailableFunds", base);
  const totalCash = balance(balances, "TotalCashValue", base);

  const cash: CashLine[] = balances
    .filter(row => row.metric === "CashBalance" && !row.segment && row.currency && row.currency !== "BASE" && num(row.value_num) !== null && num(row.value_num) !== 0)
    .map(row => {
      const currency = String(row.currency);
      const native = num(row.value_num) as number;
      const fx = rate(currency);
      return { currency, native, fx, value: fx === null ? null : native * fx };
    })
    .sort((a, b) => (a.currency === base ? -1 : b.currency === base ? 1 : a.currency.localeCompare(b.currency)));
  const borrowing = cash.some(line => line.value === null && line.native < 0) ? null
    : cash.reduce((total, line) => total + (line.value !== null && line.value < 0 ? -line.value : 0), 0);

  const unconverted = new Set<string>();
  const holdings: Holding[] = positions.map(row => {
    const symbol = String(row.trading_symbol ?? "—");
    const currency = String(row.currency ?? "");
    const native = num(row.market_value);
    const fx = rate(currency, row);
    if (native !== null && fx === null) unconverted.add(currency || "unknown");
    const toBase = (value: number | null) => value === null || fx === null ? null : value * fx;
    const cashLike = cashLikeSet.has(symbol.toUpperCase());
    const listingId = row.listing_id ? String(row.listing_id) : null;
    const measured = listingId && betas ? betas[listingId] : undefined;
    const betaSource: BetaSource | null = cashLike ? "cash" : betas === null ? null : measured !== undefined ? "measured" : "assumed";
    const beta = betaSource === null ? null : betaSource === "cash" ? 0 : measured ?? ASSUMED_BETA;
    return {
      symbol, listingId, productType: row.product_type ? String(row.product_type) : null, beta, betaSource, currency, quantity: num(row.position), native, fx, value: toBase(native),
      cashLike,
      maint: toBase(num(row.maintenance_margin_contribution)), unrealized: toBase(num(row.unrealized_pnl)),
      realized: toBase(num(row.realized_pnl_ytd)),
      attributes: (row as Attributed).attributes,
    };
  }).sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0));

  let positionsGross = 0, equityExposure = 0, equityNet = 0, cashLikeValue = 0, betaSum = 0;
  for (const holding of holdings) {
    if (holding.value === null) continue;
    positionsGross += Math.abs(holding.value);
    if (holding.cashLike) cashLikeValue += holding.value;
    else { equityExposure += Math.abs(holding.value); equityNet += holding.value; betaSum += holding.value * (holding.beta ?? 0); }
  }
  const betaExposure = betas === null ? null : betaSum;
  const assumedBetas = holdings.filter(holding => holding.betaSource === "assumed").map(holding => holding.symbol);
  const complete = unconverted.size === 0;
  const unreconciled = gross !== null && complete && holdings.length ? gross - positionsGross : null;
  const ratio = (value: number | null) => value === null || nav === null || nav <= 0 ? null : value / nav;
  // Sum of a per-position PnL field: null when no position reports it, or when a reported value cannot be converted.
  const sumPnl = (pick: (holding: Holding) => number | null) => {
    let total = 0, any = false;
    for (const holding of holdings) {
      const value = pick(holding);
      if (value !== null) { total += value; any = true; }
    }
    return any && complete ? total : null;
  };
  // Same reading as the overview: the Gateway reports account-wide unrealized PnL only as a "BASE" ledger tag
  // (already in the base currency); the ledger row labelled with the base currency covers only that currency.
  const brokerUnrealized = balance(balances, "UnrealizedPnL", base) ?? balance(balances, "$LEDGER-UnrealizedPnL", "BASE");
  const positionsUnrealized = sumPnl(holding => holding.unrealized);
  const unrealized = brokerUnrealized ?? positionsUnrealized;
  const unrealizedSource: PnlSource | null = brokerUnrealized !== null ? "broker" : positionsUnrealized !== null ? "positions" : null;
  const realized = sumPnl(holding => holding.realized);
  const largest = holdings.filter(holding => !holding.cashLike && holding.value !== null).reduce((max, holding) => Math.max(max, Math.abs(holding.value as number)), 0);

  return {
    base, nav, gross, initMargin, maintMargin, excess, availableFunds, totalCash, cash, borrowing,
    holdings, positionsGross, equityExposure, equityNet, cashLikeValue, betaExposure, assumedBetas, unconverted: [...unconverted], unreconciled,
    unrealized, unrealizedSource, realized,
    metrics: {
      grossLeverage: ratio(gross ?? (complete && holdings.length ? positionsGross : null)),
      equityLeverage: complete ? ratio(equityExposure) : null,
      netLeverage: complete ? ratio(equityNet) : null,
      betaLeverage: complete ? ratio(betaExposure) : null,
      singleName: complete ? ratio(largest) : null,
      cashLike: complete ? ratio(Math.abs(cashLikeValue)) : null,
      initUtil: ratio(initMargin),
      maintUtil: ratio(maintMargin),
      excessLiquidity: ratio(excess),
      borrowing: ratio(borrowing),
    },
  };
}

export interface Bar { t: string; close: number }

/** OLS beta of daily simple returns against a benchmark, over consecutive dates both series share.
 * Null below `minPoints` returns or when the benchmark does not move. */
export function estimateBeta(asset: Bar[], benchmark: Bar[], minPoints = 60): { beta: number; points: number } | null {
  const day = (t: string) => t.slice(0, 10);
  const bench = new Map(benchmark.filter(bar => bar.close > 0).map(bar => [day(bar.t), bar.close]));
  const shared = asset.filter(bar => bar.close > 0 && bench.has(day(bar.t))).sort((a, b) => a.t.localeCompare(b.t));
  const xs: number[] = [], ys: number[] = [];
  for (let index = 1; index < shared.length; index += 1) {
    const previous = shared[index - 1], current = shared[index];
    ys.push(current.close / previous.close - 1);
    xs.push((bench.get(day(current.t)) as number) / (bench.get(day(previous.t)) as number) - 1);
  }
  if (xs.length < minPoints) return null;
  const mean = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
  const mx = mean(xs), my = mean(ys);
  let cov = 0, variance = 0;
  for (let index = 0; index < xs.length; index += 1) {
    cov += (xs[index] - mx) * (ys[index] - my);
    variance += (xs[index] - mx) ** 2;
  }
  return variance > 0 ? { beta: cov / variance, points: xs.length } : null;
}

export function statusOf(value: number | null, limit: Limit, direction: Direction): Status {
  if (value === null) return "unknown";
  if (direction === "max") return value <= limit.target ? "green" : value <= limit.hard ? "amber" : "red";
  return value >= limit.target ? "green" : value >= limit.hard ? "amber" : "red";
}

export interface Verdict { status: Status; reasons: string[] }

/** Overall status. Stale, incomplete or unreconciled data can never read as green. */
export function overallStatus(state: RiskState, limits: Limits, stale: boolean, toleranceShare = 0.01): Verdict {
  const reasons: string[] = [];
  const statuses = LIMIT_SPECS.map(spec => ({ spec, status: statusOf(state.metrics[spec.key], limits[spec.key], spec.direction) }));
  const red = statuses.filter(item => item.status === "red");
  const amber = statuses.filter(item => item.status === "amber");
  red.forEach(item => reasons.push(`${item.spec.label} breaches its hard limit`));
  amber.forEach(item => reasons.push(`${item.spec.label} is above its operating target`));
  const unknown = statuses.filter(item => item.status === "unknown");
  if (unknown.length) reasons.push(`${unknown.length} metric${unknown.length === 1 ? " is" : "s are"} unavailable`);
  if (stale) reasons.push("The broker snapshot is stale");
  if (state.unconverted.length) reasons.push(`No FX rate for ${state.unconverted.join(", ")}`);
  if (hasReconciliationGap(state, toleranceShare)) reasons.push("Positions do not reconcile to the broker's gross position value");
  if (red.length) return { status: "red", reasons };
  if (amber.length) return { status: "amber", reasons };
  if (reasons.length) return { status: "unknown", reasons };
  return { status: "green", reasons: ["All operating targets are met"] };
}

export function hasReconciliationGap(state: RiskState, toleranceShare = 0.01): boolean {
  if (state.unreconciled === null || state.gross === null) return false;
  return Math.abs(state.unreconciled) > Math.max(1, Math.abs(state.gross) * toleranceShare);
}

export interface Headroom { key: LimitKey; label: string; amount: number | null; basis: string }
export interface Capacity { constraints: Headroom[]; binding: Headroom | null; amount: number | null }

/** Additional non-cash-equivalent exposure (base currency) each operating target allows, assuming purchases
 * are financed by borrowing (NAV unchanged) and margin scales at the account's current average rate. */
export function deployableCapacity(state: RiskState, limits: Limits): Capacity {
  const { nav, gross, initMargin, maintMargin, excess, borrowing } = state;
  const exposure = gross ?? state.positionsGross;
  const constraints: Headroom[] = [];
  const add = (key: LimitKey, amount: number | null, basis: string) =>
    constraints.push({ key, label: LIMIT_SPECS.find(spec => spec.key === key)!.label, amount, basis });
  if (nav === null || nav <= 0) return { constraints, binding: null, amount: null };
  add("grossLeverage", limits.grossLeverage.target * nav - exposure, `${limits.grossLeverage.target}× NAV less current gross`);
  add("equityLeverage", state.unconverted.length ? null : limits.equityLeverage.target * nav - state.equityExposure, `${limits.equityLeverage.target}× NAV less current equity exposure`);
  const initRate = initMargin !== null && exposure > 0 ? initMargin / exposure : null;
  add("initUtil", initRate ? (limits.initUtil.target * nav - (initMargin as number)) / initRate : null, initRate ? `Initial margin at ${(initRate * 100).toFixed(1)}% of exposure` : "Initial margin not reported");
  const maintRate = maintMargin !== null && exposure > 0 ? maintMargin / exposure : null;
  add("maintUtil", maintRate ? (limits.maintUtil.target * nav - (maintMargin as number)) / maintRate : null, maintRate ? `Maintenance margin at ${(maintRate * 100).toFixed(1)}% of exposure` : "Maintenance margin not reported");
  add("excessLiquidity", maintRate && excess !== null ? (excess - limits.excessLiquidity.target * nav) / maintRate : null, "Excess liquidity falls by the added maintenance margin");
  add("borrowing", borrowing === null ? null : limits.borrowing.target * nav - borrowing, "New purchases are financed by borrowing");
  const known = constraints.filter(item => item.amount !== null);
  const binding = known.reduce<Headroom | null>((min, item) => (min === null || (item.amount as number) < (min.amount as number) ? item : min), null);
  return { constraints, binding, amount: binding ? Math.max(0, binding.amount as number) : null };
}

/* ---------- concentration & allocation ---------- */

export interface ConcentrationStats {
  /** Priced positions counted (cash equivalents included: weights are of gross securities). */
  count: number;
  largest: { symbol: string; grossShare: number; navShare: number | null } | null;
  top3: number; top5: number;
  /** 1 ÷ Σ w², with w = |value| ÷ Σ |value| over every priced position. */
  effectiveN: number;
}

/** Gross-weight concentration over all priced positions; null when a position cannot be converted or none is priced. */
export function concentrationStats(state: RiskState): ConcentrationStats | null {
  if (state.unconverted.length) return null;
  const values = state.holdings.filter(holding => holding.value !== null && holding.value !== 0)
    .map(holding => ({ symbol: holding.symbol, size: Math.abs(holding.value as number) }))
    .sort((a, b) => b.size - a.size);
  const totalSize = values.reduce((sum, item) => sum + item.size, 0);
  if (!values.length || totalSize <= 0) return null;
  const weights = values.map(item => item.size / totalSize);
  const top = (n: number) => weights.slice(0, n).reduce((sum, weight) => sum + weight, 0);
  const nav = state.nav !== null && state.nav > 0 ? state.nav : null;
  return {
    count: values.length,
    largest: { symbol: values[0].symbol, grossShare: weights[0], navShare: nav === null ? null : values[0].size / nav },
    top3: top(3), top5: top(5),
    effectiveN: 1 / weights.reduce((sum, weight) => sum + weight * weight, 0),
  };
}

/** Share of gross securities value (Σ |value| of priced positions); null when unknown. */
export function grossShare(state: RiskState, holding: Holding): number | null {
  if (holding.value === null || state.unconverted.length || state.positionsGross <= 0) return null;
  return Math.abs(holding.value) / state.positionsGross;
}

export interface AllocationRow { key: string; label: string; value: number; grossShare: number; navShare: number | null }

const ASSET_CLASS_LABEL: Record<string, string> = { common: "Stocks", etf: "ETFs", option: "Options", future: "Futures", forex: "FX", bond: "Bonds" };

/** Gross value by asset class: cash equivalents first, then the broker product type. Null when a position cannot be converted. */
export function assetClassAllocation(state: RiskState): AllocationRow[] | null {
  if (state.unconverted.length || state.positionsGross <= 0) return null;
  const buckets = new Map<string, { label: string; value: number }>();
  for (const holding of state.holdings) {
    if (holding.value === null || holding.value === 0) continue;
    const key = holding.cashLike ? "cash-like" : holding.productType ?? "unknown";
    const label = holding.cashLike ? "Cash equivalents" : ASSET_CLASS_LABEL[key] ?? (key === "unknown" ? "Unclassified" : key[0].toUpperCase() + key.slice(1));
    const bucket = buckets.get(key) ?? { label, value: 0 };
    bucket.value += Math.abs(holding.value);
    buckets.set(key, bucket);
  }
  const nav = state.nav !== null && state.nav > 0 ? state.nav : null;
  return [...buckets.entries()].map(([key, bucket]) => ({ key, label: bucket.label, value: bucket.value, grossShare: bucket.value / state.positionsGross, navShare: nav === null ? null : bucket.value / nav }))
    .sort((a, b) => b.value - a.value);
}

export interface CurrencyRow {
  currency: string;
  /** Σ signed position value in this currency, in base; null if any cannot be converted. */
  positions: number | null;
  /** Signed cash balance in this currency (a margin debit is negative), in base. */
  cash: number | null;
  /** positions + cash: the account's net exposure to this currency. Never subtracts the debit twice. */
  net: number | null;
  navShare: number | null;
}

/** Net exposure per currency: positions plus signed cash, both in base. Base currency first. */
export function currencyExposure(state: RiskState): CurrencyRow[] {
  const rows = new Map<string, { positions: number | null; cash: number | null }>();
  const row = (currency: string) => { const found = rows.get(currency) ?? { positions: 0, cash: 0 }; rows.set(currency, found); return found; };
  for (const holding of state.holdings) {
    if (holding.native === null) continue;
    const entry = row(holding.currency || "unknown");
    entry.positions = entry.positions === null || holding.value === null ? null : entry.positions + holding.value;
  }
  for (const line of state.cash) {
    const entry = row(line.currency);
    entry.cash = entry.cash === null || line.value === null ? null : entry.cash + line.value;
  }
  const nav = state.nav !== null && state.nav > 0 ? state.nav : null;
  return [...rows.entries()].map(([currency, entry]) => {
    const net = entry.positions === null || entry.cash === null ? null : entry.positions + entry.cash;
    return { currency, positions: entry.positions, cash: entry.cash, net, navShare: net === null || nav === null ? null : net / nav };
  }).sort((a, b) => (a.currency === state.base ? -1 : b.currency === state.base ? 1 : Math.abs(b.net ?? 0) - Math.abs(a.net ?? 0)));
}

/* ---------- stress tests ---------- */

/** Shock sizes as positive fractions (0.05 = a 5% fall). Editable per account. */
export interface StressConfig {
  market: number[];
  /** Largest non-cash position gaps down. */
  gap: number;
  /** The largest sector (by gross non-cash exposure) falls; needs sector labels on the positions. */
  sector: number;
  /** Cash-equivalent ETFs (BOXX etc.) reprice down. */
  cashLike: number;
  /** USD falls against the base currency; applies to USD positions and USD cash (a USD debit shrinks). */
  usd: number;
  combined: { market: number; cashLike: number; usd: number };
}

export const DEFAULT_STRESS: StressConfig = { market: [0.05, 0.1, 0.2, 0.35], gap: 0.3, sector: 0.15, cashLike: 0.02, usd: 0.05, combined: { market: 0.1, cashLike: 0.01, usd: 0.03 } };

export interface StressRow {
  id: string; label: string; navImpact: number; navImpactPct: number; navAfter: number;
  grossLeverageAfter: number | null; excessAfter: number | null; excessRatioAfter: number | null;
  result: "cushion" | "pressure" | "breach" | "call"; firstBreach: string | null;
}

interface Scenario { id: string; label: string; price: (holding: Holding) => number; fx: (currency: string) => number }

const pctLabel = (size: number) => `${+(size * 100).toFixed(2)}%`;

/** The largest sector by gross non-cash exposure, when every non-cash position carries a sector label. */
function largestSector(holdings: Holding[]): { id: string; label: string } | null {
  const equity = holdings.filter(holding => !holding.cashLike && holding.value !== null);
  if (!equity.length || equity.some(holding => !holding.attributes?.sector)) return null;
  const sizes = new Map<string, { label: string; size: number }>();
  for (const holding of equity) {
    const sector = holding.attributes!.sector;
    if (sector.id === null) continue;
    const entry = sizes.get(sector.id) ?? { label: sector.label, size: 0 };
    entry.size += Math.abs(holding.value as number);
    sizes.set(sector.id, entry);
  }
  const best = [...sizes.entries()].sort((a, b) => b[1].size - a[1].size)[0];
  return best ? { id: best[0], label: best[1].label } : null;
}

/** Instantaneous price and FX shocks. Each position moves by (1 + price move) × (1 + FX move) in base;
 * non-base cash moves by its FX move. Maintenance margin scales with each position's value (its reported
 * contribution, else the account average rate). Approximate by construction: margin rates do not change. */
export function stressTests(state: RiskState, limits: Limits, config: StressConfig = DEFAULT_STRESS): StressRow[] {
  const { nav, excess, base } = state;
  if (nav === null || nav <= 0) return [];
  const exposure = state.gross ?? state.positionsGross;
  const maintRate = state.maintMargin !== null && exposure > 0 ? state.maintMargin / exposure : 0;
  const priced = state.holdings.filter(holding => holding.value !== null);
  const largest = priced.find(holding => !holding.cashLike);
  const sector = largestSector(state.holdings);
  const hasUsd = base !== "USD" && (priced.some(holding => holding.currency === "USD") || state.cash.some(line => line.currency === "USD"));
  const flat = () => 0;
  const usdMove = (size: number) => (currency: string) => (currency === "USD" ? -size : 0);
  const market = (size: number) => (holding: Holding) => (holding.cashLike ? 0 : Math.max(-1, -size * (holding.beta ?? ASSUMED_BETA)));
  const scenarios: Scenario[] = [
    // Market moves scale by each position's beta (1.0 until betas are measured or when none is available).
    ...config.market.map(size => ({ id: `eq-${size}`, label: `Market −${pctLabel(size)}`, price: market(size), fx: flat })),
    ...(largest ? [{ id: "gap", label: `${largest.symbol} gaps −${pctLabel(config.gap)}`, price: (holding: Holding) => (holding === largest ? -config.gap : 0), fx: flat }] : []),
    ...(sector ? [{ id: "sector", label: `${sector.label} −${pctLabel(config.sector)}`, price: (holding: Holding) => (!holding.cashLike && holding.attributes?.sector?.id === sector.id ? -config.sector : 0), fx: flat }] : []),
    ...(priced.some(holding => holding.cashLike) ? [{ id: "cash-like", label: `Cash equivalents −${pctLabel(config.cashLike)}`, price: (holding: Holding) => (holding.cashLike ? -config.cashLike : 0), fx: flat }] : []),
    ...(hasUsd ? [{ id: "usd", label: `USD −${pctLabel(config.usd)} vs ${base}`, price: flat, fx: usdMove(config.usd) }] : []),
    {
      id: "combined", label: `Combined: market −${pctLabel(config.combined.market)}, cash equivalents −${pctLabel(config.combined.cashLike)}${hasUsd ? `, USD −${pctLabel(config.combined.usd)}` : ""}`,
      price: (holding: Holding) => (holding.cashLike ? -config.combined.cashLike : market(config.combined.market)(holding)),
      fx: hasUsd ? usdMove(config.combined.usd) : flat,
    },
  ];
  return scenarios.map(({ id, label, price, fx }) => {
    let navImpact = 0, grossChange = 0, maintRelief = 0;
    for (const holding of priced) {
      const move = (1 + price(holding)) * (1 + (holding.currency === base ? 0 : fx(holding.currency))) - 1;
      if (move === 0) continue;
      const value = holding.value as number;
      navImpact += value * move;
      grossChange += Math.abs(value * (1 + move)) - Math.abs(value);
      const maint = holding.maint !== null ? Math.abs(holding.maint) : Math.abs(value) * maintRate;
      maintRelief += -move * maint;
    }
    for (const line of state.cash) if (line.value !== null && line.currency !== base) navImpact += line.value * fx(line.currency);
    const navAfter = nav + navImpact;
    const grossLeverageAfter = state.gross === null && !state.holdings.length ? null : navAfter > 0 ? (exposure + grossChange) / navAfter : null;
    const excessAfter = excess === null ? null : excess + navImpact + maintRelief;
    const excessRatioAfter = excessAfter === null || navAfter <= 0 ? null : excessAfter / navAfter;
    const breaches: string[] = [];
    if (grossLeverageAfter !== null && grossLeverageAfter > limits.grossLeverage.hard) breaches.push("Gross leverage hard limit");
    if (excessRatioAfter !== null && excessRatioAfter < limits.excessLiquidity.hard) breaches.push("Excess liquidity floor");
    const result: StressRow["result"] = (excessAfter !== null && excessAfter < 0) || navAfter <= 0 ? "call"
      : breaches.length ? "breach"
      : excessRatioAfter !== null && excessRatioAfter < limits.excessLiquidity.target ? "pressure" : "cushion";
    return {
      id, label, navImpact, navImpactPct: navImpact / nav, navAfter, grossLeverageAfter, excessAfter, excessRatioAfter, result,
      firstBreach: result === "call" ? "Margin deficiency" : breaches[0] ?? null,
    };
  });
}

/** Cash needed today (no trades) to bring gross leverage back to a multiple, holding exposure flat. */
export function contributionToReach(state: RiskState, multiple: number): number | null {
  const exposure = state.gross ?? null;
  if (exposure === null || state.nav === null) return null;
  return Math.max(0, exposure / multiple - state.nav);
}

/** One input to a metric's numerator, in base currency. `native`/`fx` show a currency conversion and
 * `beta` a beta weighting, so the UI can print the exact arithmetic behind `value`. */
export interface CalcTerm {
  label: string; value: number | null;
  native?: { amount: number; currency: string; fx: number | null };
  beta?: { value: number; source: BetaSource; exposure: number | null };
  note?: string;
}
export interface Calculation {
  key: LimitKey; label: string; formula: string;
  numerator: { label: string; value: number | null; source: string; terms: CalcTerm[]; transform?: "abs" };
  denominator: { label: string; value: number | null; source: string };
  result: number | null;
  notes: string[];
}

const NAV_SOURCE = "Broker NetLiquidation (account-wide, base currency)";

/** The exact calculation behind one metric, from the same state the board displays: every numerator term,
 * the denominator and the result. Terms sum to the numerator (before `transform`). */
export function explainMetric(state: RiskState, key: LimitKey): Calculation {
  const spec = LIMIT_SPECS.find(item => item.key === key)!;
  const notes: string[] = [];
  const position = (holding: Holding, value: number | null, extra: Partial<CalcTerm> = {}): CalcTerm => ({
    label: holding.symbol, value,
    ...(holding.native !== null && holding.currency && holding.currency !== state.base ? { native: { amount: holding.native, currency: holding.currency, fx: holding.fx } } : {}),
    ...extra,
  });
  const abs = (value: number | null) => (value === null ? null : Math.abs(value));
  const equity = state.holdings.filter(holding => !holding.cashLike);
  const cashLike = state.holdings.filter(holding => holding.cashLike);
  const blocked = state.unconverted.length > 0;
  if (blocked && key !== "grossLeverage" && !["initUtil", "maintUtil", "excessLiquidity", "borrowing"].includes(key))
    notes.push(`Unavailable: no ${state.unconverted.join(", ")} → ${state.base} rate in this snapshot.`);
  if (state.nav === null) notes.push("Unavailable: the broker did not report net liquidation.");
  else if (state.nav <= 0) notes.push("Unavailable: net liquidation is zero or negative, so ratios to it are undefined.");

  let numerator: Calculation["numerator"];
  const denominatorLabel = "Net liquidation value";
  switch (key) {
    case "grossLeverage": {
      const terms = state.holdings.map(holding => position(holding, abs(holding.value)));
      if (state.gross !== null) {
        if (state.unreconciled !== null && state.unreconciled !== 0) terms.push({ label: "Unreconciled (broker gross − listed positions)", value: state.unreconciled });
        else if (state.unreconciled === null && state.holdings.length) notes.push("Listed positions cannot all be converted, so they are shown for reference and may not sum to the broker figure.");
        numerator = { label: "Gross position value", value: state.gross, source: "Broker GrossPositionValue (account-wide, base currency)", terms };
      } else {
        numerator = { label: "Gross position value", value: state.metrics.grossLeverage === null ? null : state.positionsGross, source: "Σ |position value| (broker gross not reported)", terms };
      }
      break;
    }
    case "equityLeverage":
      numerator = { label: "Equity exposure", value: blocked ? null : state.equityExposure, source: "Σ |position value|, excluding cash equivalents", terms: equity.map(holding => position(holding, abs(holding.value))) };
      break;
    case "netLeverage":
      numerator = { label: "Net equity exposure", value: blocked ? null : state.equityNet, source: "Σ signed position value (long +, short −), excluding cash equivalents", terms: equity.map(holding => position(holding, holding.value)) };
      break;
    case "betaLeverage":
      if (state.betaExposure === null) notes.push("Betas are still being measured.");
      numerator = {
        label: "Beta-weighted exposure", value: blocked ? null : state.betaExposure, source: "Σ position value × 1-year daily beta to SPY, excluding cash equivalents",
        terms: equity.map(holding => position(holding, holding.value === null || holding.beta === null ? null : holding.value * holding.beta,
          holding.beta === null || holding.betaSource === null ? {} : { beta: { value: holding.beta, source: holding.betaSource, exposure: holding.value } })),
      };
      break;
    case "singleName": {
      const largest = equity.filter(holding => holding.value !== null).reduce<Holding | null>((max, holding) => (max === null || Math.abs(holding.value as number) > Math.abs(max.value as number) ? holding : max), null);
      numerator = { label: "Largest single position", value: blocked ? null : largest ? Math.abs(largest.value as number) : 0, source: "Largest |position value|, excluding cash equivalents", terms: largest ? [position(largest, abs(largest.value))] : [] };
      break;
    }
    case "cashLike":
      numerator = { label: "Cash-equivalent ETFs", value: blocked ? null : Math.abs(state.cashLikeValue), source: `|Σ position value| of ${cashLike.length ? cashLike.map(holding => holding.symbol).join(", ") : "cash-equivalent symbols"}`, terms: cashLike.map(holding => position(holding, holding.value)), transform: "abs" };
      break;
    case "initUtil":
      numerator = { label: "Initial margin requirement", value: state.initMargin, source: "Broker InitMarginReq (account-wide, base currency)", terms: [] };
      break;
    case "maintUtil":
      numerator = { label: "Maintenance margin requirement", value: state.maintMargin, source: "Broker MaintMarginReq (account-wide, base currency)", terms: [] };
      break;
    case "excessLiquidity":
      numerator = { label: "Excess liquidity", value: state.excess, source: "Broker ExcessLiquidity (equity with loan value − maintenance margin)", terms: [] };
      break;
    case "borrowing": {
      const debits = state.cash.filter(line => line.native < 0);
      if (state.borrowing === null) notes.push("A debit cash balance has no FX rate to the base currency.");
      numerator = {
        label: "Borrowing", value: state.borrowing, source: "Σ |negative cash balance| per currency, converted to base",
        terms: debits.map(line => ({ label: `${line.currency} cash debit`, value: line.value === null ? null : -line.value,
          ...(line.currency !== state.base ? { native: { amount: -line.native, currency: line.currency, fx: line.fx } } : {}) })),
      };
      break;
    }
  }
  return {
    key, label: spec.label, formula: `${spec.label} = ${numerator.label} ÷ ${denominatorLabel}`,
    numerator, denominator: { label: denominatorLabel, value: state.nav, source: NAV_SOURCE },
    result: state.metrics[key], notes,
  };
}
