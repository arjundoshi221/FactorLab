// Response models of /hub/api/v1/us/indexes* and /sectors/performance (components/api indexes.py).

export type Weighting = "ew" | "cw";
export type Period = "1d" | "1w" | "1m" | "3m" | "ytd" | "1y";
export type Returns = Record<Period, number | null>;

export interface LatestValue {
  code: string;
  name: string;
  value: number | null;
  change: number | null;
  trade_date: string | null;
  complete: boolean;
}

export interface TrackedIndex extends LatestValue { group: "index" | "market_etf" }
export interface ConstructedIndex extends LatestValue { weighting: Weighting; sector_id: string | null }
export interface SectorEntry { sector_id: string; name: string; cw_etf: string; ew_etf: string }

export interface IndexOverview {
  tracked: TrackedIndex[];
  constructed: ConstructedIndex[];
  sectors: SectorEntry[];
  base_date: string;
  universe: string;
}

export interface Mover { symbol: string | null; ret_1d: number | null; contribution: number }

export interface SectorPerformance {
  sector_id: string;
  name: string;
  code: string;
  level: number | null;
  complete: boolean;
  returns: { ew: Returns; cw: Returns; cw_etf: Returns; ew_etf: Returns };
  cw_etf: string;
  ew_etf: string;
  movers: { top: Mover[]; bottom: Mover[] };
}

export interface PerformanceResponse {
  as_of: string | null;
  weighting: Weighting;
  periods: Period[];
  sectors: SectorPerformance[];
  benchmarks: Record<"composite_ew" | "composite_cw" | "$SPX" | "RSP", Returns>;
}

export interface SeriesPoint { trade_date: string; value: number; complete: boolean }
export interface Series { code: string; name: string; kind: "constructed" | "tracked"; points: SeriesPoint[] }

export interface Constituent {
  listing_id: string;
  symbol: string | null;
  name: string | null;
  weight: number;
  ret_1d: number | null;
  contribution: number;
}

export interface ConstituentsResponse { code: string; trade_date: string | null; items: Constituent[]; total: number }
