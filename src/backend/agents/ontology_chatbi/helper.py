"""Shared helpers for data query agents."""

import json
from datetime import UTC, datetime

from core.db.db import get_db
from core.llm.chat_model import get_async_client, get_model_name
from tools.logger import logger

from .constants import (
    TOOL_DISPLAY_NAMES,
    TOOL_PURPOSES,
)

def metric_definition(metric: dict) -> dict:
    definition = metric.get("definition", {})
    if isinstance(definition, str):
        try:
            definition = json.loads(definition or "{}")
        except json.JSONDecodeError:
            definition = {}
    return definition if isinstance(definition, dict) else {}


def metric_target_classes(metric: dict) -> list[str]:
    """Return the Metric anchor plus source classes declared by its structured inputs."""
    definition = metric_definition(metric)
    items = [definition.get("anchor_class")]
    input_groups = [definition.get("inputs", [])]
    input_groups.extend(
        output.get("inputs", []) for output in definition.get("outputs", [])
        if isinstance(output, dict)
    )
    items.extend(
        input_item.get("class_id")
        for inputs in input_groups
        for input_item in inputs
        if isinstance(input_item, dict)
    )
    items.append(metric.get("target_class") or metric.get("class_id"))

    target_classes = []
    seen = set()
    for item in items:
        class_id = str(item or "").strip()
        if not class_id or class_id in seen:
            continue
        seen.add(class_id)
        target_classes.append(class_id)
    return target_classes


def metric_component_names(metric: dict) -> list[str]:
    """Return the user-facing names of definition inputs, preserving component order."""
    definition = metric_definition(metric)
    if definition.get("version") == 2:
        return [
            str(output.get("output_name") or output.get("id") or "").strip()
            for output in definition.get("outputs", [])
            if isinstance(output, dict)
            and str(output.get("output_name") or output.get("id") or "").strip()
        ]
    return [
        str(input_item.get("output_name") or input_item.get("field") or input_item.get("id") or "").strip()
        for input_item in metric_definition(metric).get("inputs", [])
        if isinstance(input_item, dict) and str(input_item.get("output_name") or input_item.get("field") or input_item.get("id") or "").strip()
    ]


def metric_context_summary(metric: dict) -> str:
    """Provide LLMs a governed description of the current Metric definition."""
    definition = metric_definition(metric)
    components = metric_component_names(metric)
    if definition.get("version") == 2:
        outputs = []
        for output in definition.get("outputs", []):
            if not isinstance(output, dict):
                continue
            details = []
            for item in output.get("inputs", []):
                if isinstance(item, dict):
                    details.append(
                        f"{str(item.get('aggregation') or 'SUM').upper()}({item.get('class_id', '')}.{item.get('field', '')})"
                    )
            outputs.append(
                f"{{id={output.get('id', '')}; 名称={output.get('output_name', '')}; "
                f"计算方式={output.get('expression_operator', '')}; 结果调整={output.get('offset', 0)}; "
                f"组成项={', '.join(details)}}}"
            )
        return (
            f"id={metric.get('id', '')}; 名称={metric.get('name') or metric.get('name_cn', '')}; "
            f"锚点类={definition.get('anchor_class') or metric.get('target_class', '')}; "
            f"并列输出=[{'; '.join(outputs) or '无'}]; 可选维度={metric.get('dimensions') or []}; "
            f"必要维度={metric.get('required_dimensions') or []}; 说明={metric.get('description', '')}"
        )
    input_details = []
    for input_item in definition.get("inputs", []):
        if not isinstance(input_item, dict):
            continue
        label = str(input_item.get("output_name") or input_item.get("field") or input_item.get("id") or "").strip()
        class_id = str(input_item.get("class_id") or "").strip()
        field = str(input_item.get("field") or "").strip()
        aggregation = str(input_item.get("aggregation") or "SUM").upper()
        if label:
            input_details.append(f"{label}={aggregation}({class_id}.{field})")
    return (
        f"id={metric.get('id', '')}; 名称={metric.get('name') or metric.get('name_cn', '')}; "
        f"锚点类={definition.get('anchor_class') or metric.get('target_class', '')}; "
        f"组成项名称={components}; 组成项={'; '.join(input_details) or '无'}; "
        f"计算方式={definition.get('expression_operator') or '未定义'}; "
        f"结果调整={definition.get('offset', 0)}; "
        f"可选维度={metric.get('dimensions') or []}; 必要维度={metric.get('required_dimensions') or []}; "
        f"说明={metric.get('description', '')}"
    )


def resolve_metric_reference(reference: str, metrics: list[dict]) -> tuple[dict | None, dict | None]:
    """Resolve a parent Metric or a V2 output ID/name within a Metric collection."""
    value = str(reference or "").strip()
    for metric in metrics:
        if value in {str(metric.get("id") or ""), str(metric.get("name") or ""), str(metric.get("name_cn") or "")}:
            return metric, None
    matches = []
    for metric in metrics:
        definition = metric_definition(metric)
        if definition.get("version") != 2:
            continue
        for output in definition.get("outputs", []):
            if isinstance(output, dict) and value in {str(output.get("id") or ""), str(output.get("output_name") or "")}:
                matches.append((metric, output))
    return matches[0] if len(matches) == 1 else (None, None)


def get_tool_purpose(tool_name: str) -> str:
    return TOOL_PURPOSES.get(tool_name, "执行当前分析步骤所需的辅助能力。")


def get_tool_display_name(tool_name: str) -> str:
    return TOOL_DISPLAY_NAMES.get(tool_name, tool_name)



def match_glossary_terms(scenario_id: str, user_message: str) -> list[dict]:
    """
    匹配用户消息中出现的专用名称，返回匹配到的条目列表。
    用于在 chat 流程中识别用户使用了哪些企业术语。
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM glossary_terms WHERE scenario_id=?",
        (scenario_id,)
    ).fetchall()
    conn.close()

    matched = []
    msg_lower = user_message.lower()
    for r in rows:
        aliases = json.loads(r["aliases"]) if r["aliases"] else []
        all_terms = [r["term"]] + aliases + ([r["standard_name"]] if r["standard_name"] else [])
        for t in all_terms:
            if t and t.lower() in msg_lower:
                matched.append({
                    "term": r["term"],
                    "standard_name": r["standard_name"],
                    "aliases": aliases,
                    "description": r["description"],
                    "category": r["category"],
                })
                break  # 一个条目只匹配一次
    logger.info(
        "Glossary matched: scenario_id=%s count=%d terms=%s",
        scenario_id,
        len(matched),
        [item["standard_name"] or item["term"] for item in matched],
    )
    return matched


# ============================================================
# LLM 意图路由 — 核心方法
# ============================================================

async def route_skills(scenario_id: str, user_message: str, conversation_history: list[dict] = None) -> list[dict]:
    """
    通过 LLM 意图路由匹配技能包。
    
    流程：
    1. 从 DB 加载所有激活的 skill
    2. 构建 skill 摘要列表（id + name + trigger_condition）
    3. 调用 LLM 判断哪些 skill 匹配用户消息
    4. 返回匹配的 skill 完整内容
    
    优势（vs 关键词匹配）：
    - 语义理解："最近卖得不好" → 匹配"销售分析"
    - 上下文感知：结合对话历史判断意图
    - 精确匹配：避免关键词误触发
    - 多 skill 组合：可同时匹配多个技能
    """

    # 加载激活的 skill
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM skills WHERE scenario_id=? AND is_active=1 ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # 构建 skill 摘要
    skill_summaries = []
    for r in rows:
        skill_summaries.append({
            "id": r["id"],
            "name": r["name"],
            "trigger_condition": r["trigger_condition"] or r["description"],
        })

    # 构建对话上下文（最近 3 轮）
    context = ""
    if conversation_history:
        recent = conversation_history[-6:]  # 最近 3 轮（每轮 user+assistant）
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                context += f"{role}: {content}\n"

    # 构建意图路由 prompt
    skill_list_str = "\n".join([
        f"- ID: {s['id']} | 名称: {s['name']} | 触发条件: {s['trigger_condition']}"
        for s in skill_summaries
    ])

    routing_prompt = f"""你是一个意图识别引擎。根据用户消息和对话上下文，判断应该激活以下哪些技能。

可用技能：
{skill_list_str}

对话上下文：
{context if context else "（无历史对话）"}

当前用户消息：{user_message}

请判断哪些技能与用户当前问题相关。注意：
1. 只选择真正相关的技能，不要过度匹配
2. 可以同时匹配多个技能
3. 如果没有匹配的技能，返回空列表
4. 严格按 JSON 格式返回

返回格式：
{{"matched": ["skill_id_1", "skill_id_2"], "reason": "匹配原因简述"}}"""

    # 调用 LLM
    try:
        response = await get_async_client().chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": routing_prompt}],
            temperature=0.1,  # 低温度，确保稳定输出
            max_tokens=256,
        )
        raw = response.choices[0].message.content or ""
    except Exception as e:
        print(f"[SkillRouter] LLM 调用失败: {e}")
        return []

    # 解析 LLM 返回
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            result = json.loads(match.group())
        else:
            result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[SkillRouter] JSON 解析失败: {raw}")
        return []

    matched_ids = result.get("matched", [])
    reason = result.get("reason", "")
    
    if not matched_ids:
        return []

    print(f"[SkillRouter] 匹配技能: {matched_ids}, 原因: {reason}")

    # 返回匹配的 skill 完整内容
    matched_skills = []
    for r in rows:
        if r["id"] in matched_ids:
            matched_skills.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "trigger_condition": r["trigger_condition"],
                "content": r["content"],
            })

    return matched_skills





def now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(UTC)


def now_timestamp() -> float:
    """返回当前本地 Unix 时间戳，单位为秒。"""
    return datetime.now().timestamp()


def safe_timestamp(value: datetime | None) -> float | None:
    """将 datetime 转为 Unix 时间戳；输入为空时返回 None。"""
    if value is None:
        return None
    return value.timestamp()


def elapsed_seconds(value1: datetime | None, value2: datetime | None) -> float | None:
    """计算两个时间的间隔秒数（value2 - value1）；任一输入为空时返回 None。"""
    ts1 = safe_timestamp(value1)
    ts2 = safe_timestamp(value2)
    if ts1 is None or ts2 is None:
        return None
    return ts2 - ts1


def valid_ap_month(ap_month: str) -> bool:
    """验证字符串是否为有效的 AP 月格式（例如 "2023AP05"）。"""
    try:
        year_text, month_text = ap_month.upper().split("AP", maxsplit=1)
        year = int(year_text)
        month = int(month_text)
        return year > 1000 and year < 3000 and month >= 1 and month <= 12
    except ValueError:
        return False


def parse_ap_month(ap_month: str) -> tuple[int, int]:
    """将 AP 月解析为年份和月份。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    year_text, month_text = ap_month.upper().split("AP", maxsplit=1)
    return int(year_text), int(month_text)


def recent_ap_months(ap_month: str, max_num: int) -> list[str]:
    """获取当前 AP 月及之前 max_num - 1 个 AP 月字符串。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    year_text, month_text = ap_month.upper().split("AP", maxsplit=1)
    year = int(year_text)
    month = int(month_text)
    base_month_index = year * 12 + month - 2

    ap_months: list[str] = []
    for offset in range(-(max_num - 1), 1):
        month_index = base_month_index + offset
        calendar_year = month_index // 12
        calendar_month = month_index % 12 + 1
        if calendar_month == 12:
            ap_months.append(f"{calendar_year + 1}AP01")
        else:
            ap_months.append(f"{calendar_year}AP{calendar_month + 1:02d}")
    return ap_months


def shift_ap_month(ap_month: str, month_offset: int) -> str:
    """将 AP 月按偏移量前后推移指定月数。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    year_text, month_text = ap_month.upper().split("AP", maxsplit=1)
    year = int(year_text)
    month_num = int(month_text) + month_offset

    while month_num <= 0:
        month_num += 12
        year -= 1
    while month_num > 12:
        month_num -= 12
        year += 1

    return f"{year}AP{month_num:02d}"


def ap_month_to_quarter(ap_month: str) -> str:
    """将 AP 月转换为季度字符串，例如 2026AP05 -> 2026Q2。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    normalized = ap_month.upper()
    year = normalized[:4]
    quarter_num = ((int(normalized[-2:]) - 1) // 3) + 1
    return f"{year}Q{quarter_num}"


def previous_quarter(ap_month: str) -> str:
    """返回给定 AP 月所在季度的上一个季度。"""
    current_quarter = ap_month_to_quarter(ap_month)
    year = int(current_quarter[:4])
    quarter_num = int(current_quarter[-1])
    if quarter_num == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{quarter_num - 1}"


def current_quarter_ap_months(ap_month: str) -> list[str]:
    """返回给定 AP 月所在季度的三个 AP 月。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    normalized = ap_month.upper()
    year = int(normalized[:4])
    month_num = int(normalized[-2:])
    quarter_start_month = ((month_num - 1) // 3) * 3 + 1
    return [f"{year}AP{quarter_start_month + offset:02d}" for offset in range(3)]


def current_quarter_ap_month_tuple(ap_month: str) -> tuple[str, str, str]:
    """返回给定 AP 月所在季度的三个 AP 月元组。"""
    months = current_quarter_ap_months(ap_month)
    return months[0], months[1], months[2]


def ap_month_quarter_position(ap_month: str) -> str:
    """返回给定 AP 月在所属季度中的位置：first/second/third。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")

    position = (int(ap_month.upper()[-2:]) - 1) % 3
    if position == 0:
        return "first"
    if position == 1:
        return "second"
    return "third"


def recent_quarters_from_ap_month(ap_month: str, count: int = 3) -> list[str]:
    """返回给定 AP 月所在季度及之前 count-1 个季度，按时间升序排列。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")
    if count < 1:
        raise ValueError(f"Invalid quarter count: {count}")

    month_num = int(ap_month.upper()[-2:])
    quarter_num = ((month_num - 1) // 3) + 1
    quarter_value = int(ap_month[:4]) * 4 + (quarter_num - 1)
    quarters: list[str] = []
    for offset in range(count - 1, -1, -1):
        current_value = quarter_value - offset
        year = current_value // 4
        quarter = current_value % 4 + 1
        quarters.append(f"{year}Q{quarter}")
    return quarters


def to_short_ap_month(ap_month: str) -> str:
    """转换为短格式 AP 月字符串，例如 "2023AP05" 转为 "23AP05"。"""
    if not valid_ap_month(ap_month):
        raise ValueError(f"Invalid AP month: {ap_month}")
    return f"{ap_month[2:4]}AP{ap_month[6:]}"
