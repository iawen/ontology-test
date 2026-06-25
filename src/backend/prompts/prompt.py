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
from datetime import datetime, timezone

from configs.global_config import Cfg
from tools.db import get_db
from core.ontology.ontology_engine import OntologyEngine
from core.ontology.data_query import DataQueryEngine


def _build_ontology_context(engine: OntologyEngine, scenario_id: str) -> str:
    """构建本体上下文：Data + Logic + Action 三要素"""

    # ── 1. Data：实体类 + 关系 ──
    classes_lines = []
    for c in engine.list_classes():
        cls_info = engine.classes.get(c["id"], {})
        csv_file = cls_info.get("csv_file", "")
        props = c.get("properties", [])
        classes_lines.append(
            f"  - **{c['id']}**（{c.get('name_cn', '')}）→ {csv_file}\n"
            f"    字段: {', '.join(props[:15])}"
        )
    classes_str = "\n".join(classes_lines) if classes_lines else "（暂无）"

    rels_lines = []
    for r in engine.relationships:
        rels_lines.append(
            f"  - {r['source']} --[{r.get('type', '')}]--> {r['target']} (JOIN: {r.get('join_key', '')})"
        )
    rels_str = "\n".join(rels_lines) if rels_lines else "（暂无）"

    # ── 2. Logic：指标 + 概念 + 术语 + 技能 ──
    metrics_str = _build_metrics_summary(scenario_id)
    concepts_str = _build_concepts_summary(scenario_id)
    glossary_str = _build_glossary_summary(scenario_id)
    skills_str = _build_skills_summary(scenario_id)
    chart_rules_str = _build_chart_rules_summary(scenario_id)

    # ── 3. Action：可用行动 ──
    actions_str = _build_actions_summary(scenario_id)
    alert_rules_str = _build_alert_rules_summary(scenario_id)

    return f"""
# 本体知识库（Ontology）

## 一、Data（数据层）— 实体与关系

### 实体类（Classes）
{classes_str}

### 关系（Relationships）
{rels_str}

## 二、Logic（逻辑层）— 指标、概念、术语、技能

### 指标（Metrics）
{metrics_str}

### 概念层级（Concepts）
{concepts_str}

### 专用术语（Glossary）
{glossary_str}

### 技能包（Skills）
{skills_str}

### 图表推荐规则（Chart Rules）
{chart_rules_str}

## 三、Action（行动层）— 可执行操作

### 可用行动（Actions）
{actions_str}

### 告警规则（Alert Rules）
{alert_rules_str}
"""


def _build_metrics_summary(scenario_id: str) -> str:
    """从数据库加载指标定义，生成给 LLM 看的摘要"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=? ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return "（暂无指标定义）"
    lines = []
    current_category = ""
    for r in rows:
        if r["category"] != current_category:
            current_category = r["category"]
            lines.append(f"\n### {current_category}")
        dims = json.loads(r["dimensions"]) if r["dimensions"] else []
        req_dims = json.loads(r["required_dimensions"]) if r["required_dimensions"] else []
        chart = r["chart_type"] if r["chart_type"] else "bar"
        lines.append(
            f"  **{r['name']}** (`{r['id']}`)\n"
            f"    说明: {r['description']}\n"
            f"    数据源: {r['target_class']} | 计算方式: {r['calculation']}\n"
            f"    可选维度: {', '.join(dims)} | 必选维度: {', '.join(req_dims)}\n"
            f"    推荐图表: {chart}"
        )
    return "\n".join(lines)


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
    ontology_context = _build_ontology_context(engine, scenario_id)

    return f"""你是一个专业的数据分析助手（ChatBI），基于本体论（Ontology）驱动的语义层来回答用户的数据查询需求。

今天是{today}。

{ontology_context}

## 你的核心工作流程

1. **理解意图**：分析用户的自然语言问题，识别涉及的实体（class）、指标（metric）、维度（dimension）和过滤条件（filter）。
2. **本体映射**：将用户问题中的业务术语映射到本体 schema 中的 class 和 field。
3. **查询构建**：调用工具构建 SQL 查询，获取数据。
4. **结果解读**：将查询结果以清晰、易懂的方式呈现给用户。

## 关键规则

### 字段映射（Field Map）
- 每个字段有「逻辑名」（业务术语）和「物理列名」（数据库列名），它们可能不同。
- 你在构建查询时，必须使用**逻辑字段名**，系统会自动映射为物理列名。
- 如果用户说的术语不在 field_map 中，使用 fuzzy_search_values 工具进行模糊匹配。

### 类型安全（Type Safety）
- 每个字段都有类型声明（text / numeric / date / boolean）。
- **numeric 类型字段**：过滤值不能加引号，例如 `{{"field": "销售金额", "operator": ">", "value": 1000}}`
- **text 类型字段**：过滤值必须加引号，例如 `{{"field": "品类名称", "operator": "=", "value": "坚果"}}`
- 你必须在 filter 中明确指定 operator，不要省略。
- 如果不确定字段类型，先调用 get_field_types 工具查询。

### 多表关联（JOIN）
- 当查询涉及多个 class 的字段时，需要指定 join_class。
- 系统会自动根据 source_key / target_key 推导 JOIN 条件。
- 如果两个 class 没有直接关系，系统会尝试多跳路径推导。
- 你可以使用 get_join_path 工具查看两个 class 之间的 JOIN 路径。

### 过滤条件格式
每个 filter 必须是如下格式：
```json
{{
  "field": "字段逻辑名",
  "operator": "操作符",
  "value": "值"
}}
```
支持的操作符：=, !=, <>, >, <, >=, <=, IN, NOT IN, LIKE, NOT LIKE, IS NULL, IS NOT NULL, BETWEEN

### 聚合后过滤（HAVING）
如果需要对聚合结果进行过滤（如"销售额超过10万的门店"），使用 having 参数：
```json
{{
  "field": "销售金额",
  "operator": ">",
  "value": 100000
}}
```

## 工作流程建议
1. 先调用 get_ontology_schema 了解数据结构
2. 如果不确定字段类型，调用 get_field_types
3. 如果需要多表关联，调用 get_join_path 查看 JOIN 路径
4. 如果不确定字段值，调用 fuzzy_search_values 搜索
5. 调用 query_ontology_data 执行查询
6. 如果需要复杂分析，调用 python_analyze
"""


def _build_tools() -> list[dict]:
    return [
        # {
        #     "type": "function",
        #     "function": {
        #         "name": "get_ontology_schema",
        #         "description": "获取本体 Schema 信息。不传 class_id 返回所有 class 概览；传入 class_id 返回该 class 的详细字段映射、字段类型和关联关系。",
        #         "parameters": {
        #             "type": "object",
        #             "properties": {
        #                 "class_id": {
        #                     "type": "string",
        #                     "description": "实体类 ID（可选，不传则返回全部概览）"
        #                 }
        #             }
        #         }
        #     }
        # },
        {
            "type": "function",
            "function": {
                "name": "query_ontology_data",
                "description": "基于本体论执行数据查询。系统会自动将逻辑字段名映射为物理列名，并根据 field_types 确保过滤条件的类型安全。支持多表 JOIN 和聚合后过滤（HAVING）。",
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
                            "description": "过滤条件列表，每项必须包含 field 和 operator"
                        },
                        # "join_class": {
                        #     "type": "string",
                        #     "description": "关联的单个 class ID（向后兼容）"
                        # },
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
                            "description": "聚合后过滤条件（HAVING），如 [{'field': '销售金额', 'operator': '>', 'value': 100000}]"
                        },
                        "order_by": {
                            "type": "string",
                            "description": "排序字段（逻辑字段名），可带 ASC/DESC，如 '销售金额 DESC'"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回行数限制，默认100",
                            "default": 100
                        }
                    },
                    "required": ["target_class"]
                }
            }
        },
        # {
        #     "type": "function",
        #     "function": {
        #         "name": "get_field_types",
        #         "description": "获取指定 class 的字段类型声明。用于确认字段是 text 还是 numeric，确保过滤条件的类型安全。",
        #         "parameters": {
        #             "type": "object",
        #             "properties": {
        #                 "class_id": {
        #                     "type": "string",
        #                     "description": "实体类 ID"
        #                 }
        #             },
        #             "required": ["class_id"]
        #         }
        #     }
        # },
        # {
        #     "type": "function",
        #     "function": {
        #         "name": "get_join_path",
        #         "description": "获取两个 class 之间的 JOIN 路径。如果两个 class 没有直接关系，系统会尝试多跳路径推导。",
        #         "parameters": {
        #             "type": "object",
        #             "properties": {
        #                 "source": {
        #                     "type": "string",
        #                     "description": "起始 class ID"
        #                 },
        #                 "target": {
        #                     "type": "string",
        #                     "description": "目标 class ID"
        #                 }
        #             },
        #             "required": ["source", "target"]
        #         }
        #     }
        # },
        {
            "type": "function",
            "function": {
                "name": "fuzzy_search_values",
                "description": "模糊搜索某个字段的值，用于实体消歧。当不确定用户提到的具体值时，先用此工具搜索可能的匹配值。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "class_id": {
                            "type": "string",
                            "description": "实体类 ID"
                        },
                        "field_name": {
                            "type": "string",
                            "description": "要搜索的字段名（逻辑名）"
                        },
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回结果数量限制，默认10",
                            "default": 10
                        }
                    },
                    "required": ["class_id", "field_name", "keyword"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_class_sample",
                "description": "获取某个实体类的样本数据，用于了解数据格式和内容。当需要查看数据样例或验证字段值时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "class_id": {
                            "type": "string",
                            "description": "实体类 ID"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回行数，默认5",
                            "default": 5
                        }
                    },
                    "required": ["class_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "python_analyze",
                "description": "使用 Python 对前步查询到的 df/df_1/df_2 等数据进行统计、计算比例、同比环比或归纳。支持类似 Jupyter 习惯，最后一行写表达式（如 df['a']/df['b']）可直接捕获结果，无需刻意写 print()。如果报错，请根据返回的精确列名表进行修正。",
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


def init_prompt(scenario_id: str):
    global _engines, _query_engines, _system_prompts, TOOLS

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