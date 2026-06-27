import os
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from configs.global_config import Cfg
from core.db.db import get_db
from core.models.models import SchemaClassEdit, SchemaRelationEdit


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
    return bool(value)


def _relationship_exists(conn, scenario_id: str, source: str, target: str, rel_type: str, join_key: str, exclude_id: int | None = None) -> bool:
    sql = """SELECT id FROM schema_relationships
              WHERE scenario_id=? AND source=? AND target=? AND type=? AND COALESCE(join_key, '')=?"""
    params: list = [scenario_id, source, target, rel_type, join_key or ""]
    if exclude_id is not None:
        sql += " AND id<>?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


# ============================================================
# Schema CRUD API — 前端管理面板路径
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/schema/classes")
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
        d["is_reviewed"] = _reviewed_value(d.get("is_reviewed", 0))
        result.append(d)
    return result


@router.post("/api/admin/scenarios/{scenario_id}/schema/classes")
async def admin_create_class(scenario_id: str, req: SchemaClassEdit):
    """管理面板：新增 Schema 类"""
    conn = get_db()
    fields = _normalize_fields(req.fields)
    properties = req.properties or _field_names(fields)
    try:
        conn.execute(
            """INSERT INTO schema_classes
                    (id, scenario_id, name_cn, description, properties, fields, csv_file, primary_key, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name_cn, req.description,
             json.dumps(properties, ensure_ascii=False),
             json.dumps(fields, ensure_ascii=False),
                 req.csv_file, req.primary_key, int(req.is_reviewed)),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()

    # 同步到 schema.json 文件
    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/schema/classes/{class_id}")
async def admin_update_class(scenario_id: str, class_id: str, req: SchemaClassEdit):
    """管理面板：更新 Schema 类"""
    conn = get_db()
    fields = _normalize_fields(req.fields)
    properties = req.properties or _field_names(fields)
    conn.execute(
        """UPDATE schema_classes
              SET name_cn=?, description=?, properties=?, fields=?, csv_file=?, primary_key=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=?""",
        (req.name_cn, req.description,
         json.dumps(properties, ensure_ascii=False),
         json.dumps(fields, ensure_ascii=False),
            req.csv_file, req.primary_key, int(req.is_reviewed),
         class_id, scenario_id),
    )
    conn.commit()
    conn.close()

    # 同步到 schema.json 文件
    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/schema/classes/{class_id}")
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


@router.get("/api/admin/scenarios/{scenario_id}/schema/relationships")
async def admin_list_relationships(scenario_id: str):
    """管理面板：列出场景下所有 Schema 关系"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=? ORDER BY id",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) | {"is_reviewed": _reviewed_value(r.get("is_reviewed", 0))} for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/schema/relationships")
async def admin_create_relationship(scenario_id: str, req: SchemaRelationEdit):
    """管理面板：新增 Schema 关系"""
    conn = get_db()
    if _relationship_exists(conn, scenario_id, req.source, req.target, req.type, req.join_key):
        conn.close()
        raise HTTPException(400, "关系已存在，请勿重复添加")
    try:
        conn.execute(
            """INSERT INTO schema_relationships
                    (scenario_id, source, target, type, join_key, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (scenario_id, req.source, req.target, req.type, req.join_key, int(req.is_reviewed)),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/schema/relationships/{rel_id}")
async def admin_update_relationship(scenario_id: str, rel_id: int, req: SchemaRelationEdit):
    """管理面板：更新 Schema 关系"""
    conn = get_db()
    if _relationship_exists(conn, scenario_id, req.source, req.target, req.type, req.join_key, exclude_id=rel_id):
        conn.close()
        raise HTTPException(400, "关系已存在，请勿重复添加")
    conn.execute(
        """UPDATE schema_relationships
              SET source=?, target=?, type=?, join_key=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=?""",
        (req.source, req.target, req.type, req.join_key, int(req.is_reviewed), rel_id, scenario_id),
    )
    conn.commit()
    conn.close()

    _sync_schema_files(scenario_id)
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/schema/relationships/{rel_id}")
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
# 旧版 API（兼容 Chat 模块调用）
# ============================================================

@router.put("/api/schema/{scenario_id}/classes/{class_id}")
async def update_schema_class(scenario_id:str, class_id: str, req: SchemaClassEdit):
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")

    schema_path = os.path.join(ontology_dir, "schema.json")
    mapping_path = os.path.join(ontology_dir, "schema_mapping.json")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    # Update class in schema
    for cls in schema["classes"]:
        if cls["id"] == class_id:
            cls["name_cn"] = req.name_cn
            cls["description"] = req.description
            cls["properties"] = req.properties
            cls["fields"] = _normalize_fields(req.fields)
            cls["is_reviewed"] = req.is_reviewed
            break

    # Update class in mapping
    if class_id in mapping["classes"]:
        mapping["classes"][class_id]["name_cn"] = req.name_cn
        mapping["classes"][class_id]["csv_file"] = req.csv_file
        mapping["classes"][class_id]["primary_key"] = req.primary_key
        fields = _normalize_fields(req.fields)
        mapping["classes"][class_id]["field_map"] = _field_map(fields, req.properties)
        mapping["classes"][class_id]["field_types"] = _field_types(fields)
        mapping["classes"][class_id]["is_reviewed"] = req.is_reviewed

    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    # 同步到数据库
    conn = get_db()
    conn.execute(
        """UPDATE schema_classes
                SET name_cn=?, description=?, properties=?, fields=?, csv_file=?, primary_key=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=?""",
        (req.name_cn, req.description,
         json.dumps(req.properties, ensure_ascii=False),
            json.dumps(_normalize_fields(req.fields), ensure_ascii=False),
            req.csv_file, req.primary_key, int(req.is_reviewed),
         class_id, scenario_id),
    )
    conn.commit()
    conn.close()

    return {"status": "ok"}


@router.put("/api/schema/{scenario_id}/relationships")
async def update_schema_relationships(scenario_id: str, req: list[SchemaRelationEdit]):
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")

    schema_path = os.path.join(ontology_dir, "schema.json")
    mapping_path = os.path.join(ontology_dir, "schema_mapping.json")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    deduped = []
    seen = set()
    for r in req:
        key = (r.source, r.target, r.type, r.join_key or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    schema["relationships"] = [r.model_dump() for r in deduped]
    mapping["relationships"] = [r.model_dump() for r in deduped]

    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    # 同步到数据库
    conn = get_db()
    conn.execute("DELETE FROM schema_relationships WHERE scenario_id=?", (scenario_id,))
    for r in deduped:
        conn.execute(
            """INSERT INTO schema_relationships
                    (scenario_id, source, target, type, join_key, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (scenario_id, r.source, r.target, r.type, r.join_key, int(r.is_reviewed)),
        )
    conn.commit()
    conn.close()

    return {"status": "ok"}


@router.get("/api/schema/{scenario_id}/classes")
async def list_schema_classes(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_classes WHERE scenario_id=? ORDER BY id",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) | {"properties": _json_list(r["properties"]), "fields": _normalize_fields(_json_list(r["fields"])), "is_reviewed": _reviewed_value(r.get("is_reviewed", 0))} for r in rows]


@router.get("/api/schema/{scenario_id}/relationships")
async def list_schema_relationships(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=? ORDER BY id",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) | {"is_reviewed": _reviewed_value(r.get("is_reviewed", 0))} for r in rows]


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
            "is_reviewed": _reviewed_value(c.get("is_reviewed", 0)),
        })
        mapping_classes[c["id"]] = {
            "csv_file": c["csv_file"],
            "table_name": c["csv_file"].replace(".csv", "") if c["csv_file"] else c["id"],
            "primary_key": c["primary_key"],
            "name_cn": c["name_cn"],
            "field_map": _field_map(fields, properties),
            "field_types": _field_types(fields),
            "data_source": "csv" if c["csv_file"].endswith(".csv") else "database",
            "is_reviewed": _reviewed_value(c.get("is_reviewed", 0)),
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
            "is_reviewed": _reviewed_value(r.get("is_reviewed", 0)),
        }
        parsed_rels.append(rel_item)
        mapping_rels.append(rel_item)

    parsed_metrics = []
    for row in metrics:
        m = dict(row)
        parsed_metrics.append({
            "id": m["id"],
            "name": m["name"],
            "name_cn": m["name"],
            "description": m["description"],
            "category": m["category"],
            "target_class": m["target_class"],
            "calculation": m["calculation"],
            "formula": m["formula"],
            "dimensions": _json_list(m["dimensions"]),
            "required_dimensions": _json_list(m["required_dimensions"]),
            "filters_hint": m["filters_hint"],
            "chart_type": m["chart_type"],
            "sort_order": m["sort_order"],
            "is_reviewed": _reviewed_value(m.get("is_reviewed", 0)),
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
            "is_reviewed": _reviewed_value(c.get("is_reviewed", 0)),
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