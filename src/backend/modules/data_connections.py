"""
数据连接（Data Connections）管理 API
=====================================
管理场景的外部数据库连接，支持 PostgreSQL / MySQL。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from tools.db import get_db
from tools.db_connector import (
    test_connection,
    list_tables,
    get_table_schema,
    read_table_sample,
    mask_connection_url,
)
from modules.models import DataConnectionCreate, DataConnectionUpdate

router = APIRouter()


@router.get("/api/admin/scenarios/{scenario_id}/data_connections")
async def list_data_connections(scenario_id: str):
    """列出场景下所有数据库连接"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM data_connections WHERE scenario_id=? ORDER BY created_at DESC",
        (scenario_id,)
    ).fetchall()
    conn.close()
    # 隐藏密码
    result = []
    for r in rows:
        d = dict(r)
        d["connection_url_masked"] = mask_connection_url(d["connection_url"])
        # 不返回完整 URL，只返回 masked 版本
        d.pop("connection_url", None)
        result.append(d)
    return result


@router.post("/api/admin/scenarios/{scenario_id}/data_connections")
async def create_data_connection(scenario_id: str, req: DataConnectionCreate):
    """新增数据库连接"""
    # 先测试连接
    test_result = test_connection(req.connection_url)
    if not test_result["ok"]:
        raise HTTPException(400, f"数据库连接测试失败: {test_result['error']}")

    conn_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO data_connections
               (id, scenario_id, name, db_type, connection_url, is_active, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (conn_id, scenario_id, req.name, req.db_type, req.connection_url, 1, now),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"id": conn_id, "status": "ok", "test_result": test_result}


@router.put("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}")
async def update_data_connection(scenario_id: str, conn_id: str, req: DataConnectionUpdate):
    """更新数据库连接"""
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("db_type", req.db_type),
                  ("connection_url", req.connection_url), ("is_active", req.is_active)]:
        if v is not None and v != "":
            if k == "is_active":
                sets.append(f"{k}=?")
                vals.append(1 if v else 0)
            else:
                sets.append(f"{k}=?")
                vals.append(v)
    if not sets:
        conn.close()
        return {"status": "ok"}

    # 如果更新了 connection_url，先测试
    if "connection_url=?" in ",".join(sets):
        test_result = test_connection(req.connection_url)
        if not test_result["ok"]:
            conn.close()
            raise HTTPException(400, f"数据库连接测试失败: {test_result['error']}")

    vals.extend([conn_id, scenario_id])
    conn.execute(
        f"UPDATE data_connections SET {','.join(sets)} WHERE id=? AND scenario_id=?",
        vals
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}")
async def delete_data_connection(scenario_id: str, conn_id: str):
    """删除数据库连接"""
    conn = get_db()
    conn.execute(
        "DELETE FROM data_connections WHERE id=? AND scenario_id=?",
        (conn_id, scenario_id)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.post("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}/test")
async def test_data_connection(scenario_id: str, conn_id: str):
    """测试数据库连接是否可用"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM data_connections WHERE id=? AND scenario_id=?",
        (conn_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "连接不存在")

    result = test_connection(row["connection_url"])
    return result


@router.post("/api/admin/data_connections/test")
async def test_new_connection(body: dict):
    """测试新数据库连接（保存前先测试）"""
    url = body.get("connection_url", "")
    if not url:
        raise HTTPException(400, "connection_url 必填")
    result = test_connection(url)
    return result


# ============================================================
# 数据库表浏览
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}/tables")
async def list_db_tables(scenario_id: str, conn_id: str):
    """列出数据库连接中的所有表"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM data_connections WHERE id=? AND scenario_id=?",
        (conn_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "连接不存在")

    try:
        tables = list_tables(row["connection_url"])
        return tables
    except Exception as e:
        raise HTTPException(500, f"获取表列表失败: {str(e)}")


@router.get("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}/tables/{table_name}")
async def get_db_table_detail(scenario_id: str, conn_id: str, table_name: str):
    """获取数据库表的详细结构"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM data_connections WHERE id=? AND scenario_id=?",
        (conn_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "连接不存在")

    try:
        schema_info = get_table_schema(row["connection_url"], table_name)
        return schema_info
    except Exception as e:
        raise HTTPException(500, f"获取表结构失败: {str(e)}")


@router.get("/api/admin/scenarios/{scenario_id}/data_connections/{conn_id}/tables/{table_name}/preview")
async def preview_db_table(scenario_id: str, conn_id: str, table_name: str):
    """预览数据库表的数据（前 100 行）"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM data_connections WHERE id=? AND scenario_id=?",
        (conn_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "连接不存在")

    try:
        sample = read_table_sample(row["connection_url"], table_name, sample_rows=100)
        return sample
    except Exception as e:
        raise HTTPException(500, f"预览数据失败: {str(e)}")


# ============================================================
# 内部工具：供其他模块获取场景的活跃数据库连接
# ============================================================

def get_active_connection(scenario_id: str) -> dict | None:
    """获取场景的活跃数据库连接，返回 {"id": ..., "connection_url": ..., "db_type": ...} 或 None"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM data_connections WHERE scenario_id=? AND is_active=1 LIMIT 1",
        (scenario_id,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_all_connections(scenario_id: str) -> list[dict]:
    """获取场景的所有数据库连接"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM data_connections WHERE scenario_id=? ORDER BY created_at",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
