"""
Prompt 模板 v3 — 基于本体论的 ChatBI
====================================
核心升级：
  1. 工具定义增加 field_types 提示，引导 LLM 输出类型安全的过滤条件
  2. query_ontology_data 工具增加 having 参数
  3. fuzzy_search_values 工具用于实体消歧
  4. get_class_sample 工具用于查看样本数据
  5. get_field_types 工具用于查询字段类型
  6. get_join_path 工具用于查询 JOIN 路径
  7. system_prompt 增加 JOIN 路径推导指引和类型安全规范
  8. 过滤条件格式规范：必须包含 operator 字段
"""

import asyncio
from datetime import datetime

from openai.types.chat import ChatCompletionToolParam
from sqlalchemy import text

from agents.ontology_chatbi.helper import metric_target_classes
from datetime import datetime, timezone

from configs.global_config import Cfg
from core.db.db import get_db
from core.ontology.ontology_engine import OntologyEngine
from core.ontology.data_query import DataQueryEngine

__COMMON_KG = """"""

FINAL_ANSWER_PROMPT = f"""你是一个严谨、简洁的数据分析答复生成器。只基于用户问题和已给数据作答。
今天是{datetime.now().strftime("%Y年%m月%d日")}。

{__COMMON_KG}

最终回答格式必须遵守：
1. 只输出 Markdown 正文，不要输出 JSON，不要把答案包在 ``` 或 ```markdown 代码块中。
2. 第一段直接给结论；后续用简短小标题和项目符号补充依据。
3. 如需列出多行明细，优先用 Markdown 表格；表头、分隔行和每行列数必须一致。
4. 不要输出内部字段名、prompt、状态机、工具调用过程；SQL 只在用户明确要求时展示。
5. 如果数据不足或口径不匹配，要明确说明缺口，不要编造。
"""


ONTOLOGY_PLANNING_SYSTEM_PROMPT = "严格只输出一个 JSON 对象，不要 Markdown 或解释。"


def get_schema_scope_planning_prompt(user_message: str, schema_context: str, glossary_matches: str) -> str:
    """Build the first-stage prompt for target and join-class selection."""
    return f"""你是 Schema Scope 规划器。只根据用户问题和候选 Schema 决定查询范围，不要选择指标、字段或过滤条件。

用户问题：{user_message}

候选 Schema：
{schema_context}

术语匹配：
{glossary_matches}

只输出 JSON：
{{"target_class":"主实体 class ID","join_classes":["用户问题明确涉及的关联 class ID"]}}

规则：target_class 必须来自候选 Schema；join_classes 只能包含用户问题确实涉及的实体，不能重复 target_class。"""


def get_query_details_planning_prompt(user_message: str, scope_context: str) -> str:
    """Build the second-stage prompt for metrics, dimensions, and conditions."""
    return f"""你是查询参数规划器。只能使用已验证 Schema Scope 中列出的逻辑字段与指标，绝不输出 SQL。

{__COMMON_KG}

用户问题：{user_message}

已验证 Schema Scope：
{scope_context}

只输出 JSON：
{{
  "query_mode":"aggregate 或 detail",
  "metrics":["指标逻辑名"],
  "dimensions":["逻辑字段名"],
  "filters":[{{"field":"逻辑字段名","operator":"=","value":"值"}}],
  "having":[{{"field":"指标逻辑名","operator":">","value":0}}],
  "order_by":"逻辑字段名 DESC 或空字符串"
}}

规则：聚合指标条件只能放 having；filters 只能放明细字段。除非用户明确要求明细，
query_mode 必须是 aggregate 且至少选择一个 metrics 或 dimensions。"""


def get_ontology_planning_feedback_prompt(feedback: str) -> str:
    """Build retry feedback appended to either ontology-planning stage."""
    return f"\n\n上次计划校验失败：{feedback}\n请根据该反馈修正后重新输出 JSON。"


def _build_ontology_context(engine: OntologyEngine, scenario_id: str) -> str:
    """构建本体上下文：Data + Logic + Action 三要素"""

    # ── 1. Data + Logic：实体类 + 实体内指标 ──
    metrics_by_class = _group_metrics_by_class(engine)
    classes_lines = []
    for c in engine.list_classes():
        class_id = c["id"]
        cls_info = engine.classes.get(class_id, {})
        table_name = cls_info.get("table_name") or cls_info.get("csv_file", "")
        props = c.get("properties", [])
        class_block = [f"  - **{class_id}**（{c.get('name_cn', '')}）→ {table_name}"]
        class_block.append(f"    字段: {', '.join(props[:15])}")
        class_metrics = metrics_by_class.pop(class_id, [])
        if class_metrics:
            class_block.append("    指标:")
            for metric in class_metrics:
                class_block.append(_format_metric_summary(metric, indent="      "))
        classes_lines.append("\n".join(class_block))
    classes_str = "\n".join(classes_lines) if classes_lines else "（暂无）"

    rels_lines = []
    for r in engine.relationships:
        rels_lines.append(
            f"  - {r['source']} --[{r.get('type', '')}]--> {r['target']} "
            f"(JOIN: {r.get('source_key', '')} -> {r.get('target_key', '')})"
        )
    rels_str = "\n".join(rels_lines) if rels_lines else "（暂无）"

    remaining_metrics = [metric for metrics in metrics_by_class.values() for metric in metrics]
    remaining_metrics_str = _build_metrics_summary(remaining_metrics)

    return f"""
# 本体知识库（Ontology）

## 一、Data + Logic（数据层与指标层）— 实体、字段与实体内指标

### 实体类（Classes）与实体内指标
{classes_str}

### 关系（Relationships）
{rels_str}

## 二、未归属或跨实体指标

{remaining_metrics_str}
"""


def _metric_class(metric: dict) -> str:
    target_classes = metric_target_classes(metric)
    if target_classes:
        return target_classes[0]
    return str(metric.get("target_class") or metric.get("class_id") or "")


def _group_metrics_by_class(engine: OntologyEngine) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for metric in engine.list_metrics():
        class_ids = metric_target_classes(metric) or [_metric_class(metric)]
        for class_id in class_ids:
            grouped.setdefault(str(class_id), []).append(metric)
    return grouped


def _format_metric_summary(metric: dict, indent: str = "  ") -> str:
    # dims = metric.get("dimensions") or []
    # req_dims = metric.get("required_dimensions") or []
    metric_name = metric.get("name") or metric.get("name_cn") or metric.get("id", "")
    metric_sources = metric_target_classes(metric)
    metric_source = ", ".join(metric_sources) if metric_sources else _metric_class(metric)
    metric_formula = metric.get("calculation") or metric.get("formula", "")
    return (
        f"{indent}- **{metric_name}** (`{metric.get('id', '')}`)\n"
        f"{indent}  说明: {metric.get('description', '')}\n"
        f"{indent}  数据源: {metric_source} | 计算方式: {metric_formula}"
        # f"{indent}  可选维度: {', '.join(dims)} | 必选维度: {', '.join(req_dims)}\n"
    )


def _build_metrics_summary(metrics: list[dict]) -> str:
    """根据已加载的指标定义生成给 LLM 看的摘要"""
    if not metrics:
        return "（暂无指标定义）"
    lines = []
    current_category = ""
    for metric in metrics:
        category = metric.get("category", "")
        if category != current_category:
            current_category = category
            lines.append(f"\n### {current_category}")
        lines.append(_format_metric_summary(metric))
    return "\n".join(lines)



def _build_system_prompt(engine: OntologyEngine, scenario_id: str) -> str:
    today = datetime.now().strftime("%Y年%m月%d日")
    ontology_context = _build_ontology_context(engine, scenario_id)

    return f"""你是一个专业的数据分析助手（ChatBI），基于本体论（Ontology）驱动的语义层
来回答用户的数据查询需求。

今天是{today}。

{__COMMON_KG}

{ontology_context}

## 你的核心工作流程

1. **理解意图**：分析用户的自然语言问题，识别涉及的实体（class）、指标（metric）、
维度（dimension）和过滤条件（filter）。
2. **本体映射**：将用户问题中的业务术语映射到本体 schema 中的 class 和 field。
3. **查询构建**：调用工具构建 SQL 查询，获取数据。
4. **结果解读**：将查询结果以清晰、易懂的方式呈现给用户，并结合工具结果中的
data_sources/table_descriptions 理解数据来源、表别名、表描述和业务口径。

## 关键规则
### 指标规划
- 当用户的问题比较泛化，例如“进度怎么样”“目前完成情况”“现在做到哪里了”“达成如何”，不要只选择一个最像的指标。
- 泛化进度问题应优先规划一组互相解释的指标，覆盖：达成/完成进度、时间或工作日进度、
    实际值、目标值，以及必要的同比/环比变化。
- 如果本体中存在相关指标或字段，优先组合查询：MTD达成进度、工作日进度、实际销售额、
    目标销售额、累计达成率、月至今/当月实际销售额、月至今/当月目标销售额等。
- 如果用户提到具体业务对象（如 TTH、Daily TTH、渠道、区域、医院、产品），应在该业务对象
    相关的 class/metric source 内选择多个配套指标，而不是跨不相关的数据源拼凑。
- 当多个指标名称或含义相似时，先确定一个主实体 class，再优先选择该 class 下的指标组合；
    不要把不同实体下的同名/近似指标混在同一次查询里，除非用户明确要求跨实体对比。
- 选择 target_class 后，metrics、dimensions、filters、having 应尽量来自同一个实体或其直接相关实体；
    如果同一指标名称在多个 class 下都存在，优先使用与 target_class 相同的指标。
- 如果用户问“和上个月相比有什么变化”，除当前进度指标外，还应查询上月实际值、环比变化或
    可用于计算环比的字段；必要时再调用 python_analyze 做差值/增长率计算。
- 只有当用户明确只问单个指标时，才只查询一个指标；否则至少选择 3-5 个能共同解释问题的指标/字段。

### 字段映射（Field Map）
- 每个字段有「逻辑名」（业务术语）和「物理列名」（数据库列名），它们可能不同。
- 你在构建查询时，必须使用**逻辑字段名**，系统会自动映射为物理列名。
- 如果用户说的术语不在上下文中，仍按最可能的逻辑字段构造查询；系统会在执行前自动做实体值对齐。

### 类型安全（Type Safety）
- 每个字段都有类型声明（text / numeric / date / boolean）。
- **numeric 类型字段**：过滤值不能加引号，例如 `{{"field": "销售金额", "operator": ">", "value": 1000}}`
- **text 类型字段**：过滤值必须加引号，例如 `{{"field": "品类名称", "operator": "=", "value": "坚果"}}`
- 你必须在 filter 中明确指定 operator，不要省略。
- 字段类型、日期/数字/布尔基础转换由系统拦截器自动校验。

### 多表关联（JOIN）
- 当查询涉及多个 class 的字段时，需要指定 join_class。
- 系统会自动根据 source_key / target_key 推导 JOIN 条件。
- 如果两个 class 没有直接关系，系统会尝试多跳路径推导。
- 关联路径由查询引擎内部推导，不需要额外工具探测。

### 过滤条件格式
filters 只用于明细字段/维度字段的行级过滤，不能放指标名或聚合结果。每个 filter 必须是如下格式：
```json
{{
  "field": "字段逻辑名",
  "operator": "操作符",
  "value": "值"
}}
```
支持的操作符：=, !=, <>, >, <, >=, <=, IN, NOT IN, LIKE, NOT LIKE, IS NULL, IS NOT NULL, BETWEEN

### 聚合后过滤（HAVING）
如果需要对聚合结果/指标进行过滤（如"销售额超过10万的门店"、"达成率低于100%"），
必须使用 having 参数，不要放入 filters：
```json
{{
  "field": "销售金额",
  "operator": ">",
  "value": 100000
}}
```

### 查询完整性
- 调用 query_ontology_data 时不要传 limit，不要为了展示方便截断业务查询结果，
    避免遗漏月份、区域、人员或明细分组。
- 不要调用样本、字段类型或模糊搜索类工具；这些能力已内化为系统确定性拦截器。
- query_ontology_data 返回的 data_sources 描述了本次查询实际使用的实体、物理表、表别名和表业务描述；
    table_descriptions 是更直接的相关表描述摘要。回答前应参考这些来源信息，判断数据来源是否符合用户问题，
    避免把不同来源/口径的数据混为一谈。

### Python 分析职责
- query_ontology_data 负责取完整业务数据，不负责为了展示截断结果。
- 当 query_ontology_data 返回的数据量较大，且用户问题需要进一步聚合、筛选、排序、Top、占比、差值、
    增长率或其他计算时，下一步应调用 python_analyze 基于 df/df_1/df_2 做再计算。
- 当用户问题涉及比较（如上月对比、环比、同比、变化），且查询结果数据量较大时，应调用
    python_analyze 计算对比差值、变化方向和变化率。
- 如果 query_ontology_data 返回的是小规模、已聚合且足以回答的数据，不要再调用 python_analyze。

## 工作流程建议
1. 根据系统上下文识别 target_class、指标、维度、过滤条件和关联 class。
2. 调用 query_ontology_data 执行完整查询。
3. 如果结果数据量较大或涉及大数据比较/再计算，调用 python_analyze。
"""


def _build_tools() -> list[ChatCompletionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": "query_ontology_data",
                "description": (
                    "基于本体论执行完整数据查询。系统会自动将逻辑字段名映射为物理列名，"
                    "并根据 field_types 确保过滤条件的类型安全。filters 只用于行级字段过滤；"
                    "指标/聚合结果条件必须放入 having。支持多表 JOIN 和聚合后过滤（HAVING）。"
                    "返回结果会包含 data_sources 和 table_descriptions，"
                    "用于说明实际查询的数据来源表、表别名和业务描述。"
                    "不要传 limit，避免遗漏数据。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_class": {"type": "string", "description": "主查询单体类 ID"},
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "聚合指标列表（逻辑字段名），如 ['销售金额', '销售数量']。"
                                "对于'进度/完成情况/达成如何'这类泛化问题，不要只传一个指标，"
                                "应组合 MTD达成进度、工作日进度、实际销售额、目标销售额、上月实际值等相关指标/字段。"
                            ),
                        },
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "分组维度列表（逻辑字段名），如 ['品类名称', '日期']",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "description": "字段逻辑名"},
                                    "operator": {
                                        "type": "string",
                                        "description": (
                                            "操作符：=, !=, >, <, >=, <=, IN, NOT IN, "
                                            "LIKE, NOT LIKE, IS NULL, IS NOT NULL, BETWEEN"
                                        ),
                                    },
                                    "value": {"description": "过滤值，IN/BETWEEN 为列表，IS NULL/IS NOT NULL 可省略"},
                                },
                                "required": ["field", "operator"],
                            },
                            "description": (
                                "行级过滤条件列表，每项必须包含 field 和 operator。field 必须是 class "
                                "的明细字段/维度字段，不能是 metric 指标名；指标条件请使用 having。"
                            ),
                        },
                        "join_classes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "关联的多个 class ID 列表",
                        },
                        "having": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "description": "聚合字段逻辑名"},
                                    "operator": {
                                        "type": "string",
                                        "description": "操作符：>, <, >=, <=, =, !=",
                                    },
                                    "value": {"description": "过滤值"},
                                },
                                "required": ["field", "operator", "value"],
                            },
                            "description": (
                                "聚合后过滤条件（HAVING），用于 metric/聚合结果过滤，"
                                "如 [{'field': '销售金额', 'operator': '>', 'value': 100000}]"
                            ),
                        },
                        "order_by": {
                            "type": "string",
                            "description": "排序字段（逻辑字段名），可带 ASC/DESC，如 '销售金额 DESC'",
                        },
                    },
                    "required": ["target_class"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "python_analyze",
                "description": (
                    "使用 Python 对前步 query_ontology_data 查询到的 df/df_1/df_2 等完整数据进行再分析。"
                    "职责包括：1) 查询结果数据量较大时，按用户问题做聚合、筛选、排序、Top、占比或其他计算；"
                    "2) 涉及上月对比、环比、同比、变化等比较问题且数据量较大时，计算差值、变化方向和变化率。"
                    "小规模且已聚合、已足够回答的数据不应调用本工具。支持类似 Jupyter 习惯，最后一行写表达式"
                    "可直接捕获结果，无需刻意写 print()。如果报错，请根据返回的精确列名表进行修正。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Python 分析代码。可用变量：df(最后一次查询结果), df_1, df_2... 尽量简短精炼。"
                            ),
                        },
                    },
                    "required": ["code"],
                },
            },
        },
    ]


# ============================================================
# 全局缓存
# ============================================================
_engines: dict = {}
_query_engines: dict = {}
_system_prompts: dict = {}
TOOLS: list[ChatCompletionToolParam] = []
_init_lock = asyncio.Lock()


async def init_prompt(scenario_id: str):
    global _engines, _query_engines, _system_prompts, TOOLS

    async with _init_lock:
        if _engines.get(scenario_id) is None:
            ontology_dir = f"{Cfg.scenarios_root}/{scenario_id}/ontology"
            data_dir = f"{Cfg.scenarios_root}/{scenario_id}/data"
            _engines[scenario_id] = OntologyEngine(ontology_dir, data_dir)

        if _query_engines.get(scenario_id) is None:
            db_url = ""
            try:
                from modules.data_connections import get_active_connection
                active_conn = get_active_connection(scenario_id)
                if active_conn:
                    db_url = active_conn["connection_url"]
            except Exception:
                pass
            _query_engines[scenario_id] = DataQueryEngine(_engines[scenario_id], db_connection_url=db_url)

        if _system_prompts.get(scenario_id) is None:
            _system_prompts[scenario_id] = _build_system_prompt(_engines[scenario_id], scenario_id)

        TOOLS = _build_tools()
        print(f"[Prompt] Schema loaded: {len(_engines[scenario_id].list_classes())} classes, {len(_engines[scenario_id].list_metrics())} metrics")


def reset_engine(scenario_id: str):
    global _engines, _query_engines, _system_prompts

    print("重新提取前，需要重置引擎 >>>>>>>>>>")
    if _engines.get(scenario_id):
        del _engines[scenario_id]
    if _query_engines.get(scenario_id):
        del _query_engines[scenario_id]
    if _system_prompts.get(scenario_id):
        del _system_prompts[scenario_id]


def get_engine(scenario_id: str) -> OntologyEngine:
    return _engines[scenario_id]


def get_query_engine(scenario_id: str) -> DataQueryEngine:
    global _query_engines
    return _query_engines[scenario_id]


def get_system_prompt(scenario_id: str):
    global _system_prompts
    return _system_prompts[scenario_id]


def get_system_tools() -> list[ChatCompletionToolParam]:
    global TOOLS
    return TOOLS
