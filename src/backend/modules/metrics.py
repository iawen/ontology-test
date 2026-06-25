"""
指标管理 API + 指标查询工具
=============================
CRUD + lookup_metric（供 Chat 工具链调用）
"""

import json

from fastapi import APIRouter, HTTPException

from tools.db import get_db
from configs.global_config import Cfg
from modules.models import MetricCreate, MetricUpdate, ConceptCreate, ConceptUpdate

router = APIRouter()


def _reviewed_value(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


# ============================================================
# 指标 CRUD
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/metrics")
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
        # chart_type 可能不存在于旧数据库中
        d.setdefault("chart_type", "bar")
        d["is_reviewed"] = _reviewed_value(d.get("is_reviewed", 0))
        result.append(d)
    return result


@router.post("/api/admin/scenarios/{scenario_id}/metrics")
async def create_metric(scenario_id: str, req: MetricCreate):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO metrics
               (id, scenario_id, name, description, category, target_class,
                calculation, formula, dimensions, required_dimensions,
                     filters_hint, chart_type, sort_order, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name, req.description, req.category,
             req.target_class, req.calculation, req.formula,
             json.dumps(req.dimensions, ensure_ascii=False),
             json.dumps(req.required_dimensions, ensure_ascii=False),
                 req.filters_hint, req.chart_type, req.sort_order, int(req.is_reviewed)),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/metrics/{metric_id}")
async def update_metric(scenario_id: str, metric_id: str, req: MetricUpdate):
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("category", req.category), ("target_class", req.target_class),
                  ("calculation", req.calculation), ("formula", req.formula),
                  ("filters_hint", req.filters_hint), ("chart_type", req.chart_type),
                  ("sort_order", req.sort_order)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.dimensions is not None:
        sets.append("dimensions=?")
        vals.append(json.dumps(req.dimensions, ensure_ascii=False))
    if req.required_dimensions is not None:
        sets.append("required_dimensions=?")
        vals.append(json.dumps(req.required_dimensions, ensure_ascii=False))
    if req.is_reviewed is not None:
        sets.append("is_reviewed=?")
        vals.append(int(req.is_reviewed))
    if not sets:
        conn.close()
        return {"status": "ok"}
    sets.append("updated_at=CURRENT_TIMESTAMP")
    vals.extend([metric_id, scenario_id])
    conn.execute(f"UPDATE metrics SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/metrics/{metric_id}")
async def delete_metric(scenario_id: str, metric_id: str):
    conn = get_db()
    conn.execute("DELETE FROM metrics WHERE id=? AND scenario_id=?", (metric_id, scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


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
    return [dict(r) | {"is_reviewed": _reviewed_value(r.get("is_reviewed", 0))} for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/concepts")
async def create_concept(scenario_id: str, req: ConceptCreate):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO concepts
               (id, scenario_id, name, description, parent_id, level,
                   concept_type, related_class, sort_order, is_reviewed, created_at, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name, req.description, req.parent_id,
                 req.level, req.concept_type, req.related_class, req.sort_order, int(req.is_reviewed)),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/concepts/{concept_id}")
async def update_concept(scenario_id: str, concept_id: str, req: ConceptUpdate):
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("parent_id", req.parent_id), ("level", req.level),
                  ("concept_type", req.concept_type), ("related_class", req.related_class),
                  ("sort_order", req.sort_order), ("is_reviewed", req.is_reviewed)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        conn.close()
        return {"status": "ok"}
    sets.append("updated_at=CURRENT_TIMESTAMP")
    vals.extend([concept_id, scenario_id])
    conn.execute(f"UPDATE concepts SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/concepts/{concept_id}")
async def delete_concept(scenario_id: str, concept_id: str):
    conn = get_db()
    conn.execute("DELETE FROM concepts WHERE id=? AND scenario_id=?", (concept_id, scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}