"""
用户管理 API
=============================
CRUD + 修改密码
"""

import hashlib

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from core.models.models import UserCreate, UserUpdate

router = APIRouter()


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


@router.get("/api/admin/users")
async def list_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/users")
async def create_user(req: UserCreate):
    conn = get_db()
    try:
        pwd_hash = _hash_password(req.password)
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (req.username, pwd_hash, req.role)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/users/{user_id}")
async def update_user(user_id: int, req: UserUpdate):
    conn = get_db()
    sets, vals = [], []
    if req.username:
        sets.append("username=?")
        vals.append(req.username)
    if req.role:
        sets.append("role=?")
        vals.append(req.role)
    if req.password:
        sets.append("password_hash=?")
        vals.append(_hash_password(req.password))
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.append(user_id)
    conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int):
    conn = get_db()
    # 不允许删除最后一个管理员
    admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user and user["role"] == "admin" and admin_count <= 1:
        conn.close()
        raise HTTPException(400, "不能删除最后一个管理员账号")
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}
