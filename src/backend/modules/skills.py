"""
技能包（Skills）管理 API + LLM 意图路由
==========================================
v2: Skill.md 模式
  - 每个 skill 存储为结构化数据 + markdown 内容
  - 匹配方式：LLM 意图路由（而非关键词匹配）
  - 管理后台编辑 skill.md，禁用/激活
"""

import json
from fastapi import APIRouter, HTTPException, Request

from core.db.db import get_db
from core.models.models import SkillCreate, SkillUpdate

router = APIRouter()


# ============================================================
# CRUD
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/skills")
async def list_skills(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM skills WHERE scenario_id=? ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/skills")
async def create_skill(scenario_id: str, req: SkillCreate):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO skills
               (id, scenario_id, name, description, trigger_condition, content, is_active, sort_order)
               VALUES (?,?,?,?,?,?,?,?)""",
            (req.id, scenario_id, req.name, req.description,
             req.trigger_condition, req.content,
             1 if req.is_active else 0, req.sort_order)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/skills/{skill_id}")
async def update_skill(scenario_id: str, skill_id: str, req: SkillUpdate):
    conn = get_db()
    sets, vals = [], []
    if req.name:
        sets.append("name=?")
        vals.append(req.name)
    if req.description is not None:
        sets.append("description=?")
        vals.append(req.description)
    if req.trigger_condition is not None:
        sets.append("trigger_condition=?")
        vals.append(req.trigger_condition)
    if req.content is not None:
        sets.append("content=?")
        vals.append(req.content)
    if req.is_active is not None:
        sets.append("is_active=?")
        vals.append(1 if req.is_active else 0)
    if req.sort_order is not None:
        sets.append("sort_order=?")
        vals.append(req.sort_order)
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.extend([scenario_id, skill_id])
    conn.execute(f"UPDATE skills SET {','.join(sets)} WHERE scenario_id=? AND id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/skills/{skill_id}/switch")
async def switch_skill(scenario_id: str, skill_id: str, request: Request):
    body = await request.json()
    is_active = body.get("is_active")
    conn = get_db()
    conn.execute(
        "UPDATE skills SET is_active=? WHERE scenario_id=? AND id=?",
        (1 if is_active else 0, scenario_id, skill_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/skills/{skill_id}")
async def delete_skill(scenario_id: str, skill_id: str):
    conn = get_db()
    conn.execute("DELETE FROM skills WHERE scenario_id=? AND id=?", (scenario_id, skill_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}



