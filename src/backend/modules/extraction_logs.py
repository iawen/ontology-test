"""
提取日志（Extraction Logs）管理 API
=============================
记录和查询 AI 提取任务的执行日志。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from core.db.db import get_db

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


# ============================================================
# 内部工具：供其他模块调用记录提取日志
# ============================================================

def start_extraction_log(scenario_id: str, ext_type: str, trigger: str = "manual", message: str = "") -> str:
    """开始一条提取日志，返回 log_id"""
    log_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        """INSERT INTO extraction_logs
           (id, scenario_id, type, status, started_at, trigger, message)
           VALUES (?,?,?,?,?,?,?)""",
        (log_id, scenario_id, ext_type, "running", now, trigger, message),
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
    conn.close()
