import type { HubStatus } from "./components/ui";

export interface CatalogNamespace {
  id: string; title: string; summary: string; table_count: number; populated_tables: number; view_count: number;
  stored_rows: number; bytes_on_disk: number; status_counts: Partial<Record<HubStatus, number>>;
}

export interface CatalogTableSummary {
  name: string; namespace: string; title: string; summary: string; kind: "table" | "view"; engine: string;
  stored_rows: number; bytes_on_disk: number; first_data_at: string | null; last_data_at: string | null;
  last_ingested_at: string | null; status: HubStatus; status_reason: string; column_count: number;
  columns: string[]; column_notes: Record<string, string>; previewable: boolean;
}

export interface CatalogIndex {
  generated_at: string; namespaces: CatalogNamespace[]; tables: CatalogTableSummary[];
  previews_enabled: boolean; csv_enabled: boolean;
}

export type TypeClass = "text" | "enum" | "integer" | "number" | "decimal" | "datetime" | "date" | "uuid" | "bool" | "complex";

export interface CatalogColumn {
  name: string; type: string; friendly_type: string; type_class: TypeClass; description: string | null;
  nullable: boolean; in_primary_key: boolean; in_sorting_key: boolean; in_partition_key: boolean;
  hidden: boolean; operators: string[];
}

export interface CatalogTableDetail extends CatalogTableSummary {
  design_notes: string | null; notes: string[];
  keys: { primary_key: string; sorting_key: string; partition_key: string; explanation: string[] };
  column_details: CatalogColumn[];
  related: { direction: "out" | "in"; column: string; table: string; table_title: string; target_column: string }[];
  writers: { id: string; label: string; schedule: string }[];
  preview: {
    enabled: boolean; reason: string | null; csv_enabled: boolean; windowed: boolean; time_column: string | null;
    default_start: string | null; default_end: string | null; max_window_days: number; latest_version_only: boolean;
    hidden_columns: string[]; max_csv_rows: number; max_page_rows: number;
  };
}

export type Cell = string | number | boolean | null;

export interface RowsPage {
  table: string; columns: string[]; rows: Cell[][]; offset: number; limit: number; has_more: boolean;
  window_start: string | null; window_end: string | null; sort: string | null; descending: boolean;
  latest_version_only: boolean; truncated_cells: number; notes: string[];
}

export interface ColumnStat {
  name: string; nulls_percent: number | null; distinct_approx: number | null; min: string | null; max: string | null;
  top_values: string[];
}

export interface TableStats {
  table: string; generated_at: string; sample_rows: number; sample_limit: number;
  window_start: string | null; window_end: string | null; columns: ColumnStat[];
}

export interface PipelineRun {
  run_id: string; pipeline: string; source: string; status: string; started_at: string; completed_at: string | null;
  rows_written: number; requested_series: number; successful_series: number; failed_series: number; error: string | null;
}

export interface TableActivity {
  table: string; grain: "day" | "month"; time_column: string | null;
  buckets: { bucket: string; rows: number }[]; runs: PipelineRun[];
}

export interface PipelineCard {
  id: string; label: string; market: string; source: string; description: string; schedule: string; scheduled: boolean;
  per_symbol_runs: boolean; tables_written: string[]; status: HubStatus; status_reason: string;
  last_status: string | null; last_started_at: string | null; last_completed_at: string | null;
  runs_24h: number; success_24h: number; problem_24h: number; runs_7d: number; success_7d: number; problem_7d: number;
  rows_24h: number; days: { day: string; success: number; problem: number; total: number }[];
  recent_problems: PipelineRun[];
}

export interface PipelinesResponse {
  generated_at: string; pipelines: PipelineCard[];
  sources: { country_code: string; source: string; status: string; detail: string; checked_at: string | null }[];
}

export interface PipelineRunsPage { pipeline: string; items: PipelineRun[]; limit: number; offset: number; has_more: boolean }

export function tableHref(name: string, tab?: string): string {
  return `/data/tables/${name}${tab ? `?tab=${tab}` : ""}`;
}
