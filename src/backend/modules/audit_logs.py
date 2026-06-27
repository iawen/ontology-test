"""
操作日志（Audit Logs）管理 API
=============================
记录和查询系统操作日志。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from core.db.db import get_db
from core.models.models import AuditLogCreate

router = APIRouter()


@router.get("/api/admin/audit_logs")
async def list_audit_logs(
    action: str = Query(""),
    resource_type: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询操作日志，支持按操作类型、资源类型筛选"""
    conn = get_db()
    conditions = []
    params = []

    if action:
        conditions.append("action=?")
        params.append(action)
    if resource_type:
        conditions.append("resource_type=?")
        params.append(resource_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    rows = conn.execute(
        f"SELECT * FROM audit_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/audit_logs")
async def create_audit_log(req: AuditLogCreate):
    """创建操作日志记录"""
    log_id = str(uuid.uuid4())[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO audit_logs
               (id, user_id, username, action, resource_type, resource_id,
                scenario_id, detail, ip, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (log_id, req.user_id, req.username, req.action, req.resource_type,
             req.resource_id, req.scenario_id, req.detail, req.ip, now),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"id": log_id, "status": "ok"}


# ============================================================
# 内部工具：供其他模块调用记录审计日志
# ============================================================

def log_action(
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    scenario_id: str = "",
    detail: str = "",
    username: str = "",
    user_id: int = 0,
    ip: str = "",
):
    """记录一条操作日志"""
    log_id = str(uuid.uuid4())[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO audit_logs
               (id, user_id, username, action, resource_type, resource_id,
                scenario_id, detail, ip, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (log_id, user_id, username, action, resource_type, resource_id,
             scenario_id, detail, ip, now),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
