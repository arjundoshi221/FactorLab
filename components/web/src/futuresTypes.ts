// Response model of /hub/api/v1/us/futures (components/api futures.py).

export type FuturesGroup = "index" | "energy" | "metals" | "rates" | "fx" | "other";

export interface FuturesContract {
  symbol: string;
  contract_id: string;
  expiry: string;
  last: number | null;
  bid: number | null;
  ask: number | null;
  settlement_prev: number | null;
  change: number | null;
  change_pct: number | null;
  day_volume: number | null;
  oi: number | null;
  observed_at: string | null;
  stale: boolean;
}

export interface FuturesRoot {
  root: string;
  name: string;
  product_type: string;
  group: FuturesGroup;
  contracts: FuturesContract[];
}

export interface FuturesResponse {
  generated_at: string;
  market_open: boolean;
  items: FuturesRoot[];
}
