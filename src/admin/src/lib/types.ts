/* ─────────────── 类型定义 ─────────────── */

export type ReviewStatus = "pending" | "approved" | "rejected";

export interface Scenario {
  id: string;
  name: string;
  description: string;
  data_dir: string;
  ontology_dir: string;
  is_active: number;
  is_default: number;
  created_at: string;
}

export interface SchemaClass {
  id: string;
  scenario_id: string;
  name_cn: string;
  description: string;
  properties: string[];
  fields: SchemaField[];
  csv_file: string;
  primary_key: string;
  is_reviewed: boolean;
  review_status?: ReviewStatus;
}

export interface SchemaField {
  name: string;
  physical_name: string;
  type: "text" | "numeric" | "date" | "boolean";
  description: string;
  is_primary_key: boolean;
  is_foreign_key: boolean;
}

export interface SchemaRelationship {
  id: number;
  scenario_id: string;
  source: string;
  target: string;
  type: string;
  source_key: string;
  target_key: string;
  is_reviewed?: ReviewStatus | boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Metric {
  id: string;
  scenario_id: string;
  name: string;
  description: string;
  category: string;
  target_class: string;
  target_classes?: string[];
  calculation: string;
  formula: string;
  dimensions: string[];
  required_dimensions: string[];
  filters_hint: string;
  chart_type: string;
  sort_order: number;
  is_reviewed?: ReviewStatus | boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Concept {
  id: string;
  scenario_id: string;
  name: string;
  description: string;
  parent_id: string;
  level: number;
  concept_type: string;
  related_class: string;
  sort_order: number;
  is_reviewed: boolean;
  review_status?: ReviewStatus;
}

export interface ChartRule {
  id: number;
  scenario_id: string;
  data_pattern: string;
  chart_type: string;
  description: string;
  priority: number;
}

export interface FileInfo {
  name: string;
  size: number;
  modified: string;
  rows?: number;
  columns?: string[];
}

export interface GlossaryTerm {
  id: string;
  scenario_id: string;
  term: string;
  aliases: string[];
  description: string;
}

export interface Skill {
  id: string;
  scenario_id: string;
  name: string;
  description: string;
  trigger_condition: string;
  content: string;
  is_active: number;
  sort_order: number;
}

export interface User {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

export interface ExtractionLog {
  id: string;
  scenario_id: string;
  type: "schema" | "ontology" | "metrics" | "concepts" | "glossary";
  status: "running" | "success" | "failed";
  started_at: string;
  finished_at: string;
  duration: number;
  message: string;
  trigger: "manual" | "auto";
}

export interface AuditLog {
  id: string;
  user_id: number;
  username: string;
  action: string;
  resource_type: string;
  resource_id: string;
  scenario_id: string;
  detail: string;
  ip: string;
  created_at: string;
}

export interface SystemSettings {
  llm_provider: string;
  llm_model: string;
  llm_api_key: string;
  llm_base_url: string;
  extraction_batch_size: number;
  max_concurrent_extractions: number;
  auto_extract_on_upload: boolean;
  log_level: string;
}

export interface DashboardStats {
  total_scenarios: number;
  active_scenarios: number;
  total_files: number;
  total_schema_classes: number;
  total_metrics: number;
  total_concepts: number;
  total_glossary_terms: number;
  total_skills: number;
  recent_extractions: ExtractionLog[];
}

export interface DataConnection {
  id: string;
  scenario_id: string;
  name: string;
  db_type: "postgresql" | "mysql";
  connection_url_masked: string;
  is_active: number;
  created_at: string;
}

export interface DBTable {
  table_name?: string;
  name?: string;
  schema?: string;
  row_count: number;
  columns: string[] | { name: string; type?: string; nullable?: boolean; primary_key?: boolean }[];
}

export interface DBTablePreview {
  columns: string[] | { name: string; type?: string }[];
  rows: Record<string, unknown>[];
  row_count: number;
  sample_rows: Record<string, unknown>[];
}

export interface Action {
  id: string;
  scenario_id: string;
  name: string;
  description: string;
  action_type: "notification" | "webhook" | "email" | "data_update" | "workflow";
  trigger_condition: string;
  target_object: string;
  parameters: Record<string, unknown>;
  is_active: number;
  requires_confirm: number;
  sort_order: number;
  created_at: string;
}

export interface ActionLog {
  id: string;
  scenario_id: string;
  action_id: string;
  action_name: string;
  trigger_type: "manual" | "auto" | "chat";
  trigger_reason: string;
  status: "pending" | "success" | "failed";
  result: string;
  executed_at: string;
  finished_at: string;
  duration: number;
}

export interface AlertRule {
  id: string;
  scenario_id: string;
  name: string;
  description: string;
  target_class: string;
  condition_expression: string;
  action_id: string;
  severity: "info" | "warning" | "critical";
  is_active: number;
  last_triggered_at: string;
  trigger_count: number;
  created_at: string;
}
