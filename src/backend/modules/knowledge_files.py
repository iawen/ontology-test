import os
import sys
import json
import shutil
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks

from configs.global_config import Cfg
from prompts.prompt import reset_engine
from core.db.db import get_db
from core.ontology.extract_ontology import OntologyExtractor
from core.ontology.schema_context import load_schema_reference_context
from modules.extraction_logs import finish_extraction_log, start_extraction_log


router = APIRouter()

extract_status: dict = {"running": False, "phase": "", "progress": 0, "total": 0, "message": "", "result": None}


@router.post("/api/upload/{scenario_id}")
def upload_file(scenario_id: str, files: List[UploadFile] = File(...)):
    """上传 CSV 文件到 data 目录"""
    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    os.makedirs(data_dir, exist_ok=True)

    uploaded_files = []
    for file in files:
        dest = os.path.join(data_dir, file.filename)
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        uploaded_files.append(file.filename)
    return {"status": "ok", "filenames": uploaded_files}


# 前端兼容路径：/api/scenarios/{id}/upload → 复用 upload_file
@router.post("/api/scenarios/{scenario_id}/upload")
def upload_file_alias(scenario_id: str, files: List[UploadFile] = File(...)):
    """上传 CSV 文件到 data 目录（前端兼容路径）"""
    return upload_file(scenario_id, files)


@router.get("/api/scenarios/{scenario_id}/files")
async def list_files(scenario_id: str):
    """列出当前 scenario 目录中的所有文件"""

    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    files = []
    for p in sorted(Path(data_dir).glob("*.csv")):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                cols = f.readline().strip().split(",")
                row_count = sum(1 for _ in f)
            files.append({"name": p.name, "size": p.stat().st_size, "row_count": row_count, "columns": cols})
        except: pass
    return {"files": files}


@router.delete("/api/scenarios/{scenario_id}/files/{filename}")
async def delete_file(scenario_id:str, filename: str):
    """删除 data 目录中的文件"""
    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    if not os.path.exists(data_dir):
        raise HTTPException(404, "场景不存在")
    
    fp = os.path.join(data_dir, filename)
    if os.path.exists(fp):
        os.remove(fp)
    return {"status": "ok"}


@router.get("/api/scenarios/{scenario_id}/files/{filename}/preview")
async def preview_file(scenario_id: str, filename: str):
    """预览 CSV 文件的前 100 行数据"""
    import csv
    import io

    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    fp = os.path.join(data_dir, filename)
    if not os.path.exists(fp):
        raise HTTPException(404, "文件不存在")

    try:
        with open(fp, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            rows = []
            for i, row in enumerate(reader):
                if i >= 100:
                    break
                rows.append(dict(row))

            # 计算总行数
            total_rows = len(rows)
            with open(fp, "r", encoding="utf-8-sig") as f2:
                total_rows = sum(1 for _ in f2) - 1  # 减去表头

        return {"columns": columns, "rows": rows, "total_rows": total_rows}
    except Exception as e:
        raise HTTPException(500, f"读取文件失败: {str(e)}")


@router.post("/api/scenarios/{scenario_id}/extract")
async def start_extraction(scenario_id: str, background_tasks: BackgroundTasks, body: dict = None):
    """启动分批提取（异步后台任务），支持 CSV 和数据库直连"""
    global extract_status

    reset_engine(scenario_id)
    
    if extract_status["running"]:
        return {"error": "提取正在进行中，请等待完成"}

    conn = get_db()
    row = conn.execute("SELECT name FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "场景不存在")

    business_name = row["name"]
    batch_size = (body or {}).get("batch_size", 5)
    data_source = (body or {}).get("data_source", "auto")  # auto / csv / database
    db_connection_url = (body or {}).get("db_connection_url", "")
    db_connection_id = (body or {}).get("db_connection_id", "")
    selected_files = (body or {}).get("selected_files") or []
    selected_tables = (body or {}).get("selected_tables") or []

    if db_connection_id and not db_connection_url:
        conn = get_db()
        db_row = conn.execute(
            "SELECT connection_url FROM data_connections WHERE id=? AND scenario_id=?",
            (db_connection_id, scenario_id),
        ).fetchone()
        conn.close()
        if not db_row:
            raise HTTPException(404, "数据库连接不存在")
        db_connection_url = db_row["connection_url"]
        data_source = "database"

    # 自动检测：如果场景有活跃的数据库连接且没有指定数据源，优先使用数据库
    if data_source in ("auto", "database") and not db_connection_url:
        try:
            from modules.data_connections import get_active_connection
            active_conn = get_active_connection(scenario_id)
            if active_conn:
                db_connection_url = active_conn["connection_url"]
                data_source = "database"
        except Exception:
            pass

    if data_source == "database" and not db_connection_url:
        raise HTTPException(400, "未找到可用的数据库连接")

    if data_source == "csv" and not selected_files:
        raise HTTPException(400, "请先选择要提取的 CSV 文件")
    if data_source == "database" and not selected_tables:
        raise HTTPException(400, "请先选择要提取的数据库表")

    # 如果明确指定 csv 或没有数据库连接，使用 CSV 模式
    if data_source == "csv":
        db_connection_url = ""

    source_label = "数据库" if db_connection_url else "CSV文件"
    selected_count = len(selected_tables) if db_connection_url else len(selected_files)
    start_message = f"启动中... (数据源: {source_label}，选中 {selected_count} 项)"
    log_id = start_extraction_log(scenario_id, "ontology", trigger="manual", message=start_message)
    extract_status = {"running": True, "phase": "starting", "progress": 0, "total": 0,
                      "message": start_message, "result": None}

    # 在后台运行
    # asyncio.create_task(_run_extraction(scenario_id, business_name, batch_size,
    #                                      db_connection_url=db_connection_url))
    background_tasks.add_task(
        _run_extraction,
        scenario_id,
        business_name,
        batch_size,
        db_connection_url,
        log_id,
        selected_files,
        selected_tables,
    )
    return {"status": "started", "data_source": "database" if db_connection_url else "csv"}


@router.get("/api/extract/stream")
async def stream_extract_status():
    """SSE 流式推送提取进度（实时同步）"""
    from fastapi.responses import StreamingResponse
    import asyncio

    async def generate():
        global extract_status
        last_status = None

        while True:
            current_status = {
                "running": extract_status["running"],
                "phase": extract_status["phase"],
                "progress": extract_status["progress"],
                "total": extract_status["total"],
                "message": extract_status["message"],
            }

            # 只在状态变化时发送
            if current_status != last_status:
                yield f"data: {json.dumps(current_status, ensure_ascii=False)}\n\n"
                last_status = current_status

            # 如果已完成或出错，发送最后一条消息后结束
            if not extract_status["running"] and extract_status["phase"] in ("done", "error"):
                break

            await asyncio.sleep(0.5)  # 每 0.5 秒检查一次

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

# ============================================================
# Helper: 同步 schema.json → SQLite
# ============================================================
def _reviewed_value(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _json_text(value, default=None) -> str:
    if value is None:
        value = default if default is not None else []
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _relationship_key(row: dict) -> tuple:
    source_key = row.get("source_key") or row.get("join_key") or ""
    target_key = row.get("target_key") or row.get("join_key") or ""
    join_key = row.get("join_key") or (source_key if source_key == target_key else "")
    return (
        row.get("source", ""),
        row.get("target", ""),
        row.get("type", ""),
        source_key,
        target_key,
        join_key,
    )


def _dedupe_by_id(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items or []:
        item_id = item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _safe_rowcount(cursor) -> int:
    count = getattr(cursor, "rowcount", 0)
    return count if isinstance(count, int) and count >= 0 else 0


def _format_sync_summary(stats: dict) -> str:
    inserted = stats["inserted"]
    skipped_reviewed = stats["skipped_reviewed"]
    skipped_duplicates = stats["skipped_duplicates"]
    preserved_reviewed = stats["preserved_reviewed"]
    final_total = stats["final_total"]
    return (
        "提取完成，Schema 已增量同步到数据库。"
        f"更新：Class {inserted['classes']} 个、关系 {inserted['relationships']} 个、"
        f"概念 {inserted['concepts']} 个、指标 {inserted['metrics']} 个；"
        f"保留已审核：Class {preserved_reviewed['classes']} 个、关系 {preserved_reviewed['relationships']} 个、"
        f"概念 {preserved_reviewed['concepts']} 个、指标 {preserved_reviewed['metrics']} 个；"
        f"跳过已审核冲突：Class {skipped_reviewed['classes']} 个、关系 {skipped_reviewed['relationships']} 个、"
        f"概念 {skipped_reviewed['concepts']} 个、指标 {skipped_reviewed['metrics']} 个；"
        f"跳过重复：Class {skipped_duplicates['classes']} 个、关系 {skipped_duplicates['relationships']} 个、"
        f"概念 {skipped_duplicates['concepts']} 个、指标 {skipped_duplicates['metrics']} 个；"
        f"当前总量：Class {final_total['classes']} 个、关系 {final_total['relationships']} 个、"
        f"概念 {final_total['concepts']} 个、指标 {final_total['metrics']} 个。"
    )


def _sync_schema_db(scenario_id: str):
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")
    with open(os.path.join(ontology_dir, "schema.json"), "r", encoding="utf-8") as f:
        schema = json.loads(f.read())

    raw_classes = schema.get("classes", []) or []
    raw_relationships = schema.get("relationships", []) or []
    raw_concepts = schema.get("concepts", []) or []
    raw_metrics = schema.get("metrics", []) or []

    conn = get_db()
    reviewed_classes = {
        row["id"] for row in conn.execute(
            "SELECT id FROM schema_classes WHERE scenario_id=? AND is_reviewed IS TRUE",
            (scenario_id,),
        ).fetchall()
    }
    reviewed_metrics = {
        row["id"] for row in conn.execute(
            "SELECT id FROM metrics WHERE scenario_id=? AND is_reviewed IS TRUE",
            (scenario_id,),
        ).fetchall()
    }
    reviewed_concepts = {
        row["id"] for row in conn.execute(
            "SELECT id FROM concepts WHERE scenario_id=? AND is_reviewed IS TRUE",
            (scenario_id,),
        ).fetchall()
    }
    reviewed_rels = {
        _relationship_key(dict(row)) for row in conn.execute(
            "SELECT source, target, type, source_key, target_key, join_key FROM schema_relationships WHERE scenario_id=? AND is_reviewed IS TRUE",
            (scenario_id,),
        ).fetchall()
    }
    reviewed_rel_pairs = {
        (row["source"], row["target"]) for row in conn.execute(
            "SELECT source, target FROM schema_relationships WHERE scenario_id=? AND is_reviewed IS TRUE",
            (scenario_id,),
        ).fetchall()
    }

    # 增量刷新：不删除已有资产；未审核资产允许更新，人工审核过的数据只作为参考并被保护。
    removed_unreviewed = {"classes": 0, "relationships": 0, "concepts": 0, "metrics": 0}

    skipped_reviewed = {"classes": 0, "relationships": 0, "concepts": 0, "metrics": 0}
    skipped_duplicates = {"classes": 0, "relationships": 0, "concepts": 0, "metrics": 0}

    existing_classes = {
        row["id"] for row in conn.execute(
            "SELECT id FROM schema_classes WHERE scenario_id=?",
            (scenario_id,),
        ).fetchall()
    }
    classes = []
    seen_class_ids = set()
    for c in raw_classes:
        class_id = c.get("id")
        if not class_id:
            continue
        if class_id in seen_class_ids:
            skipped_duplicates["classes"] += 1
            continue
        seen_class_ids.add(class_id)
        if c["id"] in reviewed_classes:
            skipped_reviewed["classes"] += 1
            continue
        fields = c.get("fields", [])
        properties = c.get("properties") or [
            f.get("name") or f.get("physical_name") for f in fields if isinstance(f, dict) and (f.get("name") or f.get("physical_name"))
        ]
        classes.append((
            c["id"], scenario_id, c.get("name_cn", c["id"]), c.get("description", ""),
            _json_text(properties), c.get("primary_key", ""), c.get("csv_file", ""),
            _json_text(fields), _reviewed_value(c.get("is_reviewed", False)),
        ))

    existing_rels = {
        _relationship_key(dict(row)) for row in conn.execute(
            "SELECT source, target, type, source_key, target_key, join_key FROM schema_relationships WHERE scenario_id=?",
            (scenario_id,),
        ).fetchall()
    }
    existing_rel_pairs = {
        (row["source"], row["target"]) for row in conn.execute(
            "SELECT source, target FROM schema_relationships WHERE scenario_id=?",
            (scenario_id,),
        ).fetchall()
    }
    rels = []
    seen_rels = set(reviewed_rels)
    seen_extracted_rels = set()
    for r in raw_relationships:
        key = _relationship_key(r)
        if key in seen_extracted_rels:
            skipped_duplicates["relationships"] += 1
            continue
        seen_extracted_rels.add(key)
        if key in reviewed_rels:
            skipped_reviewed["relationships"] += 1
            continue
        source, target, rel_type, source_key, target_key, join_key = key
        if (source, target) in reviewed_rel_pairs:
            skipped_reviewed["relationships"] += 1
            continue
        if key in seen_rels:
            skipped_duplicates["relationships"] += 1
            continue
        seen_rels.add(key)
        rels.append((
            scenario_id, source, target, rel_type, source_key, target_key, join_key,
            r.get("description", ""), _reviewed_value(r.get("is_reviewed", False)),
        ))

    existing_concepts = {
        row["id"] for row in conn.execute(
            "SELECT id FROM concepts WHERE scenario_id=?",
            (scenario_id,),
        ).fetchall()
    }
    concepts = []
    seen_concept_ids = set()
    for c in raw_concepts:
        concept_id = c.get("id")
        if not concept_id:
            continue
        if concept_id in seen_concept_ids:
            skipped_duplicates["concepts"] += 1
            continue
        seen_concept_ids.add(concept_id)
        if c["id"] in reviewed_concepts:
            skipped_reviewed["concepts"] += 1
            continue
        concepts.append((
            c["id"], scenario_id, c.get("name", c["id"]), c.get("description", ""),
            c.get("parent_id", ""), c.get("level", 0), c.get("concept_type", ""),
            c.get("related_class", ""), _reviewed_value(c.get("is_reviewed", False)),
        ))

    existing_metrics = {
        row["id"] for row in conn.execute(
            "SELECT id FROM metrics WHERE scenario_id=?",
            (scenario_id,),
        ).fetchall()
    }
    metrics = []
    seen_metric_ids = set()
    for c in raw_metrics:
        metric_id = c.get("id")
        if not metric_id:
            continue
        if metric_id in seen_metric_ids:
            skipped_duplicates["metrics"] += 1
            continue
        seen_metric_ids.add(metric_id)
        if c["id"] in reviewed_metrics:
            skipped_reviewed["metrics"] += 1
            continue
        metrics.append((
            c["id"], scenario_id, c.get("name", c.get("name_cn", c["id"])), c.get("description", ""),
            c.get("category", ""), c.get("target_class", ""), c.get("calculation", ""),
            c.get("formula", ""), _json_text(c.get("dimensions")), _json_text(c.get("required_dimensions")),
            c.get("filters_hint", ""), c.get("chart_type", "bar"), c.get("sort_order", 0),
            _reviewed_value(c.get("is_reviewed", False)),
        ))

    for item in classes:
        class_id = item[0]
        if class_id in existing_classes:
            conn.execute(
                """UPDATE schema_classes
                   SET name_cn=?, description=?, properties=?, primary_key=?, csv_file=?, fields=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=? AND is_reviewed IS NOT TRUE""",
                (item[2], item[3], item[4], item[5], item[6], item[7], item[8], class_id, scenario_id),
            )
        else:
            conn.execute(
                "INSERT INTO schema_classes (id, scenario_id, name_cn, description, properties, primary_key, csv_file, fields, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                item,
            )

    for item in rels:
        source_target = (item[1], item[2])
        if source_target in existing_rel_pairs:
            conn.execute(
                """UPDATE schema_relationships
                   SET type=?, source_key=?, target_key=?, join_key=?, description=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
                   WHERE scenario_id=? AND source=? AND target=? AND is_reviewed IS NOT TRUE""",
                (item[3], item[4], item[5], item[6], item[7], item[8], scenario_id, item[1], item[2]),
            )
        else:
            conn.execute(
                "INSERT INTO schema_relationships (scenario_id, source, target, type, source_key, target_key, join_key, description, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                item,
            )

    for item in metrics:
        metric_id = item[0]
        if metric_id in existing_metrics:
            conn.execute(
                """UPDATE metrics
                   SET name=?, description=?, category=?, target_class=?, calculation=?, formula=?, dimensions=?, required_dimensions=?, filters_hint=?, chart_type=?, sort_order=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=? AND is_reviewed IS NOT TRUE""",
                (item[2], item[3], item[4], item[5], item[6], item[7], item[8], item[9], item[10], item[11], item[12], item[13], metric_id, scenario_id),
            )
        else:
            conn.execute(
                "INSERT INTO metrics (id, scenario_id, name, description, category, target_class, calculation, formula, dimensions, required_dimensions, filters_hint, chart_type, sort_order, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                item,
            )

    for item in concepts:
        concept_id = item[0]
        if concept_id in existing_concepts:
            conn.execute(
                """UPDATE concepts
                   SET name=?, description=?, parent_id=?, level=?, concept_type=?, related_class=?, is_reviewed=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=? AND is_reviewed IS NOT TRUE""",
                (item[2], item[3], item[4], item[5], item[6], item[7], item[8], concept_id, scenario_id),
            )
        else:
            conn.execute(
                "INSERT INTO concepts (id, scenario_id, name, description, parent_id, level, concept_type, related_class, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                item,
            )

    conn.commit()
    final_total = {
        "classes": conn.execute("SELECT COUNT(*) AS count FROM schema_classes WHERE scenario_id=?", (scenario_id,)).fetchone()["count"],
        "relationships": conn.execute("SELECT COUNT(*) AS count FROM schema_relationships WHERE scenario_id=?", (scenario_id,)).fetchone()["count"],
        "concepts": conn.execute("SELECT COUNT(*) AS count FROM concepts WHERE scenario_id=?", (scenario_id,)).fetchone()["count"],
        "metrics": conn.execute("SELECT COUNT(*) AS count FROM metrics WHERE scenario_id=?", (scenario_id,)).fetchone()["count"],
    }
    conn.close()
    from modules.schema import _sync_schema_files
    _sync_schema_files(scenario_id)
    stats = {
        "extracted": {
            "classes": len(raw_classes),
            "relationships": len(raw_relationships),
            "concepts": len(raw_concepts),
            "metrics": len(raw_metrics),
        },
        "inserted": {
            "classes": len(classes),
            "relationships": len(rels),
            "concepts": len(concepts),
            "metrics": len(metrics),
        },
        "preserved_reviewed": {
            "classes": len(reviewed_classes),
            "relationships": len(reviewed_rels),
            "concepts": len(reviewed_concepts),
            "metrics": len(reviewed_metrics),
        },
        "skipped_reviewed": skipped_reviewed,
        "skipped_duplicates": skipped_duplicates,
        "removed_unreviewed": removed_unreviewed,
        "final_total": final_total,
    }
    print(f"[SyncDB] {scenario_id}: {_format_sync_summary(stats)}")
    return stats


def _run_extraction(scenario_id:str, business_name: str, batch_size: int,
                         db_connection_url: str = "", log_id: str = "",
                         selected_files: list[str] = None, selected_tables: list[str] = None):
    """后台执行分批提取，支持 CSV 和数据库直连两种数据源"""
    global extract_status
    started_at = time.time()
    
    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")

    def on_progress(phase, current, total, msg):
        extract_status["phase"] = phase
        extract_status["progress"] = current
        extract_status["total"] = total
        extract_status["message"] = msg

    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        extractor = OntologyExtractor()

        result = extractor.run(
            input_dir=data_dir,
            output_dir=ontology_dir,
            business_name=business_name,
            # batch_size=batch_size,
            on_progress=on_progress,
            db_connection_url=db_connection_url,
            selected_files=selected_files or [],
            selected_tables=selected_tables or [],
            reviewed_schema_context=load_schema_reference_context(scenario_id),
        )

        extract_status["running"] = False
        extract_status["phase"] = "done"
        extract_status["result"] = result

        sync_stats = _sync_schema_db(scenario_id=scenario_id)
        extract_status["message"] = _format_sync_summary(sync_stats)
        reset_engine(scenario_id)
        if log_id:
            finish_extraction_log(
                log_id,
                "success",
                extract_status["message"],
                round(time.time() - started_at, 2),
            )

    except Exception as e:
        import traceback
        traceback.print_exc()
        extract_status["running"] = False
        extract_status["phase"] = "error"
        extract_status["message"] = str(e)
        if log_id:
            finish_extraction_log(
                log_id,
                "failed",
                str(e),
                round(time.time() - started_at, 2),
            )


@router.get("/api/extract/status")
async def get_extract_status():
    """获取提取进度"""
    global extract_status
    return extract_status


if __name__ == "__main__":
    _sync_schema_db("blank")