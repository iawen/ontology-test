"""
指标管理 API + 指标查询工具
=============================
CRUD + lookup_metric（供 Chat 工具链调用）
"""

import json
import math

from fastapi import APIRouter, HTTPException, Query

from core.db.db import get_db
from configs.global_config import Cfg
from core.models.models import MetricBatchDelete, MetricCreate, MetricUpdate, ConceptCreate, ConceptUpdate

router = APIRouter()


REVIEW_STATUSES = {"pending", "approved", "rejected"}
SOURCE_SHAPES = {"wide", "long"}
METRIC_FILTER_OPERATORS = {"=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"}


def _normalize_metric_offset(value, label: str) -> float:
    """Validate a post-expression numeric adjustment without allowing SQL text."""
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        raise HTTPException(400, f"{label}必须是有限数字")
    try:
        offset = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label}必须是有限数字") from None
    if not math.isfinite(offset):
        raise HTTPException(400, f"{label}必须是有限数字")
    return offset

def _metric_definition(value) -> dict:
    return value if isinstance(value, dict) else {}


def _physical_metric_definition(definition: dict, class_field_map: dict[str, dict[str, str]]) -> dict:
    """Convert legacy logical metric field names to their physical column names."""
    normalized = json.loads(json.dumps(_metric_definition(definition)))
    input_groups = [normalized.get("inputs", [])]
    input_groups.extend(
        output.get("inputs", []) for output in normalized.get("outputs", [])
        if isinstance(output, dict)
    )
    for inputs in input_groups:
        for item in inputs:
            if not isinstance(item, dict):
                continue
            fields = class_field_map.get(str(item.get("class_id") or "").strip(), {})
            field = str(item.get("field") or "").strip()
            if field in fields:
                item["field"] = fields[field]
            for filter_item in item.get("filters", []):
                if not isinstance(filter_item, dict):
                    continue
                filter_field = str(filter_item.get("field") or "").strip()
                if filter_field in fields:
                    filter_item["field"] = fields[filter_field]
    return normalized


def _validate_metric_definition(scenario_id: str, definition: dict) -> tuple[str, list[str], dict]:
    definition = _metric_definition(definition)
    version = definition.get("version")
    if version not in {1, 2}:
        raise HTTPException(400, "指标定义版本不受支持")
    anchor_class = str(definition.get("anchor_class") or "").strip()
    if not anchor_class:
        raise HTTPException(400, "指标定义必须包含锚点类和至少一个组成项")
    if version == 1:
        operator = str(definition.get("expression_operator") or "").upper()
        if operator not in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "CONCAT"}:
            raise HTTPException(400, "指标表达式操作符不支持")
        offset = _normalize_metric_offset(definition.get("offset"), "指标计算结果调整值")
        if operator == "CONCAT" and offset:
            raise HTTPException(400, "拼接展示不支持计算结果调整值")
        definition["expression_operator"] = operator
        definition["offset"] = offset
    outputs = definition.get("outputs") if version == 2 else None
    if version == 2:
        if not isinstance(outputs, list) or not outputs:
            raise HTTPException(400, "并列指标必须至少包含一个输出")
        names = [str(output.get("output_name") or "").strip() for output in outputs if isinstance(output, dict)]
        if len(names) != len(outputs) or not all(names) or len(set(names)) != len(names):
            raise HTTPException(400, "并列指标输出名称必填且不能重复")
        if any(
            str(output.get("expression_operator") or "").upper() not in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}
            or (
                str(output.get("expression_operator") or "").upper() == "DIVIDE"
                and len(output.get("inputs") or []) != 2
            )
            for output in outputs
            if isinstance(output, dict)
        ):
            raise HTTPException(400, "并列输出仅支持基础运算；相除必须恰好包含分子和分母两个组成项")
        for output in outputs:
            output["offset"] = _normalize_metric_offset(
                output.get("offset"),
                "并列输出计算结果调整值",
            )
    conn = get_db()
    rows = conn.execute("SELECT id, fields, properties FROM schema_classes WHERE scenario_id=?", (scenario_id,)).fetchall()
    conn.close()
    class_fields = {}
    class_field_map = {}
    for row in rows:
        try:
            fields = json.loads(row["fields"] or "[]")
        except (TypeError, json.JSONDecodeError):
            fields = []
        field_map = {}
        for item in fields:
            if not isinstance(item, dict):
                continue
            physical_name = str(item.get("physical_name") or item.get("name") or "").strip()
            logical_name = str(item.get("name") or "").strip()
            if not physical_name:
                continue
            field_map[physical_name] = physical_name
            if logical_name:
                field_map[logical_name] = physical_name
        class_field_map[row["id"]] = field_map
        class_fields[row["id"]] = set(field_map.values())
    definition = _physical_metric_definition(definition, class_field_map)
    if anchor_class not in class_fields:
        raise HTTPException(400, "锚点类不存在")
    source_classes = []
    input_groups = [definition.get("inputs", [])] if version == 1 else [output.get("inputs", []) for output in outputs]
    for inputs in input_groups:
        if not isinstance(inputs, list) or not inputs:
            raise HTTPException(400, "每个指标输出必须包含至少一个组成项")
        for item in inputs:
            if not isinstance(item, dict):
                raise HTTPException(400, "指标组成项格式无效")
            class_id = str(item.get("class_id") or "").strip()
            source_shape = str(item.get("source_shape") or "wide").lower().strip()
            field = str(item.get("field") or "").strip()
            aggregation = str(item.get("aggregation") or "").upper()
            if class_id not in class_fields or not field or field not in class_fields[class_id]:
                raise HTTPException(400, "指标组成项的来源类或字段无效")
            if source_shape not in SOURCE_SHAPES:
                raise HTTPException(400, "指标组成项的 source_shape 仅支持 wide 或 long")
            if aggregation not in {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"}:
                raise HTTPException(400, "指标组成项的聚合方式不支持")
            filters = item.get("filters", [])
            if not isinstance(filters, list):
                raise HTTPException(400, "指标组成项的固定条件必须是数组")
            if source_shape == "long" and not filters:
                raise HTTPException(400, "窄表指标组成项必须配置至少一个固定条件")
            for filter_item in filters:
                if not isinstance(filter_item, dict):
                    raise HTTPException(400, "指标组成项的固定条件格式无效")
                filter_field = str(filter_item.get("field") or "").strip()
                operator = str(filter_item.get("operator") or "").upper().strip()
                if not filter_field or filter_field not in class_fields[class_id]:
                    raise HTTPException(400, "指标组成项的固定条件字段无效")
                if operator not in METRIC_FILTER_OPERATORS:
                    raise HTTPException(400, "指标组成项的固定条件操作符不支持")
                if operator not in {"IS NULL", "IS NOT NULL"} and filter_item.get("value") in (None, "", []):
                    raise HTTPException(400, "指标组成项的固定条件必须包含值")
            source_classes.append(class_id)
    return anchor_class, list(dict.fromkeys(source_classes)), definition


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


def _sync_ontology_files(scenario_id: str):
    from modules.schema import _sync_schema_files
    from prompts.prompt import reset_engine

    try:
        _sync_schema_files(scenario_id)
        reset_engine(scenario_id)
    except Exception as e:
        raise HTTPException(500, f"数据已保存，但同步 schema 文件失败: {e}")


def _validate_dimension_group_ids(conn, scenario_id: str, group_ids: list[str]) -> list[str]:
    """Return unique approved group IDs, rejecting missing or unpublished assets."""
    normalized = list(dict.fromkeys(group_id.strip() for group_id in group_ids if group_id.strip()))
    for group_id in normalized:
        row = conn.execute(
            "SELECT status FROM dimension_groups WHERE scenario_id=? AND id=?",
            (scenario_id, group_id),
        ).fetchone()
        if not row:
            raise HTTPException(400, f"分析维度组不存在：{group_id}")
        if row["status"] != "approved":
            raise HTTPException(400, f"分析维度组尚未通过审核：{group_id}")
    return normalized


def _replace_metric_dimension_bindings(conn, scenario_id: str, metric_id: str, group_ids: list[str]) -> None:
    conn.execute(
        "DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND metric_id=?",
        (scenario_id, metric_id),
    )
    for group_id in group_ids:
        conn.execute(
            "INSERT INTO metric_dimension_bindings (metric_id, scenario_id, group_id) VALUES (?,?,?)",
            (metric_id, scenario_id, group_id),
        )


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
    binding_rows = conn.execute(
        "SELECT metric_id, group_id FROM metric_dimension_bindings WHERE scenario_id=? ORDER BY group_id",
        (scenario_id,),
    ).fetchall()
    bindings: dict[str, list[str]] = {}
    for binding in binding_rows:
        bindings.setdefault(binding["metric_id"], []).append(binding["group_id"])
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["dimensions"] = json.loads(d.get("dimensions", "[]"))
        d["required_dimensions"] = json.loads(d.get("required_dimensions", "[]"))
        d["dimension_group_ids"] = bindings.get(d["id"], [])
        try:
            d["definition"] = json.loads(d.get("definition") or "{}")
        except (TypeError, json.JSONDecodeError):
            d["definition"] = {}
        # Keep the editor compatible with definitions saved before physical names
        # became mandatory. A subsequent save persists the normalized definition.
        try:
            _, _, d["definition"] = _validate_metric_definition(
                scenario_id, d["definition"]
            )
        except HTTPException:
            pass
        # chart_type 可能不存在于旧数据库中
        d.setdefault("chart_type", "bar")
        d["review_status"] = _review_status(d.get("review_status"), d.get("is_reviewed", 0))
        d["is_reviewed"] = _is_reviewed_status(d["review_status"])
        result.append(d)
    return result


@router.get("/api/scenarios/{scenario_id}/metrics/field-values")
async def metric_field_values(
    scenario_id: str,
    class_id: str = Query(...),
    field: str = Query(...),
    q: str = Query("", max_length=100),
    limit: int = Query(100, ge=1, le=500),
):
    """Return bounded DISTINCT values for a configured physical Class field."""
    try:
        from prompts.prompt import init_prompt, get_query_engine

        init_prompt(scenario_id)
        query_engine = get_query_engine(scenario_id)
        if not query_engine.field_available_in_class(class_id, field):
            raise HTTPException(400, "字段不属于指定目标类")
        return query_engine.get_field_distinct_values(class_id, field, limit=limit, search=q)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"加载字段候选值失败: {e}")


@router.post("/api/scenarios/{scenario_id}/metrics")
@router.post("/api/admin/scenarios/{scenario_id}/metrics", include_in_schema=False)
async def create_metric(scenario_id: str, req: MetricCreate):
    conn = get_db()
    review_status = _review_status(req.review_status, req.is_reviewed)
    anchor_class, _, definition = _validate_metric_definition(scenario_id, req.definition)
    try:
        dimension_group_ids = _validate_dimension_group_ids(
            conn, scenario_id, req.dimension_group_ids
        )
        conn.execute(
            """INSERT INTO metrics
                             (id, scenario_id, name, description, category, target_class, definition,
                dimensions, required_dimensions, chart_type, sort_order, is_reviewed, review_status, created_at, updated_at)
                                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (req.id, scenario_id, req.name, req.description, req.category,
                         anchor_class, json.dumps(definition, ensure_ascii=False),
             json.dumps(req.dimensions, ensure_ascii=False),
             json.dumps(req.required_dimensions, ensure_ascii=False),
                 req.chart_type, req.sort_order, _is_reviewed_status(review_status), review_status),
        )
        _replace_metric_dimension_bindings(conn, scenario_id, req.id, dimension_group_ids)
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
    if req.definition is not None:
        anchor_class, _, definition = _validate_metric_definition(scenario_id, req.definition)
        sets.extend(["definition=?", "target_class=?"])
        vals.extend([json.dumps(definition, ensure_ascii=False), anchor_class])
    for k, v in [("name", req.name), ("description", req.description),
                  ("category", req.category),
                  ("chart_type", req.chart_type),
                  ("sort_order", req.sort_order)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.definition is None and req.target_class:
        if not req.target_class.strip():
            conn.close()
            raise HTTPException(400, "目标类必填")
        sets.append("target_class=?")
        vals.append(req.target_class.strip())
    if req.dimensions is not None:
        sets.append("dimensions=?")
        vals.append(json.dumps(req.dimensions, ensure_ascii=False))
    if req.required_dimensions is not None:
        sets.append("required_dimensions=?")
        vals.append(json.dumps(req.required_dimensions, ensure_ascii=False))
    if req.dimension_group_ids is not None:
        dimension_group_ids = _validate_dimension_group_ids(
            conn, scenario_id, req.dimension_group_ids
        )
        _replace_metric_dimension_bindings(conn, scenario_id, metric_id, dimension_group_ids)
        sets.append("updated_at=CURRENT_TIMESTAMP")
    if req.review_status is not None or req.is_reviewed is not None:
        review_status = _review_status(req.review_status, req.is_reviewed)
        sets.append("is_reviewed=?")
        vals.append(_is_reviewed_status(review_status))
        sets.append("review_status=?")
        vals.append(review_status)
    if not sets:
        conn.close()
        return {"status": "ok"}
    if "updated_at=CURRENT_TIMESTAMP" not in sets:
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
    conn.execute("DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND metric_id=?", (scenario_id, metric_id))
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
        conn.executemany(
            "DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND metric_id=?",
            [(scenario_id, metric_id) for metric_id in ids],
        )
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
                "definition": json.loads(r.get("definition") or "{}"),
                "dimensions": json.loads(r["dimensions"]),
                "required_dimensions": json.loads(r.get("required_dimensions", "[]")),
                "chart_type": r.get("chart_type", "bar"),
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