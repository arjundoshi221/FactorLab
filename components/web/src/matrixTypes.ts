import type { Instrument } from "./instrumentTypes";

export type CellState = "ok" | "stale" | "missing" | "stored" | "none" | "not_configured" | "needs_tags" | "unavailable";

export interface MatrixColumn {
  key: string; label: string; group: "market" | "alternative" | "fundamentals" | "broker" | "tags";
  dataset: string; level: "listing" | "underlying" | "issuer" | "account" | "order";
  country: string | null; source: string | null; resolution: string | null; has_intent: boolean;
}
export interface MatrixCell {
  state: CellState; latest: string | null; count: number | null; detail: string;
  items: Record<string, unknown>[];
}
export interface MatrixRow { instrument: Instrument; cells: Record<string, MatrixCell> }
export interface MatrixPage {
  columns: MatrixColumn[]; items: MatrixRow[]; total: number; limit: number; offset: number;
  generated_at: string; expected_sessions: Record<string, string>; unavailable: string[];
}
