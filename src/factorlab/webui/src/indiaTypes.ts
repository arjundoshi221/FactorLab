export type IndiaCheckStatus = "healthy" | "attention" | "missing" | "not_expected";
export type IndiaCollectionScope = "all" | "collecting" | "historical" | "not_configured";
export type IndiaCollectionStatus = Exclude<IndiaCollectionScope, "all">;

export interface IndiaDashboardData {
  trading_date: string;
  market_status: string;
  reference_instruments: number;
  expected_series: number;
  series_with_data: number;
  data_points: number;
  expected_data_points: number;
  coverage_percent: number;
  collected_on_date: number;
  backfilled_on_date: number;
  last_bar_time: string | null;
  last_ingested_at: string | null;
  freshness_seconds: number | null;
  anomaly_count: number;
}

export interface IndiaInstrumentRow {
  instrument_id: string;
  symbol: string;
  name: string | null;
  exchange_code: string | null;
  segment: string | null;
  instrument_type: string | null;
  status: string | null;
  source: string | null;
  collection_status: IndiaCollectionStatus;
  data_points: number;
  trading_days: number;
  unique_series: number;
  first_bar_time: string | null;
  last_bar_time: string | null;
  last_ingested_at: string | null;
  expected_series: number;
  selected_data_points: number;
  selected_unique_series: number;
  expected_data_points: number;
  coverage_percent: number | null;
  ohlc_violations: number;
  null_ohlc_values: number;
  outside_session: number;
  duplicate_versions: number;
  quality_issue_count: number;
  check_status: IndiaCheckStatus;
  check_reason: string;
}

export interface IndiaInstrumentPage {
  trading_date: string;
  market_status: string;
  expected_points_per_series: number;
  scope: IndiaCollectionScope;
  items: IndiaInstrumentRow[];
  total: number;
  reference_total: number;
  collecting_total: number;
  historical_total: number;
  not_configured_total: number;
  limit: number;
  offset: number;
  data_as_of: string | null;
}

export interface IndiaInstrumentDay {
  trading_date: string;
  market_status: string;
  data_points: number;
  unique_series: number;
  expected_series: number;
  expected_data_points: number;
  coverage_percent: number | null;
  first_bar_time: string | null;
  last_bar_time: string | null;
  last_ingested_at: string | null;
  ohlc_violations: number;
  null_ohlc_values: number;
  outside_session: number;
  duplicate_versions: number;
  quality_issue_count: number;
  check_status: IndiaCheckStatus;
  check_reason: string;
}

export interface IndiaInstrumentDays {
  instrument_id: string;
  date_from: string;
  date_to: string;
  items: IndiaInstrumentDay[];
}

export interface IndiaCandle {
  instrument_id: string;
  contract_id: string;
  symbol: string;
  market_code: string;
  bar_time: string;
  open: number | string | null;
  high: number | string | null;
  low: number | string | null;
  close: number | string | null;
  volume: number | null;
  oi: number | null;
  source: string;
  as_of_time: string;
  ingested_at: string;
}

export interface IndiaCandlePage {
  items: IndiaCandle[];
  next_cursor: string | null;
  limit: number;
  data_as_of: string | null;
}
