export interface Instrument {
  kind: "listing" | "contract"; id: string; listing_id: string; security_id: string;
  entity_id: string; symbol: string; name: string; country: string; exchange: string;
  instrument_type: string; active: boolean; configured: boolean;
  expiry: string | null; strike: string | null; right: string;
  sector_id: string; sector: string;
}
export interface InstrumentPage { items: Instrument[]; total: number; limit: number; offset: number }
export interface SectorCount { sector_id: string; sector: string; count: number }
export interface SectorSummary { items: SectorCount[]; unclassified: number; total: number }
export interface Dataset {
  table: string; title: string; description: string; group: string; classification: string;
  relationships: string[]; time_column: string | null; dimensions: string[];
  writers: string[]; notes: string[];
}
export interface Identity {
  instrument: Instrument; aliases: Record<string, unknown>[]; related: Instrument[];
  datasets: Dataset[]; warnings: string[]; identifiers: Record<string, Record<string, unknown>[]>;
}
export interface DatasetResult {
  table: string; state: "stored" | "empty" | "missing_table" | "unavailable" | "unsupported";
  checked_at: string; message: string; groups: Record<string, unknown>[];
  columns: string[]; rows: unknown[][]; has_more: boolean; limit: number; offset: number;
  start: string | null; end: string | null;
  evidence: Record<string, { state: string; message?: string; table?: string;
    columns?: string[]; rows?: unknown[][]; unavailable_ids?: string[] }>;
}
