export type USResolution = "1min" | "daily";
export type USScope = "all" | "minute" | "daily_only" | "no_data";

export interface USSeries {
  resolution: USResolution;
  source: string;
  status: string;
  expected: number;
  actual: number;
  missing: number;
  available_from: string | null;
  last_bar: string | null;
  error: string | null;
}

export interface USInstrument {
  instrument_id: string;
  symbol: string;
  name: string;
  exchange_code: string;
  series: USSeries[];
}

export interface USSourceStatus { source: string; status: string; detail: string }
export interface USDashboard {
  trading_date: string;
  market_status: string;
  instruments: number;
  source: USSourceStatus;
  sources: USSourceStatus[];
  universe: {
    active: number;
    daily_configured: number;
    minute_configured: number;
    daily_with_data: number;
    minute_with_data: number;
    no_daily_data: number;
  };
  resolutions: Record<USResolution, {
    actual: number; expected: number; missing: number; coverage_percent: number | null;
  }>;
}

export interface USCandle {
  trade_date?: string; bar_time?: string; open: string | number; high: string | number;
  low: string | number; close: string | number; volume: number;
}
export interface USDay {
  trade_date: string; resolution: USResolution; expected: number; actual: number; missing: number;
}
export interface USPage<T> {
  items: T[]; total: number | null; offset: number; next_cursor: string | null;
}

export interface USRun {
  run_id: string;
  pipeline: string;
  source: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  successful_series: number;
  failed_series: number;
  rows_written: number;
  error: string | null;
}
