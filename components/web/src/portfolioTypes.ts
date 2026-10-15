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
