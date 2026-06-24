"""
chat_helper.py — 槽位填充 + 图表推荐 + 下钻 + Ontology→SQL 辅助
================================================================
v2: 从 DB 动态加载 Ontology，generate_physical_query 基于 OntologyEngine
    保留原始 6 个支撑函数，适配新架构。
"""

import json
import re
from typing import List, Dict, Any, Optional, Tuple

from configs.global_config import client
from tools.db import get_db


# ============================================================
# ① 从 DB 加载 Ontology（替代原始硬编码 ONTOLOGY/MAPPING）
# ============================================================

def load_ontology(scenario_id: str) -> dict:
    """从 SQLite 加载完整 Ontology（概念 + 指标 + 图表规则）"""
    conn = get_db()

    # 概念层级
    concepts = conn.execute(
        "SELECT * FROM concepts WHERE scenario_id=? ORDER BY level, sort_order",
        (scenario_id,)
    ).fetchall()

    # 指标
    metrics = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=? ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()

    # 图表规则
    chart_rules = conn.execute(
        "SELECT * FROM chart_rules WHERE scenario_id=? ORDER BY priority DESC",
        (scenario_id,)
    ).fetchall()

    # Schema classes
    classes = conn.execute(
        "SELECT * FROM schema_classes WHERE scenario_id=?",
        (scenario_id,)
    ).fetchall()

    # Schema relationships
    rels = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=?",
        (scenario_id,)
    ).fetchall()

    conn.close()

    # 构建概念树
    concept_tree = _build_concept_tree([dict(c) for c in concepts])

    # 构建 ONTOLOGY 结构（兼容原始函数签名）
    ontology = {
        "classes": [
            {"id": c["id"], "name_cn": c["name_cn"],
             "properties": json.loads(c["properties"])}
            for c in classes
        ],
        "metrics": [
            {"id": m["id"], "name_cn": m["name"], "class": m["target_class"],
             "field": m["formula"], "agg": m["calculation"],
             "chart_type": m["chart_type"], "category": m["category"]}
            for m in metrics
        ],
    }

    # 构建 MAPPING 结构（兼容原始函数签名）
    # 关键：table_name 从 csv_file 推导，确保与 OntologyEngine 一致
    mapping = {
        "classes": {
            c["id"]: {
                "table_name": c["csv_file"].replace(".csv", "") if c["csv_file"] else c["id"],
                "primary_key": c["primary_key"],
                "name_cn": c["name_cn"],
                "field_map": {p: p for p in json.loads(c["properties"])},
            }
            for c in classes
        },
        "relationships": [
            {"source": r["source"], "target": r["target"], "join_key": r["join_key"]}
            for r in rels
        ],
    }

    return {
        "concepts": [dict(c) for c in concepts],
        "concept_tree": concept_tree,
        "metrics": [
            dict(m) | {
                "dimensions": json.loads(m["dimensions"]),
                "required_dimensions": json.loads(m["required_dimensions"]),
            }
            for m in metrics
        ],
        "chart_rules": [dict(r) for r in chart_rules],
        # 兼容原始函数
        "ONTOLOGY": ontology,
        "MAPPING": mapping,
    }


def _build_concept_tree(concepts: list) -> list:
    """构建概念树"""
    by_parent: dict[str, list] = {}
    for c in concepts:
        pid = c.get("parent_id") or "__root__"
        by_parent.setdefault(pid, []).append(c)

    def _make_node(c: dict) -> dict:
        children = by_parent.get(c["id"], [])
        return {
            "id": c["id"],
            "name": c["name"],
            "level": c["level"],
            "concept_type": c.get("concept_type", ""),
            "related_class": c.get("related_class", ""),
            "children": [_make_node(ch) for ch in children],
        }

    return [_make_node(c) for c in by_parent.get("__root__", [])]


# ============================================================
# ② NL2Ontology 语义卡槽提取 (LLM)
# ============================================================

async def llm_extract_ontology(scenario_id: str, message: str, current_slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用 LLM 将用户口语化输入映射到企业本体节点/属性。
    v2: 从 DB 动态加载指标和维度定义。
    """
    ontology_data = load_ontology(scenario_id)
    ONTOLOGY = ontology_data["ONTOLOGY"]

    system_prompt = f"""你是一个企业数据查询的语义解析器。
用户会说类似"上个月华东区各门店的销售额"这样的话，
你需要把它映射到下面的本体结构中。

可用指标:
{json.dumps(ONTOLOGY.get("metrics", []), ensure_ascii=False, indent=2)}

可用实体类:
{json.dumps(ONTOLOGY.get("classes", []), ensure_ascii=False, indent=2)}

请返回 JSON 格式:
{{
  "metrics": ["指标id"],
  "dimensions": {{"维度字段": "值或空字符串"}},
  "time_range": "时间描述或空字符串"
}}

只返回 JSON，不要其他文字。"""

    try:
        response = await client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"当前槽位: {json.dumps(current_slots, ensure_ascii=False)}\n用户输入: {message}"}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content.strip()
        # 提取 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception as e:
        print(f"[chat_helper] llm_extract_ontology error: {e}")
        return current_slots


# ============================================================
# ③ 约束校验 (纯代码逻辑)
# ============================================================

def check_ontology_constraints(scenario_id: str, slots: Dict[str, Any]) -> Tuple[bool, str]:
    """
    校验查询槽位是否满足业务约束。
    v2: 从 DB 动态加载指标的 required_dimensions。
    """
    if not slots.get("metrics"):
        return False, "NEED_METRIC"

    ontology_data = load_ontology(scenario_id)
    dimensions = slots.get("dimensions", {})

    # 查找指标定义，检查必填维度
    for m in ontology_data["metrics"]:
        if m["id"] in slots.get("metrics", []):
            required_dims = m.get("required_dimensions", [])
            for rd in required_dims:
                if rd not in dimensions:
                    return False, f"NEED_{rd.upper()}"

    # 默认业务规则：查询销售指标需要时间和门店
    metric_classes = set()
    for m in ontology_data["metrics"]:
        if m["id"] in slots.get("metrics", []):
            metric_classes.add(m.get("target_class", ""))

    if "Sale" in metric_classes:
        if "sale_date" not in dimensions and "time_range" not in dimensions:
            return False, "NEED_TIME"

    return True, ""


# ============================================================
# ④ 反问话术润色 (LLM)
# ============================================================

async def llm_generate_clarification(missing_type: str, user_msg: str) -> str:
    """让大模型结合上下文让反问显得更自然友好"""
    prompts = {
        "NEED_METRIC": "正在为您关注运营数据。请问您具体想看的是销售总额、订单量，还是库存情况？",
        "NEED_TIME": "收到。请问您关注的是本月度、整个第二季度，还是特定的日期范围？",
        "NEED_STORE": "请问您想查看的是整个集团、特定区域（如华东区），还是某家特定门店（如松江万达店）的数据？",
    }
    default_prompt = prompts.get(missing_type, "请问您能提供更具体的时间或门店信息吗？")

    try:
        response = await client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": "你是一个亲切的企业智能看板助手。请参考给定的反问方向，结合用户刚才说的话，说一句自然的中文反问引导。"},
                {"role": "user", "content": f"反问方向提示：{default_prompt}。用户刚刚输入了：{user_msg}"}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except:
        return default_prompt


# ============================================================
# ⑤ 确定性物理 SQL 自动组装引擎 (纯代码逻辑)
# ============================================================

def generate_physical_query(scenario_id: str, slots: Dict[str, Any]) -> str:
    """
    根据槽位自动组装标准 SQL 语句。
    v2: 优先使用 OntologyEngine 获取正确的 table_name，
        回退到 DB MAPPING，兜底使用 class_id。

    输入: slots = {"metrics": ["total_sales"], "dimensions": {"store_name": "xx", "sale_date": "2025-01"}}
    输出: 标准 SQL 字符串
    """
    if not slots.get("metrics"):
        return ""

    metric_id = slots["metrics"][0]
    dims = slots.get("dimensions", {})

    # --- 优先使用 OntologyEngine 获取 table_name ---
    fact_table, target_class, metric_meta = _resolve_metric(scenario_id, metric_id)
    if not fact_table or not metric_meta:
        # 回退到 DB MAPPING
        ontology_data = load_ontology(scenario_id)
        ONTOLOGY = ontology_data["ONTOLOGY"]
        MAPPING = ontology_data["MAPPING"]

        for m in ONTOLOGY.get("metrics", []):
            if m["id"] == metric_id:
                metric_meta = m
                break
        if not metric_meta:
            return ""

        target_class = metric_meta.get("class", "")
        class_mapping = MAPPING["classes"].get(target_class, {})
        fact_table = class_mapping.get("table_name", target_class.lower())

    # 解析聚合字段和函数
    formula = metric_meta.get("field", metric_meta.get("formula", ""))
    agg_func = metric_meta.get("agg", "SUM").upper()

    # 尝试从 formula 中提取字段名和聚合函数
    select_field = formula
    if "(" in formula:
        m = re.search(r'\((\w+)\)', formula)
        if m:
            select_field = m.group(1)
            agg_func = formula.split("(")[0].strip().upper()

    # --- 构建 JOIN 和 WHERE ---
    joins = []
    where_clauses = []
    group_by_fields = []
    used_tables = {fact_table}

    # 获取 MAPPING（优先 OntologyEngine，回退 DB）
    mapping_data = _resolve_mapping(scenario_id)

    # 遍历维度，匹配对应的 JOIN 表
    for dim_key, dim_value in dims.items():
        for cls_id, cls_map in mapping_data.get("classes", {}).items():
            field_map = cls_map.get("field_map", {})
            if dim_key in field_map and cls_id != target_class:
                join_table = cls_map.get("table_name", cls_id.lower())
                if join_table not in used_tables:
                    join_key = _find_join_key(mapping_data, target_class, cls_id)
                    if join_key:
                        pk = cls_map.get("primary_key", "id")
                        joins.append(
                            f'LEFT JOIN "{join_table}" ON "{fact_table}"."{join_key}" = "{join_table}"."{pk}"'
                        )
                        used_tables.add(join_table)
                        group_by_fields.append(f'"{join_table}"."{dim_key}"')
                elif join_table in used_tables and cls_id != target_class:
                    group_by_fields.append(f'"{join_table}"."{dim_key}"')

        # 添加 WHERE 条件
        if dim_value and dim_value != "":
            dim_table = fact_table
            for cls_id, cls_map in mapping_data.get("classes", {}).items():
                if dim_key in cls_map.get("field_map", {}):
                    dim_table = cls_map.get("table_name", cls_id.lower())
                    break
            where_clauses.append(f'"{dim_table}"."{dim_key}" = \'{dim_value}\'')

    # --- 拼装 SQL ---
    if agg_func in ("SUM", "COUNT", "AVG", "MAX", "MIN"):
        fields_to_select = f'{agg_func}("{fact_table}"."{select_field}") AS result_value'
    else:
        fields_to_select = f'"{fact_table}"."{select_field}" AS result_value'

    if group_by_fields:
        fields_to_select = f'{", ".join(group_by_fields)}, ' + fields_to_select

    sql = f'SELECT {fields_to_select} FROM "{fact_table}"'
    if joins:
        sql += f' {" ".join(joins)}'
    if where_clauses:
        sql += f' WHERE {" AND ".join(where_clauses)}'
    if group_by_fields:
        sql += f' GROUP BY {", ".join(group_by_fields)}'

    return sql


def _resolve_metric(scenario_id: str, metric_id: str) -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    优先从 OntologyEngine 解析指标的物理表名。
    返回 (fact_table, target_class, metric_meta)
    """
    try:
        from prompts.prompt import get_engine, get_query_engine
        engine = get_engine(scenario_id)

        # 从 DB 查指标元数据
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM metrics WHERE scenario_id=? AND id=?",
            (scenario_id, metric_id)
        ).fetchone()
        conn.close()

        if not row:
            return None, None, None

        target_class = row["target_class"]
        fact_table = engine.get_table_name(target_class)

        metric_meta = {
            "id": row["id"],
            "name": row["name"],
            "class": target_class,
            "field": row["formula"],
            "agg": row["calculation"],
            "chart_type": row["chart_type"],
        }

        return fact_table, target_class, metric_meta
    except Exception:
        return None, None, None


def _resolve_mapping(scenario_id: str) -> dict:
    """
    优先从 OntologyEngine 构建 MAPPING，回退到 DB。
    确保表名与 schema_mapping.json 一致。
    """
    try:
        from prompts.prompt import get_engine
        engine = get_engine(scenario_id)

        mapping = {"classes": {}, "relationships": []}
        for cls_id, cls_data in engine.classes.items():
            mapping["classes"][cls_id] = {
                "table_name": engine.get_table_name(cls_id),
                "primary_key": engine.get_primary_key(cls_id),
                "name_cn": cls_data.get("name_cn", ""),
                "field_map": engine.get_field_map(cls_id),
            }
        mapping["relationships"] = [
            {"source": r["source"], "target": r["target"], "join_key": r["join_key"]}
            for r in engine.relationships
        ]
        return mapping
    except Exception:
        # 回退到 DB
        ontology_data = load_ontology(scenario_id)
        return ontology_data["MAPPING"]


def _find_join_key(mapping: dict, source: str, target: str) -> Optional[str]:
    """查找两个 class 之间的 JOIN 字段"""
    for rel in mapping.get("relationships", []):
        if rel["source"] == source and rel["target"] == target:
            return rel["join_key"]
        if rel["source"] == target and rel["target"] == source:
            return rel["join_key"]
    return None


# ============================================================
# ⑥ 数据特征推荐图表组件 (纯代码逻辑)
# ============================================================

def recommend_visualization(slots: Dict[str, Any], data: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """根据数据特征推荐图表类型，返回 (echarts_option, display_type)"""
    dims = slots.get("dimensions", {})

    # 如果存在分组字段（如子品类拆分），则返回完整的 ECharts 柱状图协议
    if data and "sub_category" in data[0]:
        categories = [row["sub_category"] for row in data]
        values = [row["result_value"] for row in data]

        echarts_option = {
            "title": {"text": f"{dims.get('store_name', '')} - {dims.get('category', '')}销售拆分图"},
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value"},
            "series": [{"data": values, "type": "bar", "name": "销售额"}]
        }
        return echarts_option, "CHART"

    # 如果已经到了极细颗粒度或者要求看清单明细
    if dims.get("get_detail") == "true":
        return None, "TABLE"

    # 默认兜底只返回数据卡片
    return None, "CARD"


# ============================================================
# ⑦ 大模型数据结论总结 (LLM)
# ============================================================

async def llm_summarize_data(slots: Dict[str, Any], data: List[Dict[str, Any]]) -> str:
    """使用 LLM 对查询结果进行业务分析总结"""
    try:
        response = await client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": "你是一个零售数据分析专家。请根据底层查出来的结构化数据结果，为店长或高管写一段简明扼要的业务分析小结。不要透露具体的 SQL 字段名。"},
                {"role": "user", "content": f"查询参数: {json.dumps(slots, ensure_ascii=False)}, 数据库真实返回的数据: {json.dumps(data, ensure_ascii=False)}"}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except:
        return "已成功为您调取并生成对应的运营看板，请查看下方图表。"


# ============================================================
# ⑧ 概念下钻 (纯代码逻辑)
# ============================================================

def drill_down(scenario_id: str, current_concept_id: str) -> dict:
    """
    概念下钻：返回当前概念的子概念列表。
    如果是叶子节点，返回关联的 class 明细建议。
    """
    ontology_data = load_ontology(scenario_id)
    concepts = ontology_data["concepts"]

    current = None
    children = []
    for c in concepts:
        if c["id"] == current_concept_id:
            current = c
        if c.get("parent_id") == current_concept_id:
            children.append(c)

    if not current:
        return {"error": f"概念 {current_concept_id} 不存在"}

    if not children:
        return {
            "concept": current,
            "children": [],
            "is_leaf": True,
            "related_class": current.get("related_class", ""),
            "suggestion": f"已到达最细粒度「{current['name']}」，可以查看关联的 {current.get('related_class', '')} 明细数据",
        }

    return {
        "concept": current,
        "children": children,
        "is_leaf": False,
        "suggestion": f"「{current['name']}」包含以下子类：{', '.join(c['name'] for c in children)}，可以继续下钻",
    }


# ============================================================
# ⑨ 图表推荐 (纯代码逻辑)
# ============================================================

def recommend_chart(scenario_id: str, data_pattern: str = "", metric_chart_type: str = "") -> str:
    """
    推荐图表类型。
    优先级: 指标绑定的 chart_type > chart_rules 匹配 > 默认 bar
    """
    if metric_chart_type:
        return metric_chart_type

    if data_pattern:
        conn = get_db()
        rules = conn.execute(
            "SELECT * FROM chart_rules WHERE scenario_id=? AND data_pattern=? ORDER BY priority DESC LIMIT 1",
            (scenario_id, data_pattern)
        ).fetchone()
        conn.close()
        if rules:
            return rules["chart_type"]

    return "bar"


# ============================================================
# ⑩ 生成槽位摘要（供 LLM 理解当前对话状态）
# ============================================================

def format_slots_summary(slots: dict) -> str:
    """将槽位状态格式化为自然语言摘要"""
    parts = []

    if slots.get("metric"):
        m = slots["metric"]
        parts.append(f"指标: {m['name']}（{m['id']}）")
        parts.append(f"计算方式: {m.get('calculation', 'N/A')}")
        parts.append(f"推荐图表: {slots.get('chart_type', 'auto')}")

    if slots.get("dimensions"):
        parts.append(f"维度: {', '.join(slots['dimensions'])}")

    if slots.get("time_range"):
        parts.append(f"时间范围: {slots['time_range']}")

    if slots.get("concept"):
        parts.append(f"概念: {slots['concept']['name']}（层级 {slots['concept']['level']}）")

    if slots.get("missing_required"):
        parts.append(f"缺失必填维度: {', '.join(slots['missing_required'])}")

    if slots.get("clarification"):
        parts.append(f"需要追问: {slots['clarification']}")

    return "\n".join(parts) if parts else "（未识别到明确的查询意图）"
