import os
import json

from fastapi import APIRouter, HTTPException

from configs.global_config import Cfg
from core.db.db import get_db
from core.models.models import SchemaClassEdit, SchemaRelationEdit
from tools.logger import logger


router = APIRouter()


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _normalize_fields(fields: list) -> list[dict]:
    normalized = []
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        physical_name = str(item.get("physical_name", "")).strip()
        if not name and not physical_name:
            continue
        field_type = str(item.get("type", "text")).strip() or "text"
        if field_type not in {"text", "numeric", "date", "boolean"}:
            field_type = "text"
        normalized.append({
            "name": name or physical_name,
            "physical_name": physical_name or name,
            "type": field_type,
            "description": str(item.get("description", "")).strip(),
            "is_primary_key": bool(item.get("is_primary_key", False)),
            "is_foreign_key": bool(item.get("is_foreign_key", False)),
        })
    return normalized


def _field_names(fields: list[dict]) -> list[str]:
    return [f.get("name") or f.get("physical_name") for f in fields if f.get("name") or f.get("physical_name")]


def _field_map(fields: list[dict], properties: list[str]) -> dict:
    if fields:
        return {f.get("name") or f.get("physical_name"): f.get("physical_name") or f.get("name") for f in fields}
    return {p: p for p in properties}


def _field_types(fields: list[dict]) -> dict:
    return {f.get("physical_name") or f.get("name"): f.get("type", "text") for f in fields}


def _reviewed_value(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return value == 1 or value is True


REVIEW_STATUSES = {"pending", "approved", "rejected"}


def _review_status(value, is_reviewed=False) -> str:
    if isinstance(value, str) and value.lower() == "rejected":
        return "rejected"
    if isinstance(is_reviewed, str) and is_reviewed.lower() in {"-1", "rejected"}:
        return "rejected"
    if is_reviewed == -1:
        return "rejected"
    if (isinstance(value, str) and value.lower() == "approved") or _reviewed_value(is_reviewed):
        return "approved"
    if isinstance(value, str) and value.lower() in REVIEW_STATUSES:
        return value.lower()
    return "pending"


def _is_reviewed_status(value) -> int:
    return {"rejected": -1, "approved": 1}.get(value, 0)


def _relationship_exists(conn, scenario_id: str, source: str, target: str, rel_type: str, source_key: str, target_key: str, exclude_id: int | None = None) -> bool:
    sql = """SELECT id FROM schema_relationships
              WHERE scenario_id=? AND source=? AND target=? AND type=?
                AND COALESCE(source_key, '')=? AND COALESCE(target_key, '')=?"""
    params: list = [scenario_id, source, target, rel_type, source_key or "", target_key or ""]
    if exclude_id is not None:
        sql += " AND id<>?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


# ============================================================
# Schema CRUD API — 前端管理面板路径
# ============================================================

@router.get("/api/scenarios/{scenario_id}/schema/classes")
@router.get("/api/admin/scenarios/{scenario_id}/schema/classes", include_in_schema=False)
async def admin_list_classes(scenario_id: str):
    """管理面板：列出场景下所有 Schema 类"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_classes WHERE scenario_id=? ORDER BY id",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["properties"] = _json_list(d.get("properties", "[]"))
        d["fields"] = _normalize_fields(_json_list(d.get("fields", "[]")))
        d["review_status"] = _review_status(d.get("review_status"), d.get("is_reviewed", 0))
        d["is_reviewed"] = _is_reviewed_status(d["review_status"])
        result.append(d)
    return result


@router.post("/api/scenarios/{scenario_id}/schema/classes")
@router.post("/api/admin/scenarios/{scenario_id}/schema/classes", include_in_schema=False)
async def admin_create_class(scenario_id: str, req: SchemaClassEdit):
    """管理面板：新增 Schema 类"""
    conn = get_db()
    fields = _normalize_fields(req.fields)
    properties = req.properties or _field_names(fields)
    review_status = _review_status(req.review_status, req.is_reviewed)
    try:
        conn.execute(
            """INSERT INTO schema_classes
                    (id, scenario_id, name_cn, description, properties, fields, csv_file, primary_key, is_reviewed, review_status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name_cn, req.description,
             json.dumps(properties, ensure_ascii=False),
             json.dumps(fields, ensure_ascii=False),
                 req.csv_file, req.primary_key, _is_reviewed_status(review_status), review_status),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()

    # 同步到 schema.json 文件
    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/scenarios/{scenario_id}/schema/classes/{class_id}")
@router.put("/api/admin/scenarios/{scenario_id}/schema/classes/{class_id}", include_in_schema=False)
async def admin_update_class(scenario_id: str, class_id: str, req: SchemaClassEdit):
    """管理面板：更新 Schema 类；ID 变更时原子同步其引用。"""
    conn = get_db()
    fields = _normalize_fields(req.fields)
    properties = req.properties or _field_names(fields)
    review_status = _review_status(req.review_status, req.is_reviewed)
    new_class_id = req.id.strip()
    if not new_class_id:
        conn.close()
        raise HTTPException(400, "类 ID 不能为空")
    try:
        current = conn.execute(
            "SELECT id FROM schema_classes WHERE id=? AND scenario_id=?",
            (class_id, scenario_id),
        ).fetchone()
        if current is None:
            raise HTTPException(404, "待更新的类不存在")
        if new_class_id != class_id:
            duplicate = conn.execute(
                "SELECT id FROM schema_classes WHERE id=? AND scenario_id=?",
                (new_class_id, scenario_id),
            ).fetchone()
            if duplicate is not None:
                raise HTTPException(400, f"类 ID 已存在：{new_class_id}")

        conn.execute(
            """UPDATE schema_classes
                      SET id=?, name_cn=?, description=?, properties=?, fields=?, csv_file=?, primary_key=?, is_reviewed=?, review_status=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=?""",
            (new_class_id, req.name_cn, req.description,
             json.dumps(properties, ensure_ascii=False),
             json.dumps(fields, ensure_ascii=False),
             req.csv_file, req.primary_key, _is_reviewed_status(review_status), review_status,
             class_id, scenario_id),
        )
        if new_class_id != class_id:
            conn.execute(
                "UPDATE schema_relationships SET source=?, updated_at=CURRENT_TIMESTAMP WHERE scenario_id=? AND source=?",
                (new_class_id, scenario_id, class_id),
            )
            conn.execute(
                "UPDATE schema_relationships SET target=?, updated_at=CURRENT_TIMESTAMP WHERE scenario_id=? AND target=?",
                (new_class_id, scenario_id, class_id),
            )
            _rename_metric_class_references(conn, scenario_id, class_id, new_class_id)
            conn.execute(
                "UPDATE concepts SET related_class=?, updated_at=CURRENT_TIMESTAMP WHERE scenario_id=? AND related_class=?",
                (new_class_id, scenario_id, class_id),
            )
            conn.execute(
                "UPDATE alert_rules SET target_class=? WHERE scenario_id=? AND target_class=?",
                (new_class_id, scenario_id, class_id),
            )
            logger.info(
                "Schema class renamed: scenario_id=%s old_id=%s new_id=%s; synchronized relationships, metrics, concepts, and alert rules",
                scenario_id,
                class_id,
                new_class_id,
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(400, f"更新失败: {exc}")
    finally:
        conn.close()

    # 同步到 schema.json 文件
    _sync_schema_files(scenario_id)
    return {"status": "ok", "id": new_class_id, "renamed": new_class_id != class_id}


def _rename_metric_class_references(conn, scenario_id: str, old_class_id: str, new_class_id: str) -> None:
    """Replace exact class IDs in both legacy target_class and multi-class target_classes fields."""
    metric_rows = conn.execute(
        "SELECT id, target_class, target_classes FROM metrics WHERE scenario_id=?",
        (scenario_id,),
    ).fetchall()
    for row in metric_rows:
        metric = dict(row)
        target_classes = _json_list(metric.get("target_classes"))
        if not target_classes and metric.get("target_class"):
            target_classes = [metric["target_class"]]
        renamed_classes = [new_class_id if value == old_class_id else value for value in target_classes]
        renamed_classes = list(dict.fromkeys(renamed_classes))
        target_class = new_class_id if metric.get("target_class") == old_class_id else metric.get("target_class", "")
        if renamed_classes:
            target_class = renamed_classes[0]
        if renamed_classes != target_classes or target_class != metric.get("target_class", ""):
            conn.execute(
                """UPDATE metrics SET target_class=?, target_classes=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=?""",
                (target_class, json.dumps(renamed_classes, ensure_ascii=False), metric["id"], scenario_id),
            )


@router.delete("/api/scenarios/{scenario_id}/schema/classes/{class_id}")
@router.delete("/api/admin/scenarios/{scenario_id}/schema/classes/{class_id}", include_in_schema=False)
async def admin_delete_class(scenario_id: str, class_id: str):
    """管理面板：删除 Schema 类"""
    conn = get_db()
    # 同时删除关联的关系
    conn.execute(
        "DELETE FROM schema_relationships WHERE scenario_id=? AND (source=? OR target=?)",
        (scenario_id, class_id, class_id),
    )
    conn.execute(
        "DELETE FROM schema_classes WHERE id=? AND scenario_id=?",
        (class_id, scenario_id),
    )
    conn.commit()
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.get("/api/scenarios/{scenario_id}/schema/relationships")
@router.get("/api/admin/scenarios/{scenario_id}/schema/relationships", include_in_schema=False)
async def admin_list_relationships(scenario_id: str):
    """管理面板：列出场景下所有 Schema 关系"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=? ORDER BY id",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        review_status = _review_status(d.get("review_status"), d.get("is_reviewed", 0))
        result.append(d | {
            "review_status": review_status,
            "is_reviewed": _is_reviewed_status(review_status),
        })
    return result


@router.post("/api/scenarios/{scenario_id}/schema/relationships")
@router.post("/api/admin/scenarios/{scenario_id}/schema/relationships", include_in_schema=False)
async def admin_create_relationship(scenario_id: str, req: SchemaRelationEdit):
    """管理面板：新增 Schema 关系"""
    conn = get_db()
    source_key = req.source_key.strip() or req.join_key.strip()
    target_key = req.target_key.strip() or req.join_key.strip()
    join_key = req.join_key.strip() or (source_key if source_key == target_key else "")
    if _relationship_exists(conn, scenario_id, req.source, req.target, req.type, source_key, target_key):
        conn.close()
        raise HTTPException(400, "关系已存在，请勿重复添加")
    review_status = _review_status(req.review_status, req.is_reviewed)
    try:
        conn.execute(
            """INSERT INTO schema_relationships
                    (scenario_id, source, target, type, source_key, target_key, join_key, description, is_reviewed, review_status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (scenario_id, req.source, req.target, req.type, source_key, target_key,
                 join_key, req.description, _is_reviewed_status(review_status), review_status),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/scenarios/{scenario_id}/schema/relationships/{rel_id}")
@router.put("/api/admin/scenarios/{scenario_id}/schema/relationships/{rel_id}", include_in_schema=False)
async def admin_update_relationship(scenario_id: str, rel_id: int, req: SchemaRelationEdit):
    """管理面板：更新 Schema 关系"""
    conn = get_db()
    source_key = req.source_key.strip() or req.join_key.strip()
    target_key = req.target_key.strip() or req.join_key.strip()
    join_key = req.join_key.strip() or (source_key if source_key == target_key else "")
    if _relationship_exists(conn, scenario_id, req.source, req.target, req.type, source_key, target_key, exclude_id=rel_id):
        conn.close()
        raise HTTPException(400, "关系已存在，请勿重复添加")
    review_status = _review_status(req.review_status, req.is_reviewed)
    conn.execute(
        """UPDATE schema_relationships
              SET source=?, target=?, type=?, source_key=?, target_key=?, join_key=?, description=?, is_reviewed=?, review_status=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=?""",
        (req.source, req.target, req.type, source_key, target_key, join_key,
         req.description, _is_reviewed_status(review_status), review_status, rel_id, scenario_id),
    )
    conn.commit()
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.delete("/api/scenarios/{scenario_id}/schema/relationships/{rel_id}")
@router.delete("/api/admin/scenarios/{scenario_id}/schema/relationships/{rel_id}", include_in_schema=False)
async def admin_delete_relationship(scenario_id: str, rel_id: int):
    """管理面板：删除 Schema 关系"""
    conn = get_db()
    conn.execute(
        "DELETE FROM schema_relationships WHERE id=? AND scenario_id=?",
        (rel_id, scenario_id),
    )
    conn.commit()
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


# ============================================================
# 内部工具：同步数据库 → schema.json / schema_mapping.json
# ============================================================

def _sync_schema_files(scenario_id: str):
    """将数据库中的 schema 数据同步到 JSON 文件"""
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")
    conn = get_db()
    classes = conn.execute(
        "SELECT * FROM schema_classes WHERE scenario_id=?", (scenario_id,)
    ).fetchall()
    rels = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=?", (scenario_id,)
    ).fetchall()
    metrics = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=?", (scenario_id,)
    ).fetchall()
    concepts = conn.execute(
        "SELECT * FROM concepts WHERE scenario_id=?", (scenario_id,)
    ).fetchall()
    conn.close()

    parsed_classes = []
    mapping_classes = {}
    for row in classes:
        c = dict(row)
        properties = _json_list(c["properties"])
        fields = _normalize_fields(_json_list(c["fields"]))
        if not properties:
            properties = _field_names(fields)
        parsed_classes.append({
            "id": c["id"],
            "name_cn": c["name_cn"],
            "description": c["description"],
            "properties": properties,
            "primary_key": c["primary_key"],
            "csv_file": c["csv_file"],
            "fields": fields,
            "is_reviewed": _is_reviewed_status(
                _review_status(c.get("review_status"), c.get("is_reviewed", 0))
            ),
            "review_status": _review_status(c.get("review_status"), c.get("is_reviewed", 0)),
        })
        mapping_classes[c["id"]] = {
            "csv_file": c["csv_file"],
            "table_name": c["csv_file"].replace(".csv", "") if c["csv_file"] else c["id"],
            "primary_key": c["primary_key"],
            "name_cn": c["name_cn"],
            "field_map": _field_map(fields, properties),
            "field_types": _field_types(fields),
            "data_source": "csv" if c["csv_file"].endswith(".csv") else "database",
            "is_reviewed": _is_reviewed_status(
                _review_status(c.get("review_status"), c.get("is_reviewed", 0))
            ),
            "review_status": _review_status(c.get("review_status"), c.get("is_reviewed", 0)),
        }

    parsed_rels = []
    mapping_rels = []
    for row in rels:
        r = dict(row)
        join_key = r["join_key"] or (r["source_key"] if r["source_key"] == r["target_key"] else "")
        rel_item = {
            "source": r["source"],
            "target": r["target"],
            "type": r["type"],
            "source_key": r["source_key"] or join_key,
            "target_key": r["target_key"] or join_key,
            "join_key": join_key,
            "description": r["description"],
            "is_reviewed": _is_reviewed_status(
                _review_status(r.get("review_status"), r.get("is_reviewed", 0))
            ),
            "review_status": _review_status(r.get("review_status"), r.get("is_reviewed", 0)),
        }
        parsed_rels.append(rel_item)
        mapping_rels.append(rel_item)

    parsed_metrics = []
    for row in metrics:
        m = dict(row)
        target_classes = _json_list(m.get("target_classes", "[]"))
        if not target_classes and m.get("target_class"):
            target_classes = [m["target_class"]]
        parsed_metrics.append({
            "id": m["id"],
            "name": m["name"],
            "name_cn": m["name"],
            "description": m["description"],
            "category": m["category"],
            "target_class": m["target_class"],
            "target_classes": target_classes,
            "calculation": m["calculation"],
            "formula": m["formula"],
            "dimensions": _json_list(m["dimensions"]),
            "required_dimensions": _json_list(m["required_dimensions"]),
            "filters_hint": m["filters_hint"],
            "chart_type": m["chart_type"],
            "sort_order": m["sort_order"],
            "is_reviewed": _is_reviewed_status(
                _review_status(m.get("review_status"), m.get("is_reviewed", 0))
            ),
            "review_status": _review_status(m.get("review_status"), m.get("is_reviewed", 0)),
        })

    parsed_concepts = []
    for row in concepts:
        c = dict(row)
        parsed_concepts.append({
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "parent_id": c["parent_id"],
            "level": c["level"],
            "concept_type": c["concept_type"],
            "related_class": c["related_class"],
            "sort_order": c["sort_order"],
            "is_reviewed": _is_reviewed_status(
                _review_status(c.get("review_status"), c.get("is_reviewed", 0))
            ),
            "review_status": _review_status(c.get("review_status"), c.get("is_reviewed", 0)),
        })

    schema = {
        "business_name": scenario_id,
        "classes": parsed_classes,
        "relationships": parsed_rels,
        "concepts": parsed_concepts,
        "metrics": parsed_metrics,
    }
    mapping = {
        "classes": mapping_classes,
        "relationships": mapping_rels,
    }

    os.makedirs(ontology_dir, exist_ok=True)
    with open(os.path.join(ontology_dir, "schema.json"), "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ontology_dir, "schema_mapping.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)