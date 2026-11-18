export interface Profile { user_id: string; display_name: string; active: boolean }
export interface BrokerAccount {
  broker_code: string; account_id: string; account_mode: string;
  base_currency?: string; booking_country?: string; active?: boolean;
  user_id: string | null; display_name: string; simulated: boolean;
}
export interface ItemsPage<T> { items: T[] }
/** One row's bucket for a pivot dimension; `id` null is Unclassified. */
export interface AttrValue { id: string | null; label: string }
/** A pivot dimension (`/portfolio/dimensions`) with its buckets in display order. */
export interface Dimension { id: string; label: string; buckets: AttrValue[] }
/** Present when the request asked for `dims=`. */
export interface Attributed { attributes?: Record<string, AttrValue> }
export type BrokerRow = Record<string, string | number | null>;
export interface SnapshotPage extends ItemsPage<BrokerRow> {
  snapshot_time: string | null; age_seconds: number | null;
  source_channel: string | null; simulated: boolean;
  freshness: "not_reported" | "fresh" | "stale";
  unavailable_dimensions?: string[];
}
export interface AccountSummary { account: BrokerAccount; balances: SnapshotPage }
export interface ExecutionPage extends ItemsPage<BrokerRow> { limit: number; offset: number; has_more: boolean }

export type CatalogKind = "themes" | "strategies" | "trade-names";
export interface CatalogEntry { id: string; user_id: string; name: string; description: string; active: boolean }
export interface OrderAssignment { theme_id: string | null; strategy_id: string | null; trade_name_id: string | null }
export interface TaggedOrder extends OrderAssignment {
  key: string; perm_id: string; trading_symbol: string; currency: string; side: string;
  status: string; filled_quantity: string; taggable: boolean; fills: BrokerRow[];
}
export interface OrdersPage extends ItemsPage<TaggedOrder> { limit: number; offset: number; has_more: boolean }
/** IBKR's own figures from Flex statements, net of commissions: total = realized + unrealized. */
export interface IbkrValues {
  ibkr_realized?: string | null; ibkr_unrealized?: string | null; ibkr_total?: string | null; ibkr_issues?: string[];
}
export interface PnlDetail extends OrderAssignment, IbkrValues, Attributed {
  broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  trading_symbol: string; currency: string; quantity: string; orders: string[];
  realized: string | null; unrealized: string | null; commissions: string | null; total: string | null;
  issues: string[]; price_time: string | null; history_start: string | null;
}
export interface PnlGroup extends IbkrValues {
  id: string | null; name: string; account_mode: string; simulated: boolean; currency: string;
  realized: string | null; unrealized: string | null; commissions: string | null; total: string | null;
  issues: string[]; details: PnlDetail[];
  /** Instruments left out of realized/unrealized because they cannot be valued; their fees still count. */
  excluded?: ({ trading_symbol: string; account_id: string; issues: string[] } & Attributed)[];
  /** Instruments left out of the IBKR figures. */
  ibkr_excluded?: { trading_symbol: string; account_id: string; issues: string[] }[];
}
export type ReconciliationStatus = "match" | "mismatch" | "ours_unavailable" | "ibkr_unavailable" | "not_comparable";
/** All-time tagged-FIFO total vs IBKR's total for one account/instrument. */
export interface ReconciliationRow {
  broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  trading_symbol: string; currency: string; quantity: string; fills: number;
  ours: { realized: string | null; unrealized: string | null; commissions: string | null; total: string | null };
  ibkr: { realized: string | null; unrealized: string | null; total: string | null };
  difference: string | null; status: ReconciliationStatus; reasons: string[];
}
export interface StatementInfo {
  broker_code: string; account_id: string; generated_at: string | null; statement_to: string | null; lot_detail: boolean;
}
/** Flex-reported account-level costs (not attributable to a fill), as positive amounts. */
export interface AccountFee {
  broker_code: string; account_id: string; account_mode: string; simulated: boolean; currency: string;
  fees: string; commission_adjustments: string; fx_conversions?: string; total: string;
}
export interface PnlReport extends ItemsPage<PnlGroup> {
  period?: { since: string; start: string; timezone: string } | null;
  account_fees?: AccountFee[];
  reconciliation?: ReconciliationRow[];
  statements?: StatementInfo[];
  unavailable_dimensions?: string[];
  /** Latest broker exchange rates per account: 1 `currency` = `rate` `base`. */
  fx_rates?: { currency: string; base: string; rate: string; as_of: string | null }[];
}

export type LedgerTagStatus = "tagged" | "untagged" | "not_taggable";
export interface LedgerRow extends OrderAssignment, Attributed {
  key: string; broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  perm_id: string; taggable: boolean; trading_symbol: string; listing_id: string | null;
  security_id: string | null; contract_id: string | null; product_type: string; currency: string;
  side: string; quantity: string; avg_price: string | null; first_fill_at: string; last_fill_at: string;
  fill_count: number; commissions: { currency: string; amount: string }[]; broker_realized_pnl: string | null;
  theme_name: string | null; strategy_name: string | null; trade_name_name: string | null;
  tag_status: LedgerTagStatus; fills: BrokerRow[]; fills_truncated: boolean;
}
/** A pivot bucket (e.g. a sector) over every filtered order, ignoring the bucket filter. */
export interface LedgerBucket extends AttrValue { counts: Record<LedgerTagStatus, number> }
export interface LedgerPage extends ItemsPage<LedgerRow> {
  total: number; limit: number; offset: number; counts: Record<LedgerTagStatus, number>;
  accounts: { broker_code: string; account_id: string; account_mode: string | null; simulated: boolean }[];
  generated_at: string;
  /** `dims=sector`: buckets per dimension; a failed dimension is in `unavailable_dimensions`. */
  pivots?: Record<string, LedgerBucket[]>; unavailable_dimensions?: string[];
}

export type TradeStatus = "open" | "closed";
export interface TradeFill {
  exec_id: string; exec_time: string; perm_id: string; side: string; role: "open" | "add" | "reduce" | "close";
  quantity: string; price: string; commission: string | null; commission_ccy: string | null;
}
/** One position's life from flat to flat in one account, instrument and currency. */
export interface TradeRow extends OrderAssignment, Attributed {
  key: string; broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  trading_symbol: string; listing_id: string | null; security_id: string | null; contract_id: string | null;
  product_type: string; currency: string; direction: "long" | "short"; status: TradeStatus;
  opened_at: string; closed_at: string | null; last_fill_at: string;
  quantity: string; max_quantity: string; entry_quantity: string; exit_quantity: string;
  avg_entry_price: string; avg_exit_price: string | null; open_cost: string;
  market_price: string | null; price_time: string | null;
  realized: string | null; unrealized: string | null; commissions: { currency: string; amount: string }[];
  net: string | null; return_pct: string | null;
  theme_name: string | null; strategy_name: string | null; trade_name_name: string | null; tags_mixed: boolean;
  orders: string[]; fill_count: number; fills: TradeFill[]; fills_truncated: boolean; issues: string[];
}
export interface TradeSummary {
  currency: string; account_mode: string; simulated: boolean;
  closed: number; valued: number; wins: number; losses: number; realized: string; net: string | null;
}
/** Open trades per currency/mode/simulation; `unrealized` is null if any is unavailable. */
export interface OpenSummary { currency: string; account_mode: string; simulated: boolean; open: number; unrealized: string | null }
/** A pivot bucket (e.g. a sector) over every filtered trade, all statuses, ignoring the bucket filter. */
export interface TradeBucket extends AttrValue { counts: Record<TradeStatus, number>; summary: TradeSummary[]; open: OpenSummary[] }
export interface TradesPage extends ItemsPage<TradeRow> {
  total: number; limit: number; offset: number; counts: Record<TradeStatus, number>; summary: TradeSummary[];
  accounts: { broker_code: string; account_id: string; account_mode: string | null; simulated: boolean }[];
  generated_at: string;
  pivots?: Record<string, TradeBucket[]>; unavailable_dimensions?: string[];
}

/** `GET /accounts/{broker}/{account}/performance?since=` — base currency, New York dates. */
export interface PerformanceNavPoint { date: string; nav: number; contributions: number; twr: number; ret: number | null }
export interface PerformanceMonth { month: string; start_nav: number; end_nav: number; flows: number; profit: number; twr: number }
export interface AttributionLine { key: string; label: string; amount: number; note: string }
export interface PerformanceReport {
  currency: string | null; since: string; start: string | null; end: string | null;
  opening_nav: number | null; closing_nav: number | null;
  deposits: number; withdrawals: number; net_contributions: number;
  net_profit: number | null; return_on_capital: number | null; twr: number | null; xirr: number | null;
  /** IBKR's own daily TWR chained over the same days; null when statements do not cover them. */
  ibkr_twr: number | null;
  /** Interest charged or credited plus the change in interest accruals (negative = cost). */
  financing_cost: number | null;
  /** "ibkr": IBKR's daily change-in-NAV lines; "cash": cash records with a residual. */
  attribution_source: "ibkr" | "cash";
  attribution: AttributionLine[]; nav: PerformanceNavPoint[]; monthly: PerformanceMonth[];
  warnings: string[]; basis: string[];
  risk: RiskStats | null; benchmarks: Benchmark[];
}
/** Statistical risk over the report's daily time-weighted returns. Fractions; VaR/ES positive = loss. */
export interface RiskStats {
  observations: number; daily_vol: number | null; annual_vol: number | null;
  var95: number | null; es95: number | null; var95_amount: number | null; es95_amount: number | null;
  max_drawdown: number | null; drawdown_peak: string | null; drawdown_trough: string | null; current_drawdown: number | null;
  beta: number | null; correlation: number | null; benchmark: string | null; enough_history: boolean;
}
export interface Benchmark { code: string; label: string; note: string; twr: number | null; excess: number | null; points: { date: string; value: number }[] }
