"""
指标管理 API + 指标查询工具
=============================
CRUD + lookup_metric（供 Chat 工具链调用）
"""

import json

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from configs.global_config import Cfg
from core.models.models import MetricBatchDelete, MetricCreate, MetricUpdate, ConceptCreate, ConceptUpdate

router = APIRouter()


REVIEW_STATUSES = {"pending", "approved", "rejected"}


def _reviewed_value(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return value == 1 or value is True


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


def _target_classes(target_class, target_classes=None) -> list[str]:
    values = target_classes if target_classes is not None else target_class
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _row_target_classes(row: dict) -> list[str]:
    try:
        values = json.loads(row.get("target_classes") or "[]")
    except (TypeError, json.JSONDecodeError):
        values = []
    return _target_classes(row.get("target_class", ""), values or None)


def _sync_ontology_files(scenario_id: str):
    from modules.schema import _sync_schema_files

    try:
        _sync_schema_files(scenario_id)
    except Exception as e:
        raise HTTPException(500, f"数据已保存，但同步 schema 文件失败: {e}")


# ============================================================
# 指标 CRUD
# ============================================================

@router.get("/api/scenarios/{scenario_id}/metrics")
@router.get("/api/admin/scenarios/{scenario_id}/metrics", include_in_schema=False)
async def list_metrics(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=? ORDER BY sort_order, category",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["dimensions"] = json.loads(d.get("dimensions", "[]"))
        d["required_dimensions"] = json.loads(d.get("required_dimensions", "[]"))
        d["target_classes"] = _row_target_classes(d)
        d["target_class"] = d["target_classes"][0] if d["target_classes"] else ""
        # chart_type 可能不存在于旧数据库中
        d.setdefault("chart_type", "bar")
        d["review_status"] = _review_status(d.get("review_status"), d.get("is_reviewed", 0))
        d["is_reviewed"] = _is_reviewed_status(d["review_status"])
        result.append(d)
    return result


@router.post("/api/scenarios/{scenario_id}/metrics")
@router.post("/api/admin/scenarios/{scenario_id}/metrics", include_in_schema=False)
async def create_metric(scenario_id: str, req: MetricCreate):
    conn = get_db()
    review_status = _review_status(req.review_status, req.is_reviewed)
    target_classes = _target_classes(req.target_class, req.target_classes)
    if not target_classes:
        conn.close()
        raise HTTPException(400, "目标类必填")
    try:
        conn.execute(
            """INSERT INTO metrics
                             (id, scenario_id, name, description, category, target_class, target_classes,
                calculation, formula, dimensions, required_dimensions,
                                         filters_hint, chart_type, sort_order, is_reviewed, review_status, created_at, updated_at)
                                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name, req.description, req.category,
                         target_classes[0], json.dumps(target_classes, ensure_ascii=False), req.calculation, req.formula,
             json.dumps(req.dimensions, ensure_ascii=False),
             json.dumps(req.required_dimensions, ensure_ascii=False),
                 req.filters_hint, req.chart_type, req.sort_order, _is_reviewed_status(review_status), review_status),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/scenarios/{scenario_id}/metrics/{metric_id}")
@router.put("/api/admin/scenarios/{scenario_id}/metrics/{metric_id}", include_in_schema=False)
async def update_metric(scenario_id: str, metric_id: str, req: MetricUpdate):
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("category", req.category),
                  ("calculation", req.calculation), ("formula", req.formula),
                  ("filters_hint", req.filters_hint), ("chart_type", req.chart_type),
                  ("sort_order", req.sort_order)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.target_classes is not None or req.target_class != "":
        target_classes = _target_classes(req.target_class, req.target_classes)
        if not target_classes:
            conn.close()
            raise HTTPException(400, "目标类必填")
        sets.extend(["target_class=?", "target_classes=?"])
        vals.extend([target_classes[0], json.dumps(target_classes, ensure_ascii=False)])
    if req.dimensions is not None:
        sets.append("dimensions=?")
        vals.append(json.dumps(req.dimensions, ensure_ascii=False))
    if req.required_dimensions is not None:
        sets.append("required_dimensions=?")
        vals.append(json.dumps(req.required_dimensions, ensure_ascii=False))
    if req.review_status is not None or req.is_reviewed is not None:
        review_status = _review_status(req.review_status, req.is_reviewed)
        sets.append("is_reviewed=?")
        vals.append(_is_reviewed_status(review_status))
        sets.append("review_status=?")
        vals.append(review_status)
    if not sets:
        conn.close()
        return {"status": "ok"}
    sets.append("updated_at=CURRENT_TIMESTAMP")
    vals.extend([metric_id, scenario_id])
    conn.execute(f"UPDATE metrics SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}


@router.delete("/api/scenarios/{scenario_id}/metrics/{metric_id}")
@router.delete("/api/admin/scenarios/{scenario_id}/metrics/{metric_id}", include_in_schema=False)
async def delete_metric(scenario_id: str, metric_id: str):
    conn = get_db()
    conn.execute("DELETE FROM metrics WHERE id=? AND scenario_id=?", (metric_id, scenario_id))
    conn.commit()
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}


@router.post("/api/scenarios/{scenario_id}/metrics/batch-delete")
@router.post("/api/admin/scenarios/{scenario_id}/metrics/batch-delete", include_in_schema=False)
async def batch_delete_metrics(scenario_id: str, req: MetricBatchDelete):
    ids = [metric_id for metric_id in req.ids if metric_id]
    if not ids:
        return {"status": "ok", "deleted": 0}

    conn = get_db()
    try:
        cursor = conn.executemany(
            "DELETE FROM metrics WHERE id=? AND scenario_id=?",
            [(metric_id, scenario_id) for metric_id in ids],
        )
        deleted = cursor.rowcount if cursor.rowcount >= 0 else len(ids)
        conn.commit()
    finally:
        conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok", "deleted": deleted}


# ============================================================
# 指标查询工具（供 Chat 工具链调用）
# ============================================================

def lookup_metric(scenario_id: str, metric_name: str) -> dict | None:
    """按名称模糊匹配指标，返回完整指标定义"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=?",
        (scenario_id,)
    ).fetchall()
    conn.close()

    for r in rows:
        if metric_name.lower() in r["name"].lower():
            return {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "category": r["category"],
                "target_class": r["target_class"],
                "target_classes": _row_target_classes(dict(r)),
                "calculation": r["calculation"],
                "formula": r["formula"],
                "dimensions": json.loads(r["dimensions"]),
                "required_dimensions": json.loads(r.get("required_dimensions", "[]")),
                "chart_type": r.get("chart_type", "bar"),
                "filters_hint": r["filters_hint"],
            }
    return None


# ============================================================
# 概念 CRUD（与指标共用此模块）
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/concepts")
async def list_concepts(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM concepts WHERE scenario_id=? ORDER BY level, sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        review_status = _review_status(d.get("review_status"), d.get("is_reviewed", 0))
        result.append(d | {"review_status": review_status, "is_reviewed": review_status == "approved"})
    return result


@router.post("/api/admin/scenarios/{scenario_id}/concepts")
async def create_concept(scenario_id: str, req: ConceptCreate):
    conn = get_db()
    review_status = _review_status(req.review_status, req.is_reviewed)
    try:
        conn.execute(
            """INSERT INTO concepts
               (id, scenario_id, name, description, parent_id, level,
                   concept_type, related_class, sort_order, is_reviewed, review_status, created_at, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name, req.description, req.parent_id,
                 req.level, req.concept_type, req.related_class, req.sort_order, _is_reviewed_status(review_status), review_status),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/concepts/{concept_id}")
async def update_concept(scenario_id: str, concept_id: str, req: ConceptUpdate):
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("parent_id", req.parent_id), ("level", req.level),
                  ("concept_type", req.concept_type), ("related_class", req.related_class),
                  ("sort_order", req.sort_order)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.review_status is not None or req.is_reviewed is not None:
        review_status = _review_status(req.review_status, req.is_reviewed)
        sets.append("is_reviewed=?")
        vals.append(_is_reviewed_status(review_status))
        sets.append("review_status=?")
        vals.append(review_status)
    if not sets:
        conn.close()
        return {"status": "ok"}
    sets.append("updated_at=CURRENT_TIMESTAMP")
    vals.extend([concept_id, scenario_id])
    conn.execute(f"UPDATE concepts SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/concepts/{concept_id}")
async def delete_concept(scenario_id: str, concept_id: str):
    conn = get_db()
    conn.execute("DELETE FROM concepts WHERE id=? AND scenario_id=?", (concept_id, scenario_id))
    conn.commit()
    conn.close()
    _sync_ontology_files(scenario_id)
    return {"status": "ok"}