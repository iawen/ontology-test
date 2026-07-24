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

import os
import json
import threading
from datetime import datetime, timezone

from configs.global_config import Cfg
from core.db.db import get_db
from core.ontology.ontology_engine import OntologyEngine
from core.ontology.data_query import DataQueryEngine


def _build_ontology_context(engine: OntologyEngine, scenario_id: str) -> str:
    """构建本体上下文：Data + Logic + Action 三要素"""
    metrics_by_class = _load_metrics_by_class(scenario_id)

    # ── 1. Data：实体类 + 关系 ──
    classes_lines = []
    for c in engine.list_classes():
        cls_info = engine.classes.get(c["id"], {})
        table_name = cls_info.get("table_name", "")
        props = c.get("properties", [])
        metric_lines = metrics_by_class.get(c["id"], [])
        metrics_text = "\n".join(metric_lines) if metric_lines else "    关联指标: （暂无）"
        classes_lines.append(
            f"  - **{c['id']}**（{c.get('name_cn', '')}）→ {table_name}\n"
            f"    字段: {', '.join(props[:15])}\n"
            f"{metrics_text}"
        )
    classes_str = "\n".join(classes_lines) if classes_lines else "（暂无）"

    rels_lines = []
    for r in engine.relationships:
        rels_lines.append(
            f"  - {r['source']} --[{r.get('type', '')}]--> {r['target']} (JOIN: {r.get('join_key', '')})"
        )
    rels_str = "\n".join(rels_lines) if rels_lines else "（暂无）"

    return f"""
# 本体知识库（Ontology）

## 一、Data + Logic — 实体、字段与关联指标

### 实体类（Classes）
{classes_str}

### 关系（Relationships）
{rels_str}
"""


def _load_metrics_by_class(scenario_id: str) -> dict[str, list[str]]:
    """从数据库加载指标定义，并按实体类分组。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=? ORDER BY target_class, sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()

    grouped: dict[str, list[str]] = {}
    for r in rows:
        dims = json.loads(r["dimensions"]) if r["dimensions"] else []
        req_dims = json.loads(r["required_dimensions"]) if r["required_dimensions"] else []
        chart = r["chart_type"] if r["chart_type"] else "bar"
        target_class = r["target_class"] or "__unbound__"
        grouped.setdefault(target_class, []).append(
            f"    关联指标: **{r['name']}** (`{r['id']}`)"
            f" | 说明: {r['description']}"
            f" | 可选维度: {', '.join(dims) or '-'}"
            f" | 必选维度: {', '.join(req_dims) or '-'}"
            f" | 推荐图表: {chart}"
        )
    return grouped


def _build_concepts_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM concepts WHERE scenario_id=? ORDER BY level, sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无概念定义）"
    lines = []
    for r in rows:
        indent = "  " * r["level"]
        type_label = {"domain": "业务域", "dimension_group": "分析维度", "metric_group": "指标组", "entity": "业务实体", "kpi": "关键指标"}.get(r["concept_type"], "")
        lines.append(f"{indent}- **{r['name']}** (`{r['id']}`) [{type_label}] → {r['related_class']}")
    return "\n".join(lines)


def _build_glossary_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=? ORDER BY category, sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无术语定义）"
    lines = []
    for r in rows:
        aliases = json.loads(r["aliases"]) if r["aliases"] else []
        alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
        lines.append(f"  - **{r['term']}** → {r['standard_name']}{alias_str}: {r['description']}")
    return "\n".join(lines)


def _build_skills_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM skills WHERE scenario_id=? AND is_active=1 ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无技能包）"
    lines = []
    for r in rows:
        lines.append(f"  - **{r['name']}** (`{r['id']}`): {r['description']}\n    触发条件: {r['trigger_condition']}")
    return "\n".join(lines)


def _build_chart_rules_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chart_rules WHERE scenario_id=? ORDER BY priority DESC",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无图表规则）"
    lines = []
    for r in rows:
        lines.append(f"  - 数据模式「{r['data_pattern']}」→ 推荐图表: {r['chart_type']} ({r['description']})")
    return "\n".join(lines)


def _build_actions_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM actions WHERE scenario_id=? AND is_active=1 ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无可用 Action）"
    lines = []
    for r in rows:
        params = json.loads(r["parameters"]) if r["parameters"] else {}
        confirm_label = "需确认" if r["requires_confirm"] else "自动执行"
        lines.append(
            f"  - **{r['name']}** (`{r['id']}`) [{r['action_type']}]\n"
            f"    说明: {r['description']}\n"
            f"    触发关键词: {r['trigger_condition']} | 目标对象: {r['target_object']} | {confirm_label}"
        )
    return "\n".join(lines)


def _build_alert_rules_summary(scenario_id: str) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM alert_rules WHERE scenario_id=? AND is_active=1",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无告警规则）"
    lines = []
    for r in rows:
        lines.append(
            f"  - **{r['name']}** (`{r['id']}`) [{r['severity']}]\n"
            f"    目标: {r['target_class']} | 条件: {r['condition_expression']}\n"
            f"    关联Action: {r['action_id'] or '无'}"
        )
    return "\n".join(lines)


def _build_system_prompt(engine: OntologyEngine, scenario_id: str) -> str:
    today = datetime.now().strftime("%Y年%m月%d日")

    return f"""你是一个专业的数据分析助手（ChatBI），基于本体论（Ontology）驱动的语义层来回答用户的数据查询需求。

今天是{today}。

## 你的核心工作流程

1. **理解意图**：分析用户的自然语言问题，识别涉及的实体（class）、指标（metric）、维度（dimension）和过滤条件（filter）。
2. **本体映射**：将用户问题中的业务术语映射到本体 schema 中的 class 和 field。
3. **查询构建**：调用工具构建 SQL 查询，获取数据。
4. **结果解读**：将查询结果以清晰、易懂的方式呈现给用户。

## 关键规则

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
如果需要对聚合结果/指标进行过滤（如"销售额超过10万的门店"、"达成率低于100%"），必须使用 having 参数，不要放入 filters：
```json
{{
  "field": "销售金额",
  "operator": ">",
  "value": 100000
}}
```

### 查询完整性
- 调用 query_ontology_data 时不要传 limit，不要为了展示方便截断业务查询结果，避免遗漏月份、区域、人员或明细分组。
- 不要调用样本、字段类型或模糊搜索类工具；这些能力已内化为系统确定性拦截器。

## 工作流程建议
1. 根据系统上下文识别 target_class、指标、维度、过滤条件和关联 class。
2. 调用 query_ontology_data 执行完整查询。
3. python_analyze 的职责是二次分析，不是查询替代品；仅在以下场景调用：
    - query_ontology_data 返回的数据量较大，不能直接从明细/宽表结果回答时，按用户问题对完整数据做聚合、汇总、排序、占比或计算，形成中间结果。
    - 用户问题涉及比较（如同步、同比、环比、差异、占比等），且已经有多个 query_ontology_data 结果、数据量较大时，使用 df_1、df_2 等完整结果做二次比较计算。
4. 如果 query_ontology_data 返回的小结果集已经足以回答，则不要调用 python_analyze，直接进入最终答复。
"""


def _build_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "query_ontology_data",
                "description": "基于本体论执行完整数据查询。系统会自动将逻辑字段名映射为物理列名，并根据 field_types 确保过滤条件的类型安全。filters 只用于行级字段过滤；指标/聚合结果条件必须放入 having。支持多表 JOIN 和聚合后过滤（HAVING）。不要传 limit，避免遗漏数据。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_class": {
                            "type": "string",
                            "description": "主查询单体类 ID"
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "聚合指标列表（逻辑字段名），如 ['销售金额', '销售数量']"
                        },
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "分组维度列表（逻辑字段名），如 ['品类名称', '日期']"
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "description": "字段逻辑名"},
                                    "operator": {"type": "string", "description": "操作符：=, !=, >, <, >=, <=, IN, NOT IN, LIKE, NOT LIKE, IS NULL, IS NOT NULL, BETWEEN"},
                                    "value": {"description": "过滤值，IN/BETWEEN 为列表，IS NULL/IS NOT NULL 可省略"}
                                },
                                "required": ["field", "operator"]
                            },
                            "description": "行级过滤条件列表，每项必须包含 field 和 operator。field 必须是 class 的明细字段/维度字段，不能是 metric 指标名；指标条件请使用 having。"
                        },
                        "join_classes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "关联的多个 class ID 列表"
                        },
                        "having": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "description": "聚合字段逻辑名"},
                                    "operator": {"type": "string", "description": "操作符：>, <, >=, <=, =, !="},
                                    "value": {"description": "过滤值"}
                                },
                                "required": ["field", "operator", "value"]
                            },
                            "description": "聚合后过滤条件（HAVING），用于 metric/聚合结果过滤，如 [{'field': '销售金额', 'operator': '>', 'value': 100000}]"
                        },
                        "order_by": {
                            "type": "string",
                            "description": "排序字段（逻辑字段名），可带 ASC/DESC，如 '销售金额 DESC'"
                        }
                    },
                    "required": ["target_class"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "python_analyze",
                "description": "二次分析工具。仅在 query_ontology_data 返回数据量较大、需要按用户问题进一步聚合/汇总/计算，或用户问题涉及同步/同比/环比/差异/占比等比较且已有多个较大查询结果时使用。小结果集已足以回答时不要调用。可用变量：df 为最后一次查询结果，df_1/df_2... 为历次查询结果。支持类似 Jupyter 习惯，最后一行表达式会被捕获为 result；也可显式赋值 result、df_result、output_data 或 summary。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python 分析代码。可用变量：df(最后一次查询结果), df_1, df_2... 尽量简短精炼。",
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
TOOLS: list[dict] = []
_init_lock = threading.RLock()


def init_prompt(scenario_id: str):
    global _engines, _query_engines, _system_prompts, TOOLS

    with _init_lock:
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


def get_system_tools():
    global TOOLS
    return TOOLS


# if __name__ == "__main__":
#     today = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
#     print(today)