export interface SchemaInfoData {
  type: "schema_info" | "schema_overview";
  class_id?: string;
  class_name?: string;
  properties?: string;
  classes?: string;
  relationships?: string;
  description?: string;
  columns?: string[];
  sample_rows?: Record<string, unknown>[];
  row_count?: number;
  csv_file?: string;
  error?: string;
}

export interface QueryResultData {
  type: "query_result";
  class_id: string;
  class_name: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  sql?: string;
  aggregated?: boolean;
  dimensions?: string[];
  metrics?: Record<string, unknown>[];
}

export interface AlertsData {
  type: "alerts";
  class_id: string;
  class_name: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
}

export type VisualizationData = SchemaInfoData | QueryResultData | AlertsData;

export interface AnswerDataset {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  chart_type?: ChartConfigData["chart_type"];
  chart_config?: ChartConfigData;
  data: QueryResultData;
}

export interface Scenario {
  id: string;
  name: string;
  description: string;
  data_dir: string;
  ontology_dir: string;
  created_at: string;
}

// 兼容旧组件
export type CategoryDrilldownData = QueryResultData;
export type TransactionDetailData = QueryResultData;

// ============================================================
// 新增：Chat 交互组件类型
// ============================================================

/** Clarification 反问选项 */
export interface ClarificationOption {
  id: string;
  label: string;
  description?: string;
  value?: string;
  is_default?: boolean;
}

export interface ClarificationQuestion {
  group_id: string;
  group_name: string;
  group_type: string;
  metric_ids: string[];
  required: boolean;
  requires_value?: boolean;
  value_label?: string;
  options: ClarificationOption[];
}

export interface ClarificationAnswer {
  group_id: string;
  option_value: string;
  selection_value?: string;
}

/** Clarification 反问数据 */
export interface ClarificationData {
  question: string;
  options: ClarificationOption[];
  version?: number;
  reason?: string;
  questions?: ClarificationQuestion[];
  field?: string;          // 缺少的字段名，如 time_range / dimension
  multi_select?: boolean;  // 是否多选
}

/** Drill-down 下钻选项 */
export interface DrilldownOption {
  label: string;
  description: string;
  target_class?: string;
  dimension?: string;
  filters?: Record<string, string>;
  action?: string;         // "drill" | "raw_data" | "compare"
}

/** Drill-down 下钻数据 */
export interface DrilldownData {
  summary: string;
  options: DrilldownOption[];
  source_class?: string;
  source_dimension?: string;
}

/** Action 确认数据 */
export interface ActionConfirmData {
  action_id: string;
  action_name: string;
  action_type: string;
  description: string;
  message: string;
  requires_confirm: boolean;
  context?: Record<string, unknown>;
}

/** Action 执行结果 */
export interface ActionResultData {
  action_id: string;
  action_name: string;
  status: "success" | "failed" | "pending";
  result: string;
  duration?: number;
}

/** Plan 步骤 */
export interface PlanStep {
  step_id: string;
  description: string;
  tool: string;
  tool_args: Record<string, unknown>;
  status?: "pending" | "running" | "completed" | "failed";
  result?: unknown;
}

/** Plan 数据 */
export interface PlanData {
  title: string;
  plan_description?: string;
  steps: PlanStep[];
  current_step?: number;
  total_steps?: number;
}

/** Chart 配置数据 */
export interface ChartConfigData {
  chart_type: "bar" | "line" | "pie" | "scatter" | "gauge" | "kpi" | "table";
  title: string;
  x_field?: string;
  y_fields?: string[];
  data: Record<string, unknown>[];
  dimensions?: string[];
}


export interface ToolStep {
  name: string;
  description?: string;
  args?: any;
  result?: any;
  status: "running" | "completed" | "failed";
  startedAt?: number;
  planningFinishedAt?: number;
  planningDurationMs?: number;
  executionStartedAt?: number;
  executionDurationMs?: number;
  finishedAt?: number;
  durationMs?: number;
}

export interface ToolStepsPanelProps {
  steps: ToolStep[];
}


export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  visualization?: VisualizationData;
  answerDatasets?: AnswerDataset[];
  isLoading?: boolean; // 代表该消息正在生成/思考中
  steps?: ToolStep[];
  clarification?: ClarificationData;
  drilldown?: DrilldownData;
  actionConfirm?: ActionConfirmData;
  plan?: PlanData;
  chartConfig?: ChartConfigData;
}

export interface Conversation {
  id: string;
  title: string;
  scenario_id: string;
}