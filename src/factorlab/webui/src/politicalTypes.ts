export type PoliticalMetric =
  | "trades"
  | "unique_legislators"
  | "unique_tickers"
  | "amount_min"
  | "late_disclosures";

export interface PoliticalDashboardData {
  as_of_date: string;
  current_legislators: number;
  current_committees: number;
  filings: number;
  parsed_filings: number;
  trades: number;
  unique_legislators: number;
  unique_tickers: number;
  unparsed_filings: number;
  legislator_match_rate: number;
  ticker_resolution_rate: number;
  late_disclosures: number;
  anomaly_count: number;
  latest_filing_date: string | null;
  latest_transaction_date: string | null;
  last_ingested_at: string | null;
  collected_on_date: number;
}

export interface PoliticalCoverage {
  chamber: string;
  filings: number;
  parsed_filings: number;
  unparsed_filings: number;
  filing_parse_rate: number;
  trades: number;
  unique_legislators: number;
  matched_legislator_trades: number;
  legislator_match_rate: number;
  unique_tickers: number;
  ticker_resolution_rate: number;
  first_filing_date: string | null;
  latest_filing_date: string | null;
  last_ingested_at: string | null;
}

export interface PoliticalMetricPoint {
  bucket: string;
  value: number;
}

export interface PoliticalMetricsSeries {
  metric: PoliticalMetric;
  date_basis: "transaction";
  group_by: "month";
  points: PoliticalMetricPoint[];
}

export interface PoliticalTrade {
  trade_key: string;
  chamber: string;
  filing_id: string;
  filing_date: string;
  filing_url: string;
  bioguide_id: string | null;
  legislator_name: string;
  state: string;
  district: number | null;
  owner_code: string;
  filer_type: string;
  asset_name_raw: string;
  ticker: string | null;
  asset_type_code: string;
  transaction_type: string;
  transaction_date: string;
  notification_date: string | null;
  amount_str: string;
  amount_min: number | null;
  amount_max: number | null;
  source: string;
  as_of_time: string;
  ingested_at: string;
}

export interface PoliticalTradesPage {
  items: PoliticalTrade[];
  next_cursor: string | null;
  limit: number;
  data_as_of: string | null;
}
