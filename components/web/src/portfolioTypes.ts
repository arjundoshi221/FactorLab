export interface Profile { user_id: string; display_name: string; active: boolean }
export interface BrokerAccount {
  broker_code: string; account_id: string; account_mode: string;
  base_currency?: string; booking_country?: string; active?: boolean;
  user_id: string | null; display_name: string; simulated: boolean;
}
export interface ItemsPage<T> { items: T[] }
export type BrokerRow = Record<string, string | number | null>;
export interface SnapshotPage extends ItemsPage<BrokerRow> {
  snapshot_time: string | null; age_seconds: number | null;
  source_channel: string | null; simulated: boolean;
  freshness: "not_reported" | "fresh" | "stale";
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
export interface PnlDetail extends OrderAssignment {
  broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  trading_symbol: string; currency: string; quantity: string; orders: string[];
  realized: string | null; unrealized: string | null; commissions: string | null; total: string | null;
  issues: string[]; price_time: string | null; history_start: string;
}
export interface PnlGroup {
  id: string | null; name: string; account_mode: string; simulated: boolean; currency: string;
  realized: string | null; unrealized: string | null; commissions: string | null; total: string | null;
  issues: string[]; details: PnlDetail[];
}

export type LedgerTagStatus = "tagged" | "untagged" | "not_taggable";
export interface LedgerRow extends OrderAssignment {
  key: string; broker_code: string; account_id: string; account_mode: string; simulated: boolean;
  perm_id: string; taggable: boolean; trading_symbol: string; listing_id: string | null;
  security_id: string | null; contract_id: string | null; product_type: string; currency: string;
  side: string; quantity: string; avg_price: string | null; first_fill_at: string; last_fill_at: string;
  fill_count: number; commissions: { currency: string; amount: string }[]; broker_realized_pnl: string | null;
  theme_name: string | null; strategy_name: string | null; trade_name_name: string | null;
  tag_status: LedgerTagStatus; fills: BrokerRow[]; fills_truncated: boolean;
}
export interface LedgerPage extends ItemsPage<LedgerRow> {
  total: number; limit: number; offset: number; counts: Record<LedgerTagStatus, number>;
  accounts: { broker_code: string; account_id: string; account_mode: string | null; simulated: boolean }[];
  generated_at: string;
}
