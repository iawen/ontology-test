import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from core.db.db import get_db

from core.ontology.schema_optimizer import SchemaOptimizer


router = APIRouter()
optimization_status: dict[str, dict] = {}
UPLOAD_ROOT = Path(__file__).resolve().parents[1] / "data" / "schema_optimization"


def _json_obj(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


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


def _row_to_dict(row) -> dict:
    return {key: row[key] for key in row.keys()} if row else {}


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "default").strip("._")
    return cleaned or "default"


def _scenario_upload_dir(scenario_id: str) -> Path:
    path = UPLOAD_ROOT / _safe_segment(scenario_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decode_content(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _safe_stored_name(file_id: str, original_filename: str) -> str:
    path = Path(original_filename)
    stem = _safe_segment(path.stem)
    suffix = path.suffix.lower()
    return f"{file_id}_{stem}{suffix}"


def _to_http_error(exc: Exception):
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


@router.get("/api/admin/scenarios/{scenario_id}/schema-optimization/files")
async def api_list_optimization_files(scenario_id: str):
    return list_optimization_files(scenario_id)


@router.post("/api/admin/scenarios/{scenario_id}/schema-optimization/files")
async def api_upload_optimization_files(scenario_id: str, files: list[UploadFile] = File(...)):
    try:
        return await save_optimization_files(scenario_id, files)
    except Exception as exc:
        raise _to_http_error(exc) from exc


@router.delete("/api/admin/scenarios/{scenario_id}/schema-optimization/files/{file_id}")
async def api_delete_optimization_file(scenario_id: str, file_id: str):
    try:
        return delete_optimization_file(scenario_id, file_id)
    except Exception as exc:
        raise _to_http_error(exc) from exc


@router.get("/api/admin/scenarios/{scenario_id}/schema-optimization/runs")
async def api_list_optimization_runs(scenario_id: str):
    return list_optimization_runs(scenario_id)


@router.post("/api/admin/scenarios/{scenario_id}/schema-optimization/optimize")
async def api_run_schema_optimization(scenario_id: str, background_tasks: BackgroundTasks, body: dict | None = None):
    body = body or {}
    file_ids = body.get("file_ids") or []
    incremental = body.get("incremental", True)
    target_class_ids = body.get("target_class_ids") or None
    enable_quality_assessment = body.get("enable_quality_assessment", True)
    try:
        run_id = create_optimization_run(scenario_id, file_ids)
    except Exception as exc:
        raise _to_http_error(exc) from exc

    optimization_status[run_id] = {
        "run_id": run_id,
        "running": True,
        "phase": "queued",
        "progress": 0,
        "total": 100,
        "message": "Schema 优化任务已进入后台队列",
        "result": None,
    }
    background_tasks.add_task(
        _run_optimization_task,
        scenario_id,
        file_ids,
        run_id,
        bool(incremental),
        target_class_ids,
        bool(enable_quality_assessment),
    )
    return {"status": "started", "run_id": run_id}


async def _run_optimization_task(
    scenario_id: str,
    file_ids: list[str],
    run_id: str,
    incremental: bool = True,
    target_class_ids: list[str] | None = None,
    enable_quality_assessment: bool = True,
):
    async def update(status: dict):
        current = optimization_status.get(run_id, {})
        optimization_status[run_id] = {**current, **status, "run_id": run_id}

    try:
        document_paths = _get_document_paths(scenario_id, file_ids)
        optimizer = SchemaOptimizer(scenario_id)
        optimizer._create_run_record = lambda _core_run_id: None
        optimizer._update_run_success = lambda _core_run_id, diff, applied, quality: _mark_run_success(
            scenario_id,
            run_id,
            diff.summary,
            {"diff": diff.model_dump(), "applied": applied, "quality": quality},
        )
        optimizer._update_run_failure = lambda _core_run_id, error: _mark_run_failed(scenario_id, run_id, error)

        result = await optimizer.optimize(
            document_paths=document_paths,
            incremental=incremental,
            target_class_ids=target_class_ids,
            progress_callback=update,
            enable_quality_assessment=enable_quality_assessment,
        )
        if result.get("status") == "skipped":
            _mark_run_success(scenario_id, run_id, result.get("message") or "无可优化资产", result)
    except Exception as exc:
        _mark_run_failed(scenario_id, run_id, str(exc))
        await update({
            "running": False,
            "phase": "error",
            "progress": 100,
            "total": 100,
            "message": str(exc),
            "result": None,
        })


def _mark_run_failed(scenario_id: str, run_id: str, error: str):
    conn = get_db()
    conn.execute(
        """UPDATE schema_optimization_runs
           SET status='failed', error=?, finished_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=? AND status='running'""",
        (error, run_id, scenario_id),
    )
    conn.commit()
    conn.close()


def _mark_run_success(scenario_id: str, run_id: str, summary: str, changes: dict):
    conn = get_db()
    conn.execute(
        """UPDATE schema_optimization_runs
           SET status='success', summary=?, changes_json=?, error='', finished_at=CURRENT_TIMESTAMP
           WHERE id=? AND scenario_id=?""",
        (summary, json.dumps(changes, ensure_ascii=False), run_id, scenario_id),
    )
    conn.commit()
    conn.close()


def create_optimization_run(scenario_id: str, file_ids: list[str]) -> str:
    run_id = str(uuid.uuid4())[:8]
    conn = get_db()
    conn.execute(
        """INSERT INTO schema_optimization_runs (id, scenario_id, file_ids, status, created_at)
           VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP)""",
        (run_id, scenario_id, json.dumps(file_ids, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return run_id


def list_optimization_files(scenario_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT id, scenario_id, filename, original_filename, file_ext, file_path,
                  content_hash, size, uploaded_at
           FROM schema_optimization_files
           WHERE scenario_id=?
           ORDER BY uploaded_at DESC""",
        (scenario_id,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(row) for row in rows]


async def save_optimization_files(scenario_id: str, files: list[UploadFile]) -> dict:
    if not files:
        raise ValueError("未选择上传文件")

    saved: list[dict] = []
    upload_dir = _scenario_upload_dir(scenario_id)
    conn = get_db()
    try:
        for upload in files:
            original_filename = Path(upload.filename or "").name
            if not original_filename:
                raise ValueError("上传文件名不能为空")
            data = await upload.read()
            if not data:
                raise ValueError(f"文件为空: {original_filename}")

            file_id = str(uuid.uuid4())[:8]
            file_ext = Path(original_filename).suffix.lower().lstrip(".")
            stored_name = _safe_stored_name(file_id, original_filename)
            file_path = upload_dir / stored_name
            file_path.write_bytes(data)
            content_hash = hashlib.sha256(data).hexdigest()
            content_text = _decode_content(data)

            conn.execute(
                """INSERT INTO schema_optimization_files
                   (id, scenario_id, filename, original_filename, file_ext, file_path, content_text, content_hash, size, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    file_id,
                    scenario_id,
                    stored_name,
                    original_filename,
                    file_ext,
                    str(file_path),
                    content_text,
                    content_hash,
                    len(data),
                ),
            )
            saved.append({
                "id": file_id,
                "filename": stored_name,
                "original_filename": original_filename,
                "file_ext": file_ext,
                "size": len(data),
            })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"status": "ok", "files": saved}


def delete_optimization_file(scenario_id: str, file_id: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT file_path FROM schema_optimization_files WHERE id=? AND scenario_id=?",
        (file_id, scenario_id),
    ).fetchone()
    if not row:
        conn.close()
        raise FileNotFoundError("优化文档不存在")

    conn.execute(
        "DELETE FROM schema_optimization_files WHERE id=? AND scenario_id=?",
        (file_id, scenario_id),
    )
    conn.commit()
    conn.close()

    file_path = Path(row["file_path"])
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
    return {"status": "ok"}


def list_optimization_runs(scenario_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT id, scenario_id, file_ids, status, summary, changes_json, error, created_at, finished_at
           FROM schema_optimization_runs
           WHERE scenario_id=?
           ORDER BY created_at DESC""",
        (scenario_id,),
    ).fetchall()
    conn.close()
    runs = []
    for row in rows:
        item = _row_to_dict(row)
        item["file_ids"] = _json_list(item.get("file_ids"))
        item["changes_json"] = _json_obj(item.get("changes_json"))
        runs.append(item)
    return runs


def _get_document_paths(scenario_id: str, file_ids: list[str]) -> list[str]:
    files = list_optimization_files(scenario_id)
    if not files:
        raise ValueError("请先上传用于优化的业务文档")

    if not file_ids:
        return [item["file_path"] for item in files]

    by_id = {item["id"]: item for item in files}
    missing = [file_id for file_id in file_ids if file_id not in by_id]
    if missing:
        raise FileNotFoundError(f"优化文档不存在: {', '.join(missing)}")
    return [by_id[file_id]["file_path"] for file_id in file_ids]


@router.get("/api/admin/scenarios/{scenario_id}/schema-optimization/stream/{run_id}")
async def api_stream_schema_optimization(scenario_id: str, run_id: str):
    async def generate():
        last_status = None
        while True:
            current_status = optimization_status.get(run_id)
            if current_status is None:
                current_status = _load_run_status(scenario_id, run_id)

            if current_status != last_status:
                yield f"data: {json.dumps(current_status, ensure_ascii=False)}\n\n"
                last_status = dict(current_status)

            if not current_status.get("running") and current_status.get("phase") in {"done", "error"}:
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _load_run_status(scenario_id: str, run_id: str) -> dict:
    runs = [run for run in list_optimization_runs(scenario_id) if run.get("id") == run_id]
    if not runs:
        return {
            "run_id": run_id,
            "running": False,
            "phase": "error",
            "progress": 100,
            "total": 100,
            "message": "优化任务不存在或已过期",
            "result": None,
        }
    run = runs[0]
    if run.get("status") == "success":
        return {
            "run_id": run_id,
            "running": False,
            "phase": "done",
            "progress": 100,
            "total": 100,
            "message": run.get("summary") or "Schema 优化完成",
            "result": run.get("changes_json", {}),
        }
    if run.get("status") == "failed":
        return {
            "run_id": run_id,
            "running": False,
            "phase": "error",
            "progress": 100,
            "total": 100,
            "message": run.get("error") or "Schema 优化失败",
            "result": None,
        }
    return {
        "run_id": run_id,
        "running": True,
        "phase": "running",
        "progress": 10,
        "total": 100,
        "message": "Schema 优化正在后台运行",
        "result": None,
    }