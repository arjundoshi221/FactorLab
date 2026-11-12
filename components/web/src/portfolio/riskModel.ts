/** Portfolio risk engine: pure functions over one broker snapshot (balances + positions).
 *
 * Everything is in the account's base currency. Values come from the broker's account-wide
 * figures (IBKR account values, segment "" and currency = base); positions are converted with
 * the Flex `fx_rate_to_base`, else the broker's `ExchangeRate` for that currency, else 1 for
 * base-currency positions. Anything that cannot be converted is reported, never guessed. */
import type { BrokerRow } from "./types";

export type Direction = "max" | "min";
export type LimitKey = "grossLeverage" | "equityLeverage" | "singleName" | "cashLike" | "initUtil" | "maintUtil" | "excessLiquidity" | "borrowing";
export type Status = "green" | "amber" | "red" | "unknown";

export interface Limit { target: number; hard: number }
export type Limits = Record<LimitKey, Limit>;

export interface LimitSpec { key: LimitKey; label: string; direction: Direction; unit: "x" | "pct"; help: string }

export const LIMIT_SPECS: LimitSpec[] = [
  { key: "grossLeverage", label: "Gross leverage", direction: "max", unit: "x", help: "Gross position value ÷ net liquidation" },
  { key: "equityLeverage", label: "Equity leverage", direction: "max", unit: "x", help: "|Positions| excluding cash-equivalent ETFs ÷ net liquidation" },
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

export interface Holding {
  symbol: string; currency: string; quantity: number | null; native: number | null;
  value: number | null; cashLike: boolean; maint: number | null; unrealized: number | null;
}
export interface CashLine { currency: string; native: number; value: number | null }

export interface RiskState {
  base: string;
  nav: number | null; gross: number | null; initMargin: number | null; maintMargin: number | null; excess: number | null;
  totalCash: number | null; cash: CashLine[]; borrowing: number | null;
  holdings: Holding[]; positionsGross: number; equityExposure: number; equityNet: number; cashLikeValue: number;
  unconverted: string[];
  /** Broker gross position value minus the sum of converted |positions|; null when either is unknown. */
  unreconciled: number | null;
  metrics: Record<LimitKey, number | null>;
}

function balance(rows: BrokerRow[], metric: string, currency: string): number | null {
  const row = rows.find(item => item.metric === metric && !item.segment && item.currency === currency);
  return num(row?.value_num);
}

export function computeRisk(base: string, balances: BrokerRow[], positions: BrokerRow[], cashLikeSymbols: string[]): RiskState {
  const cashLikeSet = new Set(cashLikeSymbols.map(symbol => symbol.trim().toUpperCase()).filter(Boolean));
  const rate = (currency: string, row?: BrokerRow): number | null => {
    const flex = num(row?.fx_rate_to_base);
    if (flex) return flex;
    if (currency === base) return 1;
    return balance(balances, "ExchangeRate", currency);
  };
  const nav = balance(balances, "NetLiquidation", base);
  const gross = balance(balances, "GrossPositionValue", base);
  const initMargin = balance(balances, "InitMarginReq", base);
  const maintMargin = balance(balances, "MaintMarginReq", base);
  const excess = balance(balances, "ExcessLiquidity", base);
  const totalCash = balance(balances, "TotalCashValue", base);

  const cash: CashLine[] = balances
    .filter(row => row.metric === "CashBalance" && !row.segment && row.currency && row.currency !== "BASE" && num(row.value_num) !== null && num(row.value_num) !== 0)
    .map(row => {
      const currency = String(row.currency);
      const native = num(row.value_num) as number;
      const fx = rate(currency);
      return { currency, native, value: fx === null ? null : native * fx };
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
    return {
      symbol, currency, quantity: num(row.position), native, value: toBase(native),
      cashLike: cashLikeSet.has(symbol.toUpperCase()),
      maint: toBase(num(row.maintenance_margin_contribution)), unrealized: toBase(num(row.unrealized_pnl)),
    };
  }).sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0));

  let positionsGross = 0, equityExposure = 0, equityNet = 0, cashLikeValue = 0;
  for (const holding of holdings) {
    if (holding.value === null) continue;
    positionsGross += Math.abs(holding.value);
    if (holding.cashLike) cashLikeValue += holding.value;
    else { equityExposure += Math.abs(holding.value); equityNet += holding.value; }
  }
  const complete = unconverted.size === 0;
  const unreconciled = gross !== null && complete && holdings.length ? gross - positionsGross : null;
  const ratio = (value: number | null) => value === null || nav === null || nav <= 0 ? null : value / nav;
  const largest = holdings.filter(holding => !holding.cashLike && holding.value !== null).reduce((max, holding) => Math.max(max, Math.abs(holding.value as number)), 0);

  return {
    base, nav, gross, initMargin, maintMargin, excess, totalCash, cash, borrowing,
    holdings, positionsGross, equityExposure, equityNet, cashLikeValue, unconverted: [...unconverted], unreconciled,
    metrics: {
      grossLeverage: ratio(gross ?? (complete && holdings.length ? positionsGross : null)),
      equityLeverage: complete ? ratio(equityExposure) : null,
      singleName: complete ? ratio(largest) : null,
      cashLike: complete ? ratio(Math.abs(cashLikeValue)) : null,
      initUtil: ratio(initMargin),
      maintUtil: ratio(maintMargin),
      excessLiquidity: ratio(excess),
      borrowing: ratio(borrowing),
    },
  };
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

export interface StressRow {
  id: string; label: string; navImpact: number; navImpactPct: number; navAfter: number;
  grossLeverageAfter: number | null; excessAfter: number | null; excessRatioAfter: number | null;
  result: "cushion" | "pressure" | "breach" | "call"; firstBreach: string | null;
}

/** Instantaneous price shocks; cash equivalents are held flat. Maintenance margin scales with each shocked
 * position (its reported contribution, else the account average rate). Approximate by construction. */
export function stressTests(state: RiskState, limits: Limits): StressRow[] {
  const { nav, excess } = state;
  if (nav === null || nav <= 0) return [];
  const exposure = state.gross ?? state.positionsGross;
  const maintRate = state.maintMargin !== null && exposure > 0 ? state.maintMargin / exposure : 0;
  const equity = state.holdings.filter(holding => !holding.cashLike && holding.value !== null);
  const largest = equity[0];
  const scenarios: { id: string; label: string; shock: (holding: Holding) => number }[] = [
    ...[0.05, 0.1, 0.2, 0.35].map(size => ({ id: `eq-${size}`, label: `Equities −${size * 100}%`, shock: () => -size })),
    ...(largest ? [{ id: "gap", label: `${largest.symbol} gaps −30%`, shock: (holding: Holding) => (holding === largest ? -0.3 : 0) }] : []),
  ];
  return scenarios.map(({ id, label, shock }) => {
    let navImpact = 0, grossChange = 0, maintRelief = 0;
    for (const holding of equity) {
      const move = shock(holding);
      const value = holding.value as number;
      navImpact += value * move;
      grossChange += Math.abs(value * (1 + move)) - Math.abs(value);
      const maint = holding.maint !== null ? Math.abs(holding.maint) : Math.abs(value) * maintRate;
      maintRelief += -move * maint;
    }
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
