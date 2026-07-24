"""Prompt templates used by the ontology extraction pipeline."""

PHASE1_TABLE_PROMPT = """你是一个顶级数据仓库建模与本体论专家。请根据以下提供的单个数据分片元数据、部分样本，以及【全表完整列目录】，只提取实体类（Class）。

## 业务上下文
{business_name}

## 核心修复提示
你当前处理的可能是一个宽表垂直切分后的分片。请参考下方的【全表完整列目录】识别完整的字段语义、主键和潜在维度字段；Metric 会在所有 Class 合并完成后的第二阶段统一定义。
如果表元数据中包含 table_comment，请将其作为判断实体业务边界、Class 中文命名、Class 描述和指标分类的高优先级依据；表注释通常比物理表名更接近业务语义。
如果字段元数据中包含 column_comments 或 full_table_catalog.comment，请将其作为理解字段业务含义、命名逻辑字段和识别指标/维度的高优先级依据；字段注释比物理字段名更接近业务语义。
你必须同时参考 table_summary.dataset_catalog 中的全部已选数据源，从全局角度判断当前 Class 与其他 Class 的边界、潜在关联键和 Concept 归类线索；不要只根据当前分片孤立命名。

## 当前分片数据流摘要
{table_summary}

## 输出要求
请输出标准 JSON 格式，严禁夹带任何 Markdown 解释文本，结构必须严格如下：
{{
	"class": {{
		"id": "PascalCase类名，如 Sale 或 PbtProcessMonitoring",
		"name_cn": "中文逻辑名称",
		"description": "该实体在业务上的核心定义与边界描述",
		"primary_key": "主键物理列名，必须从当前分片的 columns 中选择",
		"table_name": "当前分片的 CSV 文件名或者数据库表名（如：sales.csv 或 t_person_level_kpis）",
		"fields": [
			{{
                "name_cn": "字段业务逻辑中文名（如：销售金额、生产批次号）",
                "name": "物理列名（必须与输入的物理列名完全一致）",
				"type": "text / numeric / date / boolean",
				"description": "字段描述及业务含义",
				"is_primary_key": false,
				"is_foreign_key": false
			}}
		]
		}}
}}
"""

PHASE2_DIMENSION_GROUP_PROMPT = """你是企业语义层与维度建模专家。第一阶段已经完成所有物理表分片的合并，下面提供的是全局唯一、字段完整的实体类（Classes）和数据源目录。
现在请先抽取可复用的“分析维度组（DimensionGroup）”。不要生成 Metrics、Relationships 或 Concepts。

## 业务上下文
{business_name}

## 实体类 Schema 清单
{classes_summary}

## 已选数据源全局目录
{dataset_catalog}

## 维度组规则
1. DimensionGroup 是业务概念与物理字段之间的受治理桥梁，例如“时间粒度”“区域”“品类”“渠道”。不要输出单一字段的重复维度组。
2. `id` 使用稳定下划线英文，如 `time_granularity`、`region`；`name` 使用业务名称。
3. `group_type` 仅为 `time`、`categorical` 或 `hierarchy`。
4. `options` 是面向业务用户的选择。时间组可包含“按月/按季度/按财年”等选项；每项 value 必须稳定、唯一，label 必须可读，只有一个默认项。
5. `field_mappings` 负责把 option 映射到真实 Class 的**逻辑字段名**；class_id 和 field_name 必须来自上方 Schema。一个 option 可有多个 Class 映射以支持多表。
6. 只有未指定会导致指标统计口径明显不完整的组才设置 `is_required=true`。时间粒度通常可设为必选，并可通过 `default_option` 与 `clarification_policy="auto_fill"` 降低追问频率。
7. 不确定的字段不要映射；宁可少输出，也不得虚构 Class 或字段。

只输出 JSON：
{{
    "dimension_groups": [
    {{
        "id": "time_granularity",
        "name": "时间粒度",
        "description": "数据按何种时间周期聚合",
        "group_type": "time",
        "is_required": true,
        "default_option": "month",
        "clarification_policy": "auto_fill",
        "options": [
            {{"value": "month", "label": "按月", "aliases": ["本月", "上月", "环比"], "is_default": true}},
            {{"value": "quarter", "label": "按季度", "aliases": ["本季度"], "is_default": false}}
        ],
        "field_mappings": [
            {{"option_value": "month", "class_id": "SaleOrder", "field_name": "月份", "display_name": "月份", "priority": 0}}
        ]
    }}
    ]
}}
"""

PHASE3_GLOBAL_PROMPT = """你是企业指标语义建模专家。第一、二阶段已经完成所有物理表分片的合并，并定义了可复用的分析维度组。
现在请在全局范围内定义可执行的结构化 Metrics，并补充这些 Metrics 所需的 Class 关联关系。不要生成 Concepts；概念层级将在下一阶段单独规划。

## 业务上下文
{business_name}

## 实体类 Schema 清单（已执行轻量化压缩）
{classes_summary}

## 已选数据源全局目录
{dataset_catalog}

## 已提取分析维度组
{dimension_groups_summary}

## 结构化 Metric definition 规则（必须严格遵守）
1. 每个 Metric 必须提供 `definition`，且 `version` 固定为 1；不得输出 `formula`、`calculation`、`value_field`、顶层 `aggregation` 或顶层 `metric_filters` 等旧字段。
2. `definition.anchor_class` 是指标的查询锚点，必须是上方 Class 的 ID；`target_class` 必须与它完全相同。
3. 每个 `inputs[]` 是独立聚合来源，必须包含 `id`、`output_name`、`class_id`、`source_shape`、`field`、`aggregation`、`filters`：
    - `class_id` 必须来自上方 Class ID；`inputs[].field` 与 `inputs[].filters[].field` 必须使用该 Class 的**物理字段名**（`name`）；`dimensions`、`required_dimensions` 使用逻辑字段名（`name_cn`）。不可臆造字段。
		- 宽表：`source_shape="wide"`，直接选择业务数值字段；`filters` 通常为空。
		- 窄表：`source_shape="long"`，选择公共数值字段，且必须至少一个 `filters` 固定 WHERE 条件来识别 KPI、规格、品类或序列。
		- `filters` 的操作符仅允许 `=`、`!=`、`IN`、`NOT IN`、`IS NULL`、`IS NOT NULL`；`IN` / `NOT IN` 的 value 必须是数组。
4. 单输入指标使用 `ADD`。同口径并列展示的多个结果使用 `CONCAT`，每个输入必须具有唯一且业务可读的 `output_name`。只有业务上确需组合计算时，才使用 `ADD`、`SUBTRACT`、`MULTIPLY` 或 `DIVIDE`。可选 `offset` 是表达式完成后追加的有限数字；例如同比或环比增长率应使用 `DIVIDE`、两个输入，并设置 `offset: -1`，即 `(分子 / NULLIF(分母, 0)) - 1`。`CONCAT` 不得设置非零 `offset`。
5. 跨 Class Metric 的每个输入 Class 必须可通过下方 `relationships` 与 anchor_class 连通；无法确认关联时，不要生成跨 Class Metric。
6. 每个 Metric 必须输出 `dimension_group_ids`，只引用上方已提取的维度组 ID。不要创建不存在的维度组 ID；没有适用组时输出空数组。`required_dimensions` 保留为兼容字段，优先输出空数组，必选治理由维度组的 `is_required` 负责。
7. `dimensions` 必须属于 anchor_class；`required_dimensions` 是 `dimensions` 的子集。
8. Relationships 必须结合数据源目录、字段名、主键/外键线索和 Metric 依赖判断，不能只按字段名称猜测。

## 输出要求
请输出标准 JSON 格式，结构必须严格如下：
{{
	"relationships": [
		{{
			"source": "源 Class ID",
			"target": "目标 Class ID",
			"type": "belongs_to / has_many / references / correlates_with / affects",
			"source_key": "源 Class 中的关联物理字段名（多字段逗号分隔）",
			"target_key": "目标 Class 中的关联物理字段名（多字段逗号分隔）",
			"description": "关联关系的业务线索描述"
		}}
	],
    "metrics": [
    {{
        "id": "下划线英文指标ID，如 total_sales",
        "name": "指标中文名称",
        "description": "指标口径、业务含义和适用场景",
        "category": "销售 / 质量 / 生产 / 财务等分类",
        "target_class": "与 definition.anchor_class 完全相同的 Class ID",
        "definition": {{
            "version": 1,
            "anchor_class": "Class ID",
            "expression_operator": "ADD / SUBTRACT / MULTIPLY / DIVIDE / CONCAT",
            "offset": 0,
            "inputs": [{{
                "id": "input_1",
                "output_name": "组成项中文名称",
                "class_id": "Class ID",
                "source_shape": "wide / long",
                "field": "物理数值字段名",
                "aggregation": "SUM / AVG / MIN / MAX / COUNT / COUNT_DISTINCT",
                "filters": [{{"field": "物理条件字段名", "operator": "=", "value": "固定值"}}]
            }}]
        }},
        "dimensions": ["锚点类逻辑维度字段名"],
        "required_dimensions": ["锚点类必要逻辑维度字段名"],
        "dimension_group_ids": ["time_granularity"],
        "chart_type": "bar / line / pie / table / scatter / heatmap / funnel / radar"
    }}
	]
}}
"""

PHASE3_CONCEPT_PROMPT = """你是企业本体与领域驱动设计专家。Class、Relationship 和结构化 Metric 已在前两个阶段确定。
现在仅根据以下已确定资产构建默认 Concepts 树，不得修改、补充或删除 Class、Relationship、Metric。

## 业务上下文
{business_name}

## 实体类
{classes_summary}

## 已确定 Relationships
{relationships_summary}

## 已确定 Metrics
{metrics_summary}

## 概念层级树构建规则
1. Concepts 必须为多级树，不能全部平铺；每个非顶级节点的 parent_id 必须指向本次输出的 Concepts ID。
2. 顶级节点：level=1、parent_id=""、concept_type="subject_domain"，表示宏观业务主题域。
3. 二级节点：level=2，使用 `dimension_group` 或 `fact_group`，归属到一个主题域。
4. 三级实体节点：level=3，关联一个实际 Class，related_class 必须来自上方实体类 ID。
5. KPI/指标概念应挂在 `fact_group` 下；若引用业务实体，related_class 填对应 Metric 的 anchor_class，并在 description 中说明覆盖的 Metrics。
6. 每个 Class 至少应有一个 related_class 指向它的概念节点；概念 ID 使用下划线英文且唯一。

只输出 JSON：
{{
    "concepts": [
        {{
            "id": "sales_domain",
            "name": "销售主题域",
            "description": "概念的业务边界与关联指标/实体说明",
            "parent_id": "",
            "level": 1,
            "concept_type": "subject_domain / dimension_group / fact_group",
            "related_class": "可选的 Class ID；非实体节点留空"
        }}
    ]
}}
"""

__all__ = [
    "PHASE1_TABLE_PROMPT",
    "PHASE2_DIMENSION_GROUP_PROMPT",
    "PHASE3_GLOBAL_PROMPT",
    "PHASE3_CONCEPT_PROMPT",
	"build_global_correction_prompt",
	"build_optimization_batch_prompt",
	"build_quality_assessment_prompt",
	"build_schema_optimization_retry_prompt",
]


def build_optimization_batch_prompt(
	*,
	doc_context: str,
	schema_reference: str,
	stage_context: str,
	batch: str,
	stage_rules: str,
) -> str:
	"""Build the per-batch Schema optimization prompt."""
	return f"""你是数据仓库建模与本体论专家。请根据以下业务文档和当前 Schema 资产，优化实体类、指标、关系和概念。

## 业务文档片段（RAG 检索）
{doc_context}

## 已有本体资产参考（已压缩）
reviewed 中的资产是人工审核确认过的高可信业务口径，只能学习和参考，不得改写、覆盖或生成与其冲突的 Class、Metric、Relationship、Concept。
existing_unreviewed 中的资产是此前提取或优化得到的已有上下文，后续优化应增量更新或合并，不要因为当前批次未覆盖就删除、重建或改名。
{schema_reference}

## 已确定的前置阶段资产
{stage_context}

## 当前批次 Schema 资产
{batch}

## 优化要求
1. {stage_rules}
2. **去重与合并**：不得生成与当前批次、前置阶段资产、reviewed 或 existing_unreviewed 中语义相同或高度相似（同义词、缩写、仅措辞不同）的资产；应复用最合适的已有未审核 ID 并增量修正。
3. **Class 优化**：根据文档修正 name_cn、description，fields 只输出需优化的字段（排除已正确的）。
4. **维度组优化与发现**：根据文档修正业务名称、选项、别名、默认值与 Class 逻辑字段映射；当 dimension_discovery 为 true 时，额外识别当前系统缺失、可被多个 Metric 复用的时间/分类/层级维度组并输出。已有维度组必须保留原 ID；新维度组必须提供稳定英文下划线 ID、至少一个选项和至少一条映射。不允许使用物理字段名替代业务字段，也不得把临时过滤条件建成维度组。
5. **Metric 优化**：根据文档修正 name、description、definition、dimensions、required_dimensions、dimension_group_ids；definition.inputs 中每项均需包含 id、output_name、class_id、source_shape、field、aggregation、filters。long 输入必须有固定 filters。
5. **Relationship 优化**：根据文档补充或修正 source_key/target_key。
6. **Concept 优化**：根据文档补充概念层级，parent_id 只能引用当前或前置阶段已存在的 Concept。
7. **已有资产保护**：如果优化建议与 reviewed 冲突，必须以 reviewed 为准；不得输出 reviewed 的 ID。

## 输出要求
输出标准 JSON，结构如下：
{{
  "classes": [
	{{"id": "原ID", "name_cn": "优化后中文名", "description": "优化后描述", "primary_key": "", "table_name": "", "fields": []}}
  ],
  "relationships": [
	{{"source": "类ID", "target": "类ID", "type": "belongs_to", "source_key": "源键", "target_key": "目标键", "join_key": ""}}
  ],
  "metrics": [
		{{"id": "原ID", "name": "优化后名称", "description": "优化后描述", "category": "", "target_class": "类ID", "definition": {{"version": 1, "anchor_class": "类ID", "expression_operator": "ADD", "inputs": []}}, "dimensions": ["col1"], "required_dimensions": [], "dimension_group_ids": ["time_granularity"], "chart_type": "bar"}}
	],
	"dimension_groups": [
		{{"id": "已有组使用原ID；新组使用英文下划线ID", "name": "时间粒度", "description": "业务说明", "group_type": "time", "is_required": true, "default_option": "month", "clarification_policy": "auto_fill", "options": [{{"value": "month", "label": "按月", "aliases": [], "is_default": true}}], "field_mappings": [{{"option_value": "month", "class_id": "类ID", "field_name": "逻辑字段名", "display_name": "月份", "priority": 0}}]}}
  ],
  "concepts": [
	{{"id": "原ID", "name": "", "description": "", "parent_id": "", "level": 0, "concept_type": "entity", "related_class": ""}}
  ],
  "summary": "本批次优化摘要"
}}

严禁输出 JSON 以外的内容。"""


def build_schema_optimization_retry_prompt(original_prompt: str, error: str, retry_type: str = "syntax") -> str:
	"""Build a corrective retry prompt for invalid optimization output."""
	prompt = f"""{original_prompt}

## 上次输出验证失败
你的上一次输出存在以下错误：
{error}
"""
	if retry_type == "semantic":
		prompt += "\n## 特别提醒（语义错误）\n检测到字段缺失，请确保所有必填字段（如 id、source、target 等）都已完整输出，且值不为空。"
	else:
		prompt += "\n## 特别提醒（格式错误）\n请确保输出是合法的 JSON 格式，注意引号、逗号、括号匹配。"
	return f"{prompt}\n请修正错误并重新输出符合要求的 JSON。"


def build_global_correction_prompt(
	*,
	classes: str,
	metrics: str,
	relationships: str,
	concepts: str,
) -> str:
	"""Build the global consistency review prompt for optimized assets."""
	return f"""你是数据仓库建模专家。请对以下三阶段优化结果进行全局一致性核验。

## 所有 Class 摘要
{classes}

## 所有 Metric 摘要
{metrics}

## 所有 Relationship
{relationships}

## 所有 Concept
{concepts}

## 检查项
1. **去重核验**：是否有重复或近似命名的 Class、Metric、Relationship、Concept？将风险写入 warning，不得直接重命名。
2. **关系悬空**：Relationship 的 source/target 是否引用了不存在的 Class？
3. **指标悬空**：Metric 的 target_class 是否引用了不存在的 Class？
4. **概念树完整性**：Concept 的 parent_id 是否引用了不存在的概念？是否存在循环引用？
5. **指标口径一致性**：同名的 Metric 是否使用了相同的 definition？不一致的需要标记警告。

## 输出 JSON
{{
	"class_renames": [],
	"relationship_corrections": [],
	"metric_corrections": [],
	"concept_corrections": [],
	"metric_consistency_warnings": ["指标 '销售额' 在不同批次中 definition 不一致"],
  "concept_tree_warnings": ["概念 '订单' 的父级 '交易' 不存在"],
  "summary": "全局校正摘要"
}}"""


def build_quality_assessment_prompt(*, diff_summary: str, classes: str, metrics: str) -> str:
	"""Build the LLM-as-judge prompt for optimization quality assessment."""
	return f"""你是一名数据建模质量评审专家。请评估以下 Schema 优化结果的质量。

## 优化摘要
{diff_summary}

## 新增/修改的 Class（前5个）
{classes}

## 新增/修改的 Metric（前5个）
{metrics}

## 评估维度
1. **业务准确性**：优化建议是否贴合业务文档语义？
2. **命名规范性**：名称是否符合业务术语习惯？
3. **结构完整性**：Class-Metric-Relationship 引用关系是否完整？
4. **可执行性**：definition、dimensions 是否合理可执行？

## 输出 JSON
{{
  "overall_score": 8.5,
  "confidence": 0.85,
  "strengths": ["维度划分清晰", "指标口径一致"],
  "weaknesses": ["部分 Class 描述仍偏技术化"],
  "high_risk_items": ["指标 'gmv' 缺少 required_dimensions"],
  "summary": "整体质量较高，建议重点审核 high_risk_items"
}}
"""
