"""
提取日志（Extraction Logs）管理 API
=============================
记录和查询 AI 提取任务的执行日志。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from tools.db import get_db
from core.models.models import ExtractionLogCreate

router = APIRouter()


@router.get("/api/admin/extraction_logs")
async def list_extraction_logs(
    scenario_id: str = Query(""),
    type: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询提取日志，支持按场景、类型、状态筛选"""
    conn = get_db()
    conditions = []
    params = []

    if scenario_id:
        conditions.append("scenario_id=?")
        params.append(scenario_id)
    if type:
        conditions.append("type=?")
        params.append(type)
    if status:
        conditions.append("status=?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    rows = conn.execute(
        f"SELECT * FROM extraction_logs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/extraction_logs")
async def create_extraction_log(req: ExtractionLogCreate):
    """创建提取日志记录（开始提取时调用）"""
    log_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO extraction_logs
               (id, scenario_id, type, status, started_at, trigger)
               VALUES (?,?,?,?,?,?)""",
            (log_id, req.scenario_id, req.type, "running", now, req.trigger),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"id": log_id, "status": "ok"}


@router.put("/api/admin/extraction_logs/{log_id}")
async def update_extraction_log(log_id: str, body: dict):
    """更新提取日志（提取完成/失败时调用）"""
    conn = get_db()
    sets, vals = [], []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    status = body.get("status")
    if status:
        sets.append("status=?")
        vals.append(status)
        if status in ("success", "failed"):
            sets.append("finished_at=?")
            vals.append(now)

    message = body.get("message")
    if message is not None:
        sets.append("message=?")
        vals.append(message)

    duration = body.get("duration")
    if duration is not None:
        sets.append("duration=?")
        vals.append(duration)

    if not sets:
        conn.close()
        return {"status": "ok"}

    vals.append(log_id)
    conn.execute(f"UPDATE extraction_logs SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ============================================================
# 内部工具：供其他模块调用记录提取日志
# ============================================================

def start_extraction_log(scenario_id: str, ext_type: str, trigger: str = "manual") -> str:
    """开始一条提取日志，返回 log_id"""
    log_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        """INSERT INTO extraction_logs
           (id, scenario_id, type, status, started_at, trigger)
           VALUES (?,?,?,?,?,?)""",
        (log_id, scenario_id, ext_type, "running", now, trigger),
    )
    conn.commit()
    conn.close()
    return log_id


def finish_extraction_log(log_id: str, status: str, message: str = "", duration: float = 0):
    """结束一条提取日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        "UPDATE extraction_logs SET status=?, finished_at=?, message=?, duration=? WHERE id=?",
        (status, now, message, duration, log_id),
    )
    conn.commit()
    conn.close()
