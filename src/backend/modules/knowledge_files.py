import os
import sys
import json
import shutil
import asyncio
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks

from configs.global_config import Cfg
from prompts.prompt import reset_engine
from core.db.db import get_db
from core.ontology.extract_ontology import OntologyExtractor


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

    # 自动检测：如果场景有活跃的数据库连接且没有指定数据源，优先使用数据库
    if data_source == "auto" and not db_connection_url:
        try:
            from modules.data_connections import get_active_connection
            active_conn = get_active_connection(scenario_id)
            if active_conn:
                db_connection_url = active_conn["connection_url"]
                data_source = "database"
        except Exception:
            pass

    # 如果明确指定 csv 或没有数据库连接，使用 CSV 模式
    if data_source == "csv":
        db_connection_url = ""

    extract_status = {"running": True, "phase": "starting", "progress": 0, "total": 0,
                      "message": f"启动中... (数据源: {'数据库' if db_connection_url else 'CSV文件'})", "result": None}

    # 在后台运行
    # asyncio.create_task(_run_extraction(scenario_id, business_name, batch_size,
    #                                      db_connection_url=db_connection_url))
    background_tasks.add_task(_run_extraction, scenario_id, business_name, batch_size, db_connection_url)
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


def _sync_schema_db(scenario_id: str):
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")
    with open(os.path.join(ontology_dir, "schema.json"), "r", encoding="utf-8") as f:
        schema = json.loads(f.read())

    classes = [
        (c["id"], scenario_id, c["name_cn"], c["description"], c.get("primary_key", ""), c.get("csv_file", ""), json.dumps(c.get("fields", []), ensure_ascii=False), int(_reviewed_value(c.get("is_reviewed", False)))) 
         for c in schema.get("classes", [])
         ]
    rels = [(scenario_id, r["source"], r["target"], r.get("type", ""), r.get("source_key", ""), r.get("target_key", ""), r.get("description", ""), int(_reviewed_value(r.get("is_reviewed", False)))) for r in schema.get("relationships", [])] 
    concepts = [(c["id"], scenario_id, c["name"], c.get("level", 0), c.get("parent_id", ""),
                 c.get("concept_type", ""), c.get("related_class", ""), int(_reviewed_value(c.get("is_reviewed", False))))
                for c in schema.get("concepts", [])]
    metrics = [(c["id"], scenario_id, c["name"], c.get("description", ""), c.get("category", ""),
                 c.get("target_class", ""), c.get("calculation", ""), c.get("formula", ""), 
                 c.get("required_dimensions"), 
                 c.get("filters_hint"),
                 c.get("dimensions"),
                 int(_reviewed_value(c.get("is_reviewed", False))))
                for c in schema.get("metrics", [])]

    conn = get_db()
    # 清除旧数据再插入（避免重复）
    conn.execute("DELETE FROM schema_classes WHERE scenario_id=?", (scenario_id,))
    conn.execute("DELETE FROM schema_relationships WHERE scenario_id=?", (scenario_id,))
    conn.execute("DELETE FROM concepts WHERE scenario_id=?", (scenario_id,))
    conn.execute("DELETE FROM metrics WHERE scenario_id=?", (scenario_id,))

    conn.executemany("INSERT OR REPLACE INTO schema_classes (id, scenario_id, name_cn, description, primary_key, csv_file, fields, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", classes)

    conn.executemany("INSERT INTO schema_relationships (scenario_id, source, target, type, source_key, target_key, description, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", rels)

    if metrics and len(metrics) > 0:
        conn.executemany(
            "INSERT OR REPLACE INTO metrics (id, scenario_id, name, description, category, target_class, calculation, formula, required_dimensions, filters_hint, dimensions, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            metrics,
        )
    if concepts and len(concepts) > 0:
        conn.executemany(
            "INSERT OR REPLACE INTO concepts (id, scenario_id, name, level, parent_id, concept_type, related_class, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            concepts,
        )

    conn.commit()
    conn.close()
    print(f"[SyncDB] {scenario_id}: {len(classes)} classes, {len(rels)} relationships, {len(concepts)} concepts")    


def _run_extraction(scenario_id:str, business_name: str, batch_size: int,
                         db_connection_url: str = ""):
    """后台执行分批提取，支持 CSV 和数据库直连两种数据源"""
    global extract_status
    
    data_dir = os.path.join(Cfg.scenarios_root, scenario_id, "data")
    ontology_dir = os.path.join(Cfg.scenarios_root, scenario_id, "ontology")

    def on_progress(phase, current, total, msg):
        extract_status["phase"] = phase
        extract_status["progress"] = current
        extract_status["total"] = total
        extract_status["message"] = msg

    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        extractor = OntologyExtractor(
            api_key = Cfg.openai_api_key,
            base_url = Cfg.openai_base_url,
            model = Cfg.model_name,
        )

        result = extractor.run(
            input_dir=data_dir,
            output_dir=ontology_dir,
            business_name=business_name,
            # batch_size=batch_size,
            on_progress=on_progress,
            db_connection_url=db_connection_url,
        )

        extract_status["running"] = False
        extract_status["phase"] = "done"
        extract_status["result"] = result

        _sync_schema_db(scenario_id=scenario_id)

    except Exception as e:
        import traceback
        traceback.print_exc()
        extract_status["running"] = False
        extract_status["phase"] = "error"
        extract_status["message"] = str(e)


@router.get("/api/extract/status")
async def get_extract_status():
    """获取提取进度"""
    global extract_status
    return extract_status


if __name__ == "__main__":
    _sync_schema_db("blank")