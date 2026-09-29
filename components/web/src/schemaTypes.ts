export interface SchemaColumn {
  name: string;
  type: string;
  position: number;
  nullable: boolean;
  default_kind: string | null;
  default_expression: string | null;
  in_primary_key: boolean;
  in_sorting_key: boolean;
  in_partition_key: boolean;
  description?: string | null;
}

export interface SchemaTable {
  name: string;
  namespace: string;
  domain: string;
  title: string;
  summary: string;
  notes: string[];
  kind: "table" | "view";
  engine: string;
  stored_rows: number;
  bytes_on_disk: number;
  primary_key: string;
  sorting_key: string;
  partition_key: string;
  columns: SchemaColumn[];
}

export interface SchemaArea {
  id: string;
  title: string;
  summary: string;
}

export interface RelationshipEndpoint {
  table: string;
  column: string;
}

/** identity: what a row is about; lineage: where it came from; lookup: shared codes like country. */
export type RelationshipCategory = "identity" | "lineage" | "lookup";

export interface SchemaRelationship {
  id: string;
  source: RelationshipEndpoint;
  target: RelationshipEndpoint;
  kind: "logical";
  cardinality: "many_to_one";
  optional: boolean;
  enforced: false;
  category: RelationshipCategory;
}

export interface SchemaMapResponse {
  generated_at: string;
  schema_fingerprint: string;
  areas: SchemaArea[];
  tables: SchemaTable[];
  relationships: SchemaRelationship[];
  warnings: string[];
}
