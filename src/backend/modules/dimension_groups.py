"""Scenario-scoped management API for governed analysis dimension groups."""

import json

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from core.models.models import DimensionGroupCreate, DimensionGroupUpdate


router = APIRouter()

GROUP_TYPES = {"time", "categorical", "hierarchy"}
POLICIES = {"auto_fill", "ask_when_ambiguous", "always_ask"}
STATUSES = {"draft", "approved", "deprecated"}
OPTION_STATUSES = {"draft", "approved", "deprecated"}
POLICY_ALIASES = {
    "ask_user": "always_ask",
    "ask": "always_ask",
}


def _refresh_chat_ontology(scenario_id: str) -> None:
    """Invalidate the ChatBI cache after governed DimensionGroup metadata changes."""
    try:
        from agents.ontology_chatbi.prompt import reset_engine

        reset_engine(scenario_id)
    except Exception:
        # The admin API must remain available when ChatBI is not initialized.
        pass


def _normalize_policy(value) -> str:
    """Keep legacy persisted policies writable after the policy vocabulary was renamed."""
    policy = str(value or "ask_when_ambiguous").strip().lower()
    return POLICY_ALIASES.get(policy, policy)


def _rows_as_group(conn, scenario_id: str, row) -> dict:
    group = dict(row)
    group["clarification_policy"] = _normalize_policy(group.get("clarification_policy"))
    group_id = group["id"]
    option_rows = conn.execute(
        """SELECT value, label, aliases, is_default, sort_order, status
           FROM dimension_group_options WHERE scenario_id=? AND group_id=?
           ORDER BY sort_order, value""",
        (scenario_id, group_id),
    ).fetchall()
    mapping_rows = conn.execute(
        """SELECT option_value, class_id, field_name, display_name, priority
           FROM dimension_field_mappings WHERE scenario_id=? AND group_id=?
           ORDER BY priority, id""",
        (scenario_id, group_id),
    ).fetchall()
    metric_rows = conn.execute(
        """SELECT metric_id FROM metric_dimension_bindings
           WHERE scenario_id=? AND group_id=? ORDER BY metric_id""",
        (scenario_id, group_id),
    ).fetchall()
    group["is_required"] = bool(group.get("is_required"))
    group["options"] = [
        {**dict(option), "aliases": json.loads(option.get("aliases") or "[]"), "is_default": bool(option.get("is_default"))}
        for option in option_rows
    ]
    group["field_mappings"] = [dict(mapping) for mapping in mapping_rows]
    group["metric_ids"] = [item["metric_id"] for item in metric_rows]
    return group


def _validate_payload(conn, scenario_id: str, payload: dict) -> None:
    group_type = payload.get("group_type", "categorical")
    if group_type not in GROUP_TYPES:
        raise HTTPException(400, "维度组类型仅支持 time、categorical 或 hierarchy")
    payload["clarification_policy"] = _normalize_policy(
        payload.get("clarification_policy")
    )
    if payload["clarification_policy"] not in POLICIES:
        raise HTTPException(400, "澄清策略无效")
    if payload.get("status", "draft") not in STATUSES:
        raise HTTPException(400, "维度组状态无效")

    options = payload.get("options") or []
    values = [item.value if hasattr(item, "value") else item.get("value", "") for item in options]
    if len(values) != len(set(values)) or any(not value.strip() for value in values):
        raise HTTPException(400, "维度组选项 value 必须非空且唯一")
    default = str(payload.get("default_option") or "").strip()
    if default and default not in values:
        raise HTTPException(400, "默认选项必须属于当前维度组选项")
    if payload.get("status") == "approved" and not options:
        raise HTTPException(400, "已通过的维度组至少需要一个选项")

    concept_id = str(payload.get("concept_id") or "").strip()
    if concept_id and not conn.execute(
        "SELECT 1 FROM concepts WHERE scenario_id=? AND id=?", (scenario_id, concept_id)
    ).fetchone():
        raise HTTPException(400, "关联 Concept 不存在")

    field_rows = conn.execute(
        "SELECT id, fields, properties FROM schema_classes WHERE scenario_id=?", (scenario_id,)
    ).fetchall()
    class_fields: dict[str, set[str]] = {}
    for row in field_rows:
        try:
            fields = json.loads(row.get("fields") or "[]")
        except (TypeError, json.JSONDecodeError):
            fields = []
        names = set(row.get("properties") and json.loads(row.get("properties") or "[]") or [])
        for field in fields:
            if isinstance(field, dict):
                names.update(filter(None, [field.get("name"), field.get("physical_name")]))
        class_fields[row["id"]] = names
    for mapping in payload.get("field_mappings") or []:
        item = mapping.model_dump() if hasattr(mapping, "model_dump") else mapping
        if item.get("option_value") not in values:
            raise HTTPException(400, "字段映射必须引用已定义的选项")
        if item.get("class_id") not in class_fields or item.get("field_name") not in class_fields[item["class_id"]]:
            raise HTTPException(400, f"字段映射无效：{item.get('class_id')}.{item.get('field_name')}")
    for metric_id in payload.get("metric_ids") or []:
        if not conn.execute("SELECT 1 FROM metrics WHERE scenario_id=? AND id=?", (scenario_id, metric_id)).fetchone():
            raise HTTPException(400, f"关联指标不存在：{metric_id}")


def _replace_children(conn, scenario_id: str, group_id: str, payload: dict) -> None:
    conn.execute("DELETE FROM dimension_group_options WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
    conn.execute("DELETE FROM dimension_field_mappings WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
    conn.execute("DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
    for option in payload.get("options") or []:
        item = option.model_dump() if hasattr(option, "model_dump") else option
        conn.execute(
            """INSERT INTO dimension_group_options
               (group_id, scenario_id, value, label, aliases, is_default, sort_order, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (group_id, scenario_id, item["value"], item["label"], json.dumps(item.get("aliases", []), ensure_ascii=False), int(bool(item.get("is_default"))), item.get("sort_order", 0), item.get("status", "approved")),
        )
    for mapping in payload.get("field_mappings") or []:
        item = mapping.model_dump() if hasattr(mapping, "model_dump") else mapping
        conn.execute(
            """INSERT INTO dimension_field_mappings
               (group_id, scenario_id, option_value, class_id, field_name, display_name, priority)
               VALUES (?,?,?,?,?,?,?)""",
            (group_id, scenario_id, item["option_value"], item["class_id"], item["field_name"], item.get("display_name", ""), item.get("priority", 0)),
        )
    for metric_id in dict.fromkeys(payload.get("metric_ids") or []):
        conn.execute("INSERT INTO metric_dimension_bindings (metric_id, scenario_id, group_id) VALUES (?,?,?)", (metric_id, scenario_id, group_id))


@router.get("/api/admin/scenarios/{scenario_id}/dimension-groups")
async def list_dimension_groups(scenario_id: str):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM dimension_groups WHERE scenario_id=? ORDER BY name", (scenario_id,)).fetchall()
        return [_rows_as_group(conn, scenario_id, row) for row in rows]
    finally:
        conn.close()


@router.post("/api/admin/scenarios/{scenario_id}/dimension-groups")
async def create_dimension_group(scenario_id: str, req: DimensionGroupCreate):
    payload = req.model_dump()
    if not req.id.strip() or not req.name.strip():
        raise HTTPException(400, "维度组 ID 和名称必填")
    conn = get_db()
    try:
        _validate_payload(conn, scenario_id, payload)
        conn.execute(
            """INSERT INTO dimension_groups
               (id, scenario_id, name, description, group_type, concept_id, is_required, default_option, clarification_policy, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id.strip(), scenario_id, req.name.strip(), req.description, req.group_type, req.concept_id, int(req.is_required), req.default_option, req.clarification_policy, req.status),
        )
        _replace_children(conn, scenario_id, req.id.strip(), payload)
        conn.commit()
        _refresh_chat_ontology(scenario_id)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(400, f"创建维度组失败：{exc}")
    finally:
        conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/dimension-groups/{group_id}")
async def update_dimension_group(scenario_id: str, group_id: str, req: DimensionGroupUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM dimension_groups WHERE scenario_id=? AND id=?", (scenario_id, group_id)).fetchone()
        if not existing:
            raise HTTPException(404, "维度组不存在")
        current = _rows_as_group(conn, scenario_id, existing)
        incoming = req.model_dump(exclude_unset=True)
        payload = {**current, **incoming}
        _validate_payload(conn, scenario_id, payload)
        conn.execute(
            """UPDATE dimension_groups SET name=?, description=?, group_type=?, concept_id=?, is_required=?, default_option=?, clarification_policy=?, status=?, updated_at=CURRENT_TIMESTAMP
               WHERE scenario_id=? AND id=?""",
            (payload["name"], payload["description"], payload["group_type"], payload["concept_id"], int(bool(payload["is_required"])), payload["default_option"], payload["clarification_policy"], payload["status"], scenario_id, group_id),
        )
        if "options" in incoming or "field_mappings" in incoming or "metric_ids" in incoming:
            _replace_children(conn, scenario_id, group_id, payload)
        conn.commit()
        _refresh_chat_ontology(scenario_id)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(400, f"更新维度组失败：{exc}")
    finally:
        conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/dimension-groups/{group_id}")
async def delete_dimension_group(scenario_id: str, group_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
        conn.execute("DELETE FROM dimension_field_mappings WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
        conn.execute("DELETE FROM dimension_group_options WHERE scenario_id=? AND group_id=?", (scenario_id, group_id))
        conn.execute("DELETE FROM dimension_groups WHERE scenario_id=? AND id=?", (scenario_id, group_id))
        conn.commit()
        _refresh_chat_ontology(scenario_id)
    finally:
        conn.close()
    return {"status": "ok"}