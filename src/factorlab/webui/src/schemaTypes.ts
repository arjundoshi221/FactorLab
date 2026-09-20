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
}

export interface SchemaTable {
  name: string;
  domain: string;
  engine: string;
  stored_rows: number;
  bytes_on_disk: number;
  primary_key: string;
  sorting_key: string;
  partition_key: string;
  columns: SchemaColumn[];
}

export interface RelationshipEndpoint {
  table: string;
  column: string;
}

export interface SchemaRelationship {
  id: string;
  source: RelationshipEndpoint;
  target: RelationshipEndpoint;
  kind: "logical";
  cardinality: "many_to_one";
  optional: boolean;
  enforced: false;
}

export interface LayoutNode {
  table: string;
  x: number;
  y: number;
  collapsed: boolean;
}

export interface LayoutViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface SharedSchemaLayout {
  revision: number;
  schema_fingerprint: string;
  nodes: LayoutNode[];
  viewport: LayoutViewport;
  updated_at: string | null;
}

export interface SchemaMapResponse {
  generated_at: string;
  database: string;
  schema_fingerprint: string;
  tables: SchemaTable[];
  relationships: SchemaRelationship[];
  layout: SharedSchemaLayout;
  warnings: string[];
}
