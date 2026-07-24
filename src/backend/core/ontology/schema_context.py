import json
from typing import Any

from core.db.db import get_db


DEFAULT_SCHEMA_CONTEXT_CHAR_LIMIT = 12000
DEFAULT_LIMITS = {
    "reviewed_classes": 30,
    "reviewed_relationships": 50,
    "reviewed_metrics": 50,
    "reviewed_concepts": 50,
    "existing_classes": 20,
    "existing_relationships": 30,
    "existing_metrics": 30,
    "existing_concepts": 30,
    "fields_per_class": 20,
}


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _row_to_dict(row) -> dict:
    return {key: row[key] for key in row.keys()} if row else {}


def _compact_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _compact_fields(fields_value: Any, limit: int) -> list[dict]:
    fields = _json_list(fields_value)
    compacted = []
    for field in fields[:limit]:
        if not isinstance(field, dict):
            continue
        is_legacy_field = bool(field.get("physical_name"))
        compacted.append({
            "name_cn": field.get("name") if is_legacy_field else field.get("name_cn") or field.get("name") or "",
            "name": field.get("physical_name") if is_legacy_field else field.get("name") or "",
            "type": field.get("type", "text"),
        })
    return compacted


def _split_reviewed(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    reviewed = []
    existing = []
    for row in rows:
        if row.get("review_status") == "rejected":
            continue
        if row.get("review_status") == "approved" or bool(row.get("is_reviewed")):
            reviewed.append(row)
        else:
            existing.append(row)
    return reviewed, existing


def _take(rows: list[dict], limit: int) -> list[dict]:
    return rows[:max(0, limit)]


def load_schema_reference_context(
    scenario_id: str,
    max_chars: int = DEFAULT_SCHEMA_CONTEXT_CHAR_LIMIT,
    limits: dict | None = None,
) -> dict:
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    conn = get_db()
    try:
        class_rows = [_row_to_dict(row) for row in conn.execute(
                """SELECT id, name_cn, description, primary_key, table_name, fields, is_reviewed, review_status
               FROM schema_classes WHERE scenario_id=?
               ORDER BY is_reviewed DESC, updated_at DESC, id""",
            (scenario_id,),
        ).fetchall()]
        metric_rows = [_row_to_dict(row) for row in conn.execute(
                """SELECT id, name, description, category, target_class, definition, dimensions, required_dimensions, is_reviewed, review_status
               FROM metrics WHERE scenario_id=?
               ORDER BY is_reviewed DESC, updated_at DESC, id""",
            (scenario_id,),
        ).fetchall()]
        relationship_rows = [_row_to_dict(row) for row in conn.execute(
            """SELECT source, target, type, source_key, target_key, join_key, description, is_reviewed
               FROM schema_relationships WHERE scenario_id=?
               ORDER BY is_reviewed DESC, source, target""",
            (scenario_id,),
        ).fetchall()]
        concept_rows = [_row_to_dict(row) for row in conn.execute(
                """SELECT id, name, description, parent_id, level, concept_type, related_class, is_reviewed, review_status
               FROM concepts WHERE scenario_id=?
               ORDER BY is_reviewed DESC, level, id""",
            (scenario_id,),
        ).fetchall()]
    finally:
        conn.close()

    reviewed_classes, existing_classes = _split_reviewed(class_rows)
    reviewed_metrics, existing_metrics = _split_reviewed(metric_rows)
    reviewed_relationships, existing_relationships = _split_reviewed(relationship_rows)
    reviewed_concepts, existing_concepts = _split_reviewed(concept_rows)
    if not any((class_rows, metric_rows, relationship_rows, concept_rows)):
        return {}

    context = {
        "policy": "reviewed assets are authoritative; existing unreviewed assets are merge context and should be updated incrementally, not blindly replaced",
        "reviewed": {
            "classes": [_compact_class(row, limits) for row in _take(reviewed_classes, limits["reviewed_classes"])],
            "relationships": [_compact_relationship(row) for row in _take(reviewed_relationships, limits["reviewed_relationships"])],
            "metrics": [_compact_metric(row) for row in _take(reviewed_metrics, limits["reviewed_metrics"])],
            "concepts": [_compact_concept(row) for row in _take(reviewed_concepts, limits["reviewed_concepts"])],
        },
        "existing_unreviewed": {
            "classes": [_compact_class(row, limits) for row in _take(existing_classes, limits["existing_classes"])],
            "relationships": [_compact_relationship(row) for row in _take(existing_relationships, limits["existing_relationships"])],
            "metrics": [_compact_metric(row) for row in _take(existing_metrics, limits["existing_metrics"])],
            "concepts": [_compact_concept(row) for row in _take(existing_concepts, limits["existing_concepts"])],
        },
        "counts": {
            "reviewed": {
                "classes": len(reviewed_classes),
                "relationships": len(reviewed_relationships),
                "metrics": len(reviewed_metrics),
                "concepts": len(reviewed_concepts),
            },
            "existing_unreviewed": {
                "classes": len(existing_classes),
                "relationships": len(existing_relationships),
                "metrics": len(existing_metrics),
                "concepts": len(existing_concepts),
            },
        },
    }
    return _fit_context(context, max_chars)


def build_business_context(
    business_name: str,
    schema_reference_context: dict | str | None = None,
    max_chars: int = DEFAULT_SCHEMA_CONTEXT_CHAR_LIMIT,
) -> str:
    if not schema_reference_context:
        return business_name
    if isinstance(schema_reference_context, str):
        reference_text = schema_reference_context.strip()
    else:
        reference_text = json.dumps(schema_reference_context, ensure_ascii=False, indent=2)
    if not reference_text:
        return business_name
    header = (
        f"{business_name}\n\n"
        "## 已有本体资产参考（已压缩）\n"
        "reviewed 中的资产是人工审核确认过的高可信业务口径，只能学习、参考、对齐和补充，"
        "不要改写其 ID、字段口径、指标公式、关系键或概念归属。"
        "existing_unreviewed 中的资产是此前提取或优化得到的已有上下文，后续处理应增量更新或合并，"
        "不要因为本轮输入未覆盖就删除或重建。若新结果与 reviewed 冲突，以 reviewed 为准。\n"
    )
    marker = "\n...<schema reference truncated by prompt budget>"
    reference_budget = max(0, max_chars - len(header) - len(marker))
    if len(reference_text) > reference_budget:
        reference_text = reference_text[:reference_budget] + marker
    return header + reference_text


def _compact_class(row: dict, limits: dict) -> dict:
    return {
        "id": row.get("id", ""),
        "name_cn": row.get("name_cn", ""),
        "description": _compact_text(row.get("description", "")),
        "primary_key": row.get("primary_key", ""),
        "table_name": row.get("table_name", ""),
        "fields": _compact_fields(row.get("fields"), limits["fields_per_class"]),
    }


def _compact_metric(row: dict) -> dict:
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "category": row.get("category", ""),
        "target_class": row.get("target_class", ""),
        "definition": _json_dict(row.get("definition")),
        "dimensions": _json_list(row.get("dimensions"))[:12],
        "required_dimensions": _json_list(row.get("required_dimensions"))[:12],
        "description": _compact_text(row.get("description", "")),
    }


def _compact_relationship(row: dict) -> dict:
    return {
        "source": row.get("source", ""),
        "target": row.get("target", ""),
        "type": row.get("type", ""),
        "source_key": row.get("source_key", ""),
        "target_key": row.get("target_key", ""),
        "join_key": row.get("join_key", ""),
    }


def _compact_concept(row: dict) -> dict:
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "parent_id": row.get("parent_id", ""),
        "level": row.get("level", 0),
        "concept_type": row.get("concept_type", ""),
        "related_class": row.get("related_class", ""),
    }


def _fit_context(context: dict, max_chars: int) -> dict:
    context["truncated"] = False
    if len(json.dumps(context, ensure_ascii=False)) <= max_chars:
        return context

    trimmed = json.loads(json.dumps(context, ensure_ascii=False))
    trimmed["truncated"] = True
    trim_order = [
        ("existing_unreviewed", "concepts"),
        ("existing_unreviewed", "relationships"),
        ("existing_unreviewed", "metrics"),
        ("existing_unreviewed", "classes"),
        ("reviewed", "concepts"),
        ("reviewed", "relationships"),
        ("reviewed", "metrics"),
        ("reviewed", "classes"),
    ]
    for group, key in trim_order:
        items = trimmed.get(group, {}).get(key, [])
        while items and len(json.dumps(trimmed, ensure_ascii=False)) > max_chars:
            items.pop()
    return trimmed
