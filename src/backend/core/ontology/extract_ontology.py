"""
Ontology Extractor v6.2 — 分阶段结构化 Metric 提取版
================================================================================
核心重构亮点：
    1. 解决维度盲区：分片时注入 `full_table_catalog`，使度量分片在提取 Class 时具备全表维度的全局视角。
  2. 修复引用悬空：在类合并去重时，动态构建 `class_id_map` 拓扑映射，反向刷新所有指标的 `target_class`。
    3. 第二阶段从已合并的 Class 提取可治理的分析维度组与物理字段映射。
    4. 第三阶段仅基于已合并的全局 Class、维度组和关系线索生成结构化 Metric definition。
    5. 第四阶段在 Metric 与 Relationship 已确定后，构建 `subject_domain / dimension_group / fact_group` 概念层级。
"""

import os
import csv
import json
import re
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
from openai import OpenAI
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

load_dotenv()

from core.llm.chat_model import get_sync_client, get_model_name
from core.ontology.schema_context import build_business_context
from core.ontology.ontology_asset_validator import validate_schema_assets

# ============================================================
# 配置常量
# ============================================================
SAMPLE_ROWS = 2                     # 极限降采样，节省 token
WIDE_TABLE_THRESHOLD = 80           # 触发宽表纵向分片的阈值
SHARD_MAX_COLS = 20                 # 每个分片包含的最大物理列数
MAX_OUTPUT_TOKENS = 16384
MIN_OUTPUT_TOKENS = 4096

# ============================================================
# 数据读取与底层元数据推断
# ============================================================

def read_csv_summary(csv_path: str, sample_rows: int = SAMPLE_ROWS) -> dict:
    rows = []
    total = 0
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or [])
            for i, row in enumerate(reader):
                total += 1
                if i < sample_rows:
                    clean_row = {k: v for k, v in row.items() if v not in (None, "")}
                    rows.append(clean_row)
    except Exception as e:
        return {"error": str(e), "file": csv_path}

    col_types = {col: _infer_column_type(rows, col) for col in columns}

    return {
        "file": os.path.basename(csv_path),
        "columns": columns,
        "column_count": len(columns),
        "column_types": col_types,
        "sample_rows": rows,
        "total_rows": total,
        "is_wide_table": len(columns) > WIDE_TABLE_THRESHOLD,
    }


def _infer_column_type(rows: list, col: str) -> str:
    if not rows:
        return "text"
    numeric_count = date_count = bool_count = 0
    for row in rows:
        val = str(row.get(col, "")).strip()
        if not val:
            continue
        try:
            float(val.replace(",", "").replace("%", ""))
            numeric_count += 1
            continue
        except ValueError:
            pass
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", val):
            date_count += 1
            continue
        if val.lower() in ("true", "false", "是", "否", "yes", "no", "1", "0"):
            bool_count += 1
            continue
    total = len([r for r in rows if str(r.get(col, "")).strip()])
    if total == 0:
        return "text"
    if numeric_count / total > 0.7:
        return "numeric"
    if date_count / total > 0.7:
        return "date"
    if bool_count / total > 0.7:
        return "boolean"
    return "text"


def read_db_summary(db_url: str, sample_rows: int = SAMPLE_ROWS, selected_tables: list[str] = None) -> list[dict]:
    engine = create_engine(db_url)
    inspector = inspect(engine)
    summaries = []
    selected = {str(table).strip() for table in (selected_tables or []) if str(table).strip()}
    with engine.connect() as conn:
        for table in inspector.get_table_names():
            if selected and table not in selected:
                continue
            table_comment = _get_table_comment(inspector, table)
            columns_info = inspector.get_columns(table)
            columns = [c["name"] for c in columns_info]
            col_types = {c["name"]: str(c.get("type", "text")).lower() for c in columns_info}
            col_comments = {
                c["name"]: str(c.get("comment") or "").strip()
                for c in columns_info
            }
            # 基础归一化
            for k, v in col_types.items():
                if "int" in v or "numeric" in v or "decimal" in v or "float" in v or "double" in v:
                    col_types[k] = "numeric"
                elif "date" in v or "time" in v:
                    col_types[k] = "date"
                elif "bool" in v:
                    col_types[k] = "boolean"
                else:
                    col_types[k] = "text"
            try:
                result = conn.execute(text(f'SELECT * FROM "{table}" LIMIT {sample_rows}'))
                rows = [{k: v for k, v in r._mapping.items() if v not in (None, "")} for r in result]
            except Exception:
                result = conn.execute(text(f"SELECT * FROM {table} LIMIT {sample_rows}"))
                rows = [{k: v for k, v in r._mapping.items() if v not in (None, "")} for r in result]
            summaries.append({
                "file": table,
                "table_comment": table_comment,
                "columns": columns,
                "column_count": len(columns),
                "column_types": col_types,
                "column_comments": col_comments,
                "sample_rows": rows,
                "total_rows": -1,
                "is_wide_table": len(columns) > WIDE_TABLE_THRESHOLD,
            })
    engine.dispose()
    return summaries


def _get_table_comment(inspector, table_name: str) -> str:
    try:
        comment_info = inspector.get_table_comment(table_name) or {}
        return str(comment_info.get("text") or "").strip()
    except Exception:
        return ""


# ============================================================
# 宽表智能化分片（修补：挂载全表列目录解耦盲区）
# ============================================================

def split_wide_table_shards(summary: dict, max_cols: int = SHARD_MAX_COLS) -> list[dict]:
    """将宽表切分为多个逻辑分片，并无条件追加全表完整列资产目录（不含样本行）"""
    columns = summary["columns"]
    col_types = summary["column_types"]
    col_comments = summary.get("column_comments", {})
    
    # 抽取全表轻量级元数据目录作只读参考
    full_table_catalog = [
        {
            "physical_name": c,
            "type": t,
            "comment": col_comments.get(c, ""),
        }
        for c, t in col_types.items()
    ]

    if len(columns) <= max_cols:
        base_shard = summary.copy()
        base_shard["full_table_catalog"] = full_table_catalog
        return [base_shard]

    # 识别主键/时间轴作为分片锚点锚定
    pk_candidates = [c for c in columns if "id" in c.lower() or "date" in c.lower() or "编码" in c or "时间" in c]
    pk_cols = pk_candidates[:2] if pk_candidates else [columns[0]]

    numeric_cols = [c for c, t in col_types.items() if t == "numeric" and c not in pk_cols]
    dim_cols = [c for c in columns if c not in pk_cols and c not in numeric_cols]

    shards = []
    # 1. 维度特征分片
    if dim_cols:
        shard_cols = pk_cols + dim_cols[:max_cols - len(pk_cols)]
        shards.append(_make_shard(summary, shard_cols, full_table_catalog))
    
    # 2. 核心度量特征分片
    for i in range(0, len(numeric_cols), max_cols - len(pk_cols)):
        shard_numeric = numeric_cols[i:i + max_cols - len(pk_cols)]
        shard_cols = pk_cols + shard_numeric
        shards.append(_make_shard(summary, shard_cols, full_table_catalog))
    return shards


def _make_shard(original: dict, cols: list[str], full_table_catalog: list) -> dict:
    return {
        "file": original['file'],
        "original_file": original["file"],
        "table_comment": original.get("table_comment", ""),
        "columns": cols,
        "column_count": len(cols),
        "column_types": {c: original["column_types"].get(c, "text") for c in cols},
        "column_comments": {c: original.get("column_comments", {}).get(c, "") for c in cols},
        "sample_rows": [{c: r.get(c, "") for c in cols} for r in original["sample_rows"]],
        "total_rows": original["total_rows"],
        "is_wide_table": False,
        "is_shard": True,
        "full_table_catalog": full_table_catalog  # 核心修复：注入全表视角，扫除模型指标抽取盲区
    }


# ============================================================
# 健壮性工具函数（JSON 解析与修复）
# ============================================================

def _safe_json_loads(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    repaired = _repair_truncated_json(text)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    return None


def _repair_truncated_json(text: str) -> Optional[str]:
    brace_count = bracket_count = 0
    in_string = escape = False
    last_valid_pos = 0
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                last_valid_pos = i + 1
        elif ch == "[":
            bracket_count += 1
        elif ch == "]":
            bracket_count -= 1
    if last_valid_pos > 0:
        return text[:last_valid_pos]
    partial = text[:text.rfind("}") + 1] if "}" in text else text
    open_brackets = partial.count("[") - partial.count("]")
    open_braces = partial.count("{") - partial.count("}")
    repaired = re.sub(r",\s*$", "", partial)
    repaired += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
    return repaired


def _calc_max_tokens(input_text: str) -> int:
    input_tokens = len(input_text) * 2
    return max(MIN_OUTPUT_TOKENS, min(MAX_OUTPUT_TOKENS, int(input_tokens * 1.5)))


# ============================================================
# 提示词矩阵（重构：深度融入树状层次与全表透视）
# ============================================================

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
    "csv_file": "当前分片的 CSV 文件名或者数据库表名（如：sales.csv 或 t_person_level_kpis）",
    "fields": [
      {{
        "name": "字段业务逻辑中文名（如：销售金额、生产批次号）",
        "physical_name": "物理列名（必须与输入的物理列名完全一致）",
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
    - `class_id` 必须来自上方 Class ID；`inputs[].field` 与 `inputs[].filters[].field` 必须使用该 Class 的**物理字段名**（`physical_name`）；`dimensions`、`required_dimensions` 使用逻辑字段名。不可臆造字段。
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


# ============================================================
# v6.1 核心全闭环提取器
# ============================================================

class OntologyExtractor:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.model = model or os.getenv("MODEL_NAME", "qwen-plus")
        if api_key and base_url:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = get_sync_client()
        self.model = model or get_model_name()

    def run(
        self,
        input_dir: str = "",
        output_dir: str = "",
        business_name: str = "",
        on_progress: Optional[Callable] = None,
        db_connection_url: str = "",
        selected_files: list[str] = None,
        selected_tables: list[str] = None,
        reviewed_schema_context: dict | str | None = None,
    ) -> dict:
        business_context = build_business_context(business_name, reviewed_schema_context)
        # ---- Step 1: 扫描物理媒介元数据摘要 ----
        if db_connection_url:
            summaries = read_db_summary(db_connection_url, selected_tables=selected_tables or [])
        elif input_dir:
            summaries = []
            selected = {str(file_name).strip() for file_name in (selected_files or []) if str(file_name).strip()}
            for f in sorted(Path(input_dir).glob("*.csv")):
                if selected and f.name not in selected:
                    continue
                s = read_csv_summary(str(f))
                if "error" not in s:
                    summaries.append(s)
        else:
            return {"error": "必须指定 input_dir 或 db_connection_url"}

        if not summaries:
            return {"error": "未找到任何可用数据文件或表"}

        dataset_catalog = self._build_dataset_catalog(summaries)
        for summary in summaries:
            summary["dataset_catalog"] = dataset_catalog

        # ---- Step 2: 宽表分片安全拆解 ----
        all_shards = []
        for s in summaries:
            shards = split_wide_table_shards(s)
            all_shards.extend(shards)

        total_shards = len(all_shards)
        if on_progress:
            on_progress("init", total_shards, total_shards, f"元数据深度治理完毕，拆分为 {total_shards} 个标准算力单元进行流式提取")

        # ---- Phase 1: 流式微观单表提取 Class ----
        raw_classes = []
        for idx, shard in enumerate(all_shards):
            print(f"[Phase 1] Extract From {idx+1} To {len(all_shards)}")
            table_name = shard.get("file", f"shard_{idx}")
            if on_progress:
                on_progress("phase1", idx + 1, total_shards, f"正在进行微观特征提取: {table_name}")

            result = self._extract_single_table(shard, business_context)
            if "class" in result:
                # 留存原始血缘用于物理 Mapping
                result["class"]["_source_origin"] = shard.get("original_file", shard.get("file"))
                result["class"]["_temp_shard_id"] = result["class"].get("id")  # 留存分片提取时的临时 ID
                raw_classes.append(result["class"])
        # ---- Step 3: 核心重构：智能合并、跨表语义去重与拓扑映射追踪 ----
        merged_classes, _ = self._merge_and_track_classes(raw_classes)

        # ---- Step 4: 物理字段覆盖校验，补齐 LLM 遗漏的底层表/CSV 字段 ----
        self._ensure_class_fields_coverage(merged_classes, summaries)

        # ---- Phase 2: 在 Metric 之前提取可治理的业务维度组 ----
        if on_progress:
            on_progress("phase2", 1, 1, "正在基于全局实体和字段提取分析维度组...")

        dimension_result = self._extract_dimension_groups(
            merged_classes,
            dataset_catalog,
            business_context,
        )
        dimension_groups = dimension_result.get("dimension_groups", [])

        # ---- Phase 3: 基于全局 Class 和维度组生成结构化 Metrics 和 Relationships ----
        if on_progress:
            on_progress("phase3", 1, 1, "正在基于全局实体、维度组定义结构化指标与关联关系...")

        global_semantics = self._extract_global_metrics_and_relationships(
            merged_classes,
            dataset_catalog,
            business_context,
            dimension_groups,
        )
        relationships = global_semantics.get("relationships", [])
        all_metrics = [
            self._normalize_metric(metric)
            for metric in global_semantics.get("metrics", [])
            if isinstance(metric, dict)
        ]

        # ---- Phase 4: 基于已确定的 Class、Relationship、Metric 构建概念层级 ----
        if on_progress:
            on_progress("phase4", 1, 1, "正在根据实体、维度组、关联与指标构建多级概念层级...")
        concept_result = self._extract_concept_hierarchy(
            merged_classes,
            relationships,
            all_metrics,
            business_context,
        )
        concepts = concept_result.get("concepts", [])

        # 概念完整性覆盖保障
        concepts = self._ensure_tree_concepts_coverage(merged_classes, concepts)

        # 全方位资产拼装，并在写盘前执行物理血缘与引用一致性治理
        final_schema = self._validate_schema_assets({
            "business_name": business_name,
            "classes": merged_classes,
            "relationships": relationships,
            "dimension_groups": dimension_groups,
            "metrics": all_metrics,
            "concepts": concepts,
        }, summaries)
        print(
            "Extact Summary: "
            f"{len(final_schema.get('classes', []))} classes, "
            f"{len(final_schema.get('relationships', []))} relationships, , "
            f"{len(final_schema.get('dimension_groups', []))} dimension groups, "
            f"{len(final_schema.get('metrics', []))} metrics, , "
            f"{len(final_schema.get('concepts', []))} concepts"
        )

        # 持久化输出
        if output_dir:
            self._save_assets(final_schema, summaries, output_dir)

        return {
            "status": "success",
            "tables_processed": len(summaries),
            "schema": final_schema,
        }

    def _extract_single_table(self, table_meta: dict, business_name: str) -> dict:
        prompt = PHASE1_TABLE_PROMPT.format(
            business_name=business_name,
            table_summary=json.dumps(table_meta, ensure_ascii=False, indent=2)
        )
        result = self._call_llm_json(prompt, _calc_max_tokens(prompt))
        return result if result else {}

    def _build_dataset_catalog(self, summaries: list[dict]) -> list[dict]:
        catalog = []
        for summary in summaries:
            columns = []
            column_comments = summary.get("column_comments", {}) or {}
            column_types = summary.get("column_types", {}) or {}
            for column in summary.get("columns", []):
                columns.append({
                    "name": column,
                    "type": column_types.get(column, "text"),
                    "comment": column_comments.get(column, ""),
                })
            catalog.append({
                "table": summary.get("file", ""),
                "table_comment": summary.get("table_comment", ""),
                "total_rows": summary.get("total_rows", -1),
                "columns": columns,
            })
        return catalog

    def _compact_classes_for_global_prompt(self, classes: list) -> list[dict]:
        compressed = []
        for c in classes:
            compressed.append({
                "id": c.get("id"),
                "name_cn": c.get("name_cn"),
                "description": c.get("description"),
                "_source_origin": c.get("_source_origin"),
                "fields": [
                    {
                        "physical_name": f.get("physical_name"),
                        "name": f.get("name"),
                        "type": f.get("type"),
                        "is_primary_key": f.get("is_primary_key", False),
                        "is_foreign_key": f.get("is_foreign_key", False)
                    } for f in c.get("fields", [])
                ]
            })
        return compressed

    def _extract_dimension_groups(self, classes: list, dataset_catalog: list, business_name: str) -> dict:
        """Phase 2: derive reusable business dimensions before Metric generation."""
        prompt = PHASE2_DIMENSION_GROUP_PROMPT.format(
            business_name=business_name,
            classes_summary=json.dumps(self._compact_classes_for_global_prompt(classes), ensure_ascii=False, indent=2),
            dataset_catalog=json.dumps(dataset_catalog, ensure_ascii=False, indent=2),
        )
        result = self._call_llm_json(prompt, _calc_max_tokens(prompt))
        return result if result else {"dimension_groups": []}

    def _extract_global_metrics_and_relationships(self, classes: list, dataset_catalog: list, business_name: str, dimension_groups: list) -> dict:
        """Phase 3: derive governed structured Metrics after dimensions and Class IDs are final."""
        prompt = PHASE3_GLOBAL_PROMPT.format(
            business_name=business_name,
            classes_summary=json.dumps(self._compact_classes_for_global_prompt(classes), ensure_ascii=False, indent=2),
            dataset_catalog=json.dumps(dataset_catalog, ensure_ascii=False, indent=2),
            dimension_groups_summary=json.dumps(dimension_groups, ensure_ascii=False, indent=2),
        )
        result = self._call_llm_json(prompt, _calc_max_tokens(prompt))
        return result if result else {"relationships": [], "metrics": []}

    def _extract_concept_hierarchy(self, classes: list, relationships: list, metrics: list, business_name: str) -> dict:
        """Phase 3: create Concepts only after semantic assets are fully determined."""
        metric_summary = [
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "category": m.get("category"),
                "target_class": m.get("target_class"),
                "dimensions": m.get("dimensions"),
                "required_dimensions": m.get("required_dimensions"),
                    "dimension_group_ids": m.get("dimension_group_ids", []),
                "definition": m.get("definition"),
            }
            for m in metrics
        ]
        prompt = PHASE3_CONCEPT_PROMPT.format(
            business_name=business_name,
            classes_summary=json.dumps(self._compact_classes_for_global_prompt(classes), ensure_ascii=False, indent=2),
            relationships_summary=json.dumps(relationships, ensure_ascii=False, indent=2),
            metrics_summary=json.dumps(metric_summary, ensure_ascii=False, indent=2),
        )
        result = self._call_llm_json(prompt, _calc_max_tokens(prompt))
        return result if result else {"concepts": []}

    # ------------------------------------------------------------
    # 核心重构：不仅做合并去重，更建立起旧 ID 到新 ID 的强力映射矩阵
    # ------------------------------------------------------------
    def _merge_and_track_classes(self, raw_classes: list[dict]) -> tuple[list[dict], dict[str, str]]:
        """按物理来源归一化分片，并执行跨表语义去重，全程记录 Class ID 映射链路"""
        class_id_map = {}  # 旧临时 ID -> 最终归一化 ID 的重映射矩阵
        
        # 1. 物理血缘归一（将同一张表的多个分片 Class 揉碎合并）
        by_origin = {}
        for c in raw_classes:
            origin = c.get("_source_origin", "")
            temp_id = c.get("_temp_shard_id", "")
            
            if origin not in by_origin:
                by_origin[origin] = c
                class_id_map[temp_id] = c.get("id")
            else:
                target_primary = by_origin[origin]
                # 记录重映射线索
                class_id_map[temp_id] = target_primary.get("id")
                
                # 融合物理字段集
                exist_fields = {f["physical_name"]: f for f in target_primary.get("fields", [])}
                new_fields = {f["physical_name"]: f for f in c.get("fields", [])}
                exist_fields.update(new_fields)
                target_primary["fields"] = list(exist_fields.values())
                
                # 竞争合并更长的业务描述
                if c.get("description") and len(c.get("description", "")) > len(target_primary.get("description", "")):
                    target_primary["description"] = c["description"]

        merged_list = list(by_origin.values())

        # 2. 跨表高级语义去重（基于相似度对齐）
        deduped = {}
        for c in merged_list:
            cid = c.get("id", "")
            current_primary_id = class_id_map.get(c.get("_temp_shard_id"), cid)
            origin = c.get("_source_origin", "")
            
            found = False
            for target_key in list(deduped.keys()):
                # 模糊剔除复数及大小写异同带来的冗余 Class
                if target_key.lower() == cid.lower() or target_key.lower().rstrip('s') == cid.lower().rstrip('s'):
                    exist = deduped[target_key]
                    if exist.get("_source_origin") != origin:
                        unique_id = self._unique_class_id(cid, origin, deduped)
                        c["id"] = unique_id
                        class_id_map[c.get("_temp_shard_id")] = unique_id
                        class_id_map[current_primary_id] = unique_id
                        deduped[unique_id] = c
                        found = True
                        break
                    
                    # 建立二级映射映射追随关系
                    class_id_map[c.get("_temp_shard_id")] = target_key
                    class_id_map[current_primary_id] = target_key
                    
                    # 合并字段资产
                    exist_fields = {f["physical_name"]: f for f in exist.get("fields", [])}
                    new_fields = {f["physical_name"]: f for f in c.get("fields", [])}
                    exist_fields.update(new_fields)
                    exist["fields"] = list(exist_fields.values())
                    
                    if c.get("description") and len(c.get("description", "")) > len(exist.get("description", "")):
                        exist["description"] = c["description"]
                    found = True
                    break
            if not found:
                deduped[cid] = c
                
        return list(deduped.values()), class_id_map

    def _unique_class_id(self, class_id: str, origin: str, existing: dict) -> str:
        stem = re.sub(r"\.csv$", "", Path(origin or class_id).name, flags=re.IGNORECASE)
        suffix = "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", stem) if part)[:40]
        candidate = f"{class_id}{suffix}" if suffix and suffix.lower() not in class_id.lower() else f"{class_id}Source"
        base = candidate
        index = 2
        while candidate in existing:
            candidate = f"{base}{index}"
            index += 1
        return candidate

    def _ensure_class_fields_coverage(self, classes: list[dict], summaries: list[dict]) -> None:
        """对照原始表/CSV 元数据，补齐 Class.fields 中被模型遗漏的物理字段。"""
        summary_index = self._build_summary_index(summaries)
        for cls in classes:
            source = self._resolve_class_source(cls)
            summary = summary_index.get(source) or summary_index.get(source.lower())
            if not summary:
                continue

            existing_fields = cls.get("fields", [])
            if not isinstance(existing_fields, list):
                existing_fields = []

            existing_physical_names = {
                str(f.get("physical_name") or f.get("name") or "").strip()
                for f in existing_fields
                if isinstance(f, dict)
            }
            existing_physical_names = {name for name in existing_physical_names if name}

            missing_fields = []
            for column in summary.get("columns", []):
                column_name = str(column).strip()
                if not column_name or column_name in existing_physical_names:
                    continue
                missing_fields.append(self._build_missing_field(cls, summary, column_name))
                existing_physical_names.add(column_name)

            print(f"missing_fields = {missing_fields}")

            if missing_fields:
                cls["fields"] = existing_fields + missing_fields

    def _build_summary_index(self, summaries: list[dict]) -> dict[str, dict]:
        summary_index = {}
        for summary in summaries:
            file_name = str(summary.get("file", "")).strip()
            if not file_name:
                continue
            keys = {file_name, file_name.lower(), Path(file_name).name, Path(file_name).name.lower()}
            if file_name.endswith(".csv"):
                stem = file_name[:-4]
                keys.update({stem, stem.lower(), Path(stem).name, Path(stem).name.lower()})
            for key in keys:
                if key:
                    summary_index[key] = summary
        return summary_index

    def _resolve_class_source(self, cls: dict) -> str:
        for key in ("_source_origin", "csv_file", "table_name"):
            value = str(cls.get(key, "")).strip()
            if value:
                return value
        return ""

    def _build_missing_field(self, cls: dict, summary: dict, column_name: str) -> dict:
        column_comments = summary.get("column_comments", {}) or {}
        column_types = summary.get("column_types", {}) or {}
        comment = str(column_comments.get(column_name, "")).strip()
        primary_key = str(cls.get("primary_key", "")).strip()
        primary_key_parts = {part.strip() for part in primary_key.split(",") if part.strip()}
        return {
            "name": comment or column_name,
            "physical_name": column_name,
            "type": column_types.get(column_name, "text"),
            "description": comment or "底层数据源字段，模型初始提取遗漏后自动补齐",
            "is_primary_key": column_name in primary_key_parts,
            "is_foreign_key": False,
        }

    def _validate_schema_assets(self, schema: dict, summaries: list[dict]) -> dict:
        """丢弃不具备真实物理表/字段支撑的 Class、Relationship、Metric 与 Concept。"""
        return validate_schema_assets(schema, summaries, self._ensure_tree_concepts_coverage)

    def _normalize_metric(self, m: dict) -> dict:
        definition = m.get("definition", {})
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except json.JSONDecodeError:
                definition = {}
        if not isinstance(definition, dict):
            definition = {}
        dimensions = m.get("dimensions", [])
        if isinstance(dimensions, str):
            try:
                dimensions = json.loads(dimensions)
            except json.JSONDecodeError:
                dimensions = [d.strip() for d in dimensions.split(",") if d.strip()]
                
        required_dimensions = m.get("required_dimensions", [])
        if isinstance(required_dimensions, str):
            try:
                required_dimensions = json.loads(required_dimensions)
            except json.JSONDecodeError:
                required_dimensions = [d.strip() for d in required_dimensions.split(",") if d.strip()]

        dimension_group_ids = m.get("dimension_group_ids", [])
        if isinstance(dimension_group_ids, str):
            try:
                dimension_group_ids = json.loads(dimension_group_ids)
            except json.JSONDecodeError:
                dimension_group_ids = [group_id.strip() for group_id in dimension_group_ids.split(",") if group_id.strip()]
        if not isinstance(dimension_group_ids, list):
            dimension_group_ids = []
                
        return {
            "id": m.get("id", "unknown_metric"),
            "name": m.get("name", m.get("name_cn", "")),
            "description": m.get("description", ""),
            "category": m.get("category", ""),
            "target_class": definition.get("anchor_class", m.get("target_class", "")),
            "definition": definition,
            "dimensions": json.dumps(dimensions, ensure_ascii=False),
            "required_dimensions": json.dumps(required_dimensions, ensure_ascii=False),
            "dimension_group_ids": [str(group_id).strip() for group_id in dimension_group_ids if str(group_id).strip()],
            "chart_type": m.get("chart_type", "bar"),
        }

    # ------------------------------------------------------------
    # 概念覆盖树状升级兜底
    # ------------------------------------------------------------
    def _ensure_tree_concepts_coverage(self, classes: list, concepts: list) -> list:
        """强化修复：确保每一个 Class 都在树状概念网络中拥有合理的隶属身份"""
        # 提取当前所有已分配的 Class 映射
        mapped_classes = {c["related_class"] for c in concepts if c.get("related_class")}
        
        # 扫描确认系统中是否具备至少一个顶级主题域作为骨架
        has_domain = any(c.get("concept_type") == "subject_domain" for c in concepts)
        if not has_domain:
            concepts.append({
                "id": "global_auto_domain",
                "name": "业务综合主题域",
                "description": "系统自动收拢并合并生成的顶层业务抽象主域",
                "parent_id": "",
                "level": 1,
                "concept_type": "subject_domain",
                "related_class": ""
            })
            
        target_parent_domain = [c["id"] for c in concepts if c.get("concept_type") == "subject_domain"][0]

        # 扫描系统中的二级组容器
        has_group = any(c.get("concept_type") == "dimension_group" for c in concepts if c.get("parent_id") == target_parent_domain)
        if not has_group:
            concepts.append({
                "id": "default_dimension_group",
                "name": "核心数据维度资产组",
                "description": "自动对齐构建的统一实体及度量归位容器",
                "parent_id": target_parent_domain,
                "level": 2,
                "concept_type": "dimension_group",
                "related_class": ""
            })
            
        target_group = [c["id"] for c in concepts if c.get("concept_type") == "dimension_group" and c.get("parent_id") == target_parent_domain][0]

        # 为落单的 Class 建立标准树形三级类目节点
        for cls in classes:
            cid = cls.get("id")
            if cid and cid not in mapped_classes:
                concepts.append({
                    "id": f"{cid.lower()}_tree_node",
                    "name": f"{cls.get('name_cn', cid)}分析视图",
                    "description": f"基于实体类 {cid} 的相关衍生多维业务指标全景图谱",
                    "parent_id": target_group,
                    "level": 3,
                    "concept_type": "dimension_group",
                    "related_class": cid,
                })
        return concepts

    def _save_assets(self, schema: dict, summaries: list, output_dir: str):
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "schema.json", "w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)

        mapping = {"classes": {}, "relationships": schema.get("relationships", [])}
        file_to_table = {s["file"].split("#")[0]: s["file"].split("#")[0].replace(".csv", "").lower() for s in summaries}
        summary_index = self._build_summary_index(summaries)
        
        for c in schema.get("classes", []):
            cid = c.get("id")
            origin = c.get("_source_origin", "")
            summary = summary_index.get(origin) or summary_index.get(origin.lower()) or {}
            physical_columns = {str(col) for col in summary.get("columns", [])}
            fields = [
                f for f in c.get("fields", [])
                if not physical_columns or f.get("physical_name") in physical_columns
            ]
            mapping["classes"][cid] = {
                "csv_file": origin if origin.endswith(".csv") else "",
                "table_name": file_to_table.get(origin, origin.lower()),
                "primary_key": ",".join([f["physical_name"] for f in fields if f.get("is_primary_key")]),
                "name_cn": c.get("name_cn", ""),
                "field_map": {f["name"]: f["physical_name"] for f in fields},
                "field_types": {f["physical_name"]: f["type"] for f in fields},
                "data_source": "csv" if origin.endswith(".csv") else "database",
            }
        with open(out / "schema_mapping.json", "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"-> [成功] 高内聚本体模型及 schema_mapping.json 纽带配置文件生成完毕。")

    def _call_llm_json(self, prompt: str, max_tokens: int, retry: int = 2) -> Optional[dict]:
        for attempt in range(retry + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一个极其严谨的语义建模与大宽表治理专家。只输出合法且未遭遇截断的标准 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                text = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason

                result = _safe_json_loads(text)
                if result:
                    return result

                if finish_reason == "length" and attempt < retry:
                    max_tokens = min(max_tokens * 2, 16384)
                    continue

                if attempt == retry:
                    repaired = _repair_truncated_json(text)
                    if repaired:
                        try:
                            return json.loads(repaired)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                if attempt == retry:
                    print(f"  [Error] LLM 彻底调用失败: {e}")
        return None

# ============================================================
# 统一入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Ontology Extractor v6.1 (全闭环生产级修复版)")
    parser.add_argument("--input", default="", help="CSV 目录路径")
    parser.add_argument("--output", required=True, help="输出模型资产目录")
    parser.add_argument("--business", required=True, help="业务线名称/场景上下文")
    parser.add_argument("--db-url", default="", help="SQLAlchemy 数据库连接串")
    args = parser.parse_args()

    if not args.input and not args.db_url:
        parser.error("必须提供 --input 或 --db-url 其中之一")

    extractor = OntologyExtractor()
    result = extractor.run(
        input_dir=args.input,
        output_dir=args.output,
        business_name=args.business,
        db_connection_url=args.db_url,
        on_progress=lambda phase, cur, tot, msg: print(f"  [{phase.upper()}] {cur}/{tot} -> {msg}")
    )


if __name__ == "__main__":
    main()
