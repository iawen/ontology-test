import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from tools.db import get_db
from ontology.schema_optimizer import (
    create_optimization_run,
    delete_optimization_file,
    list_optimization_files,
    list_optimization_runs,
    run_schema_optimization,
    save_optimization_files,
)


router = APIRouter()
optimization_status: dict[str, dict] = {}


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
        return save_optimization_files(scenario_id, files)
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
    file_ids = (body or {}).get("file_ids") or []
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
    background_tasks.add_task(_run_optimization_task, scenario_id, file_ids, run_id)
    return {"status": "started", "run_id": run_id}


async def _run_optimization_task(scenario_id: str, file_ids: list[str], run_id: str):
    async def update(status: dict):
        current = optimization_status.get(run_id, {})
        optimization_status[run_id] = {**current, **status, "run_id": run_id}

    try:
        await run_schema_optimization(scenario_id, file_ids, run_id=run_id, progress=update)
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