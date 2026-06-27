
import os
import shutil
from fastapi import APIRouter, HTTPException, Body

from configs.global_config import Cfg
from core.db.db import IntegrityError, get_db


router = APIRouter()


@router.get("/api/admin/scenarios")
async def list_scenarios():
    conn = get_db()
    rows = conn.execute("SELECT * FROM scenarios ORDER BY is_default DESC, created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/api/scenarios/list")
async def list_active_scenarios():
    conn = get_db()
    rows = conn.execute("SELECT * FROM scenarios WHERE is_active=1 ORDER BY is_default DESC, created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/scenarios")
async def create_scenario(body: dict = Body(...)):
    sid = body.get("id", "")
    name = body.get("name", "")
    desc = body.get("description", "")
    if not sid or not name:
        raise HTTPException(400, "id 和 name 必填")
    
    data_dir = f"{sid}/data"
    ontology_dir = f"{sid}/ontology"
    os.makedirs(os.path.join(Cfg.scenarios_root, data_dir), exist_ok=True)
    os.makedirs(os.path.join(Cfg.scenarios_root, ontology_dir), exist_ok=True)
    conn = get_db()
    try:
        conn.execute("INSERT INTO scenarios (id, name, description, data_dir, ontology_dir) VALUES (?,?,?,?,?)",
                     (sid, name, desc, data_dir, ontology_dir))
        conn.commit()
    except IntegrityError:
        conn.close()
        raise HTTPException(400, f"场景 {sid} 已存在")
    conn.close()
    return {"status": "ok", "id": sid}


@router.delete("/api/admin/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str):
    conn = get_db()
    conn.execute("DELETE FROM scenarios WHERE id=?", (scenario_id,))
    conn.execute("DELETE FROM schema_classes WHERE scenario_id=?", (scenario_id,))
    conn.execute("DELETE FROM schema_relationships WHERE scenario_id=?", (scenario_id,))
    conn.commit()
    conn.close()

    # 删除文件
    scenario_dir = os.path.join(Cfg.scenarios_root, scenario_id)
    if os.path.exists(scenario_dir):
        shutil.rmtree(scenario_dir)
    return {"status": "ok"}


@router.post("/api/admin/scenarios/{scenario_id}/default")
async def set_default_scenario(scenario_id: str):
    conn = get_db()
    conn.execute("UPDATE scenarios SET is_default=0")
    conn.execute("UPDATE scenarios SET is_default=1 WHERE id=?", (scenario_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.post("/api/admin/scenarios/{scenario_id}/toggle")
async def toggle_scenario(scenario_id: str, body: dict = Body(...)):
    """切换场景的激活/禁用状态"""
    is_active = body.get("is_active", 1)
    is_active = 0 if is_active == 1 else 1
    conn = get_db()
    conn.execute("UPDATE scenarios SET is_active=? WHERE id=?", (is_active, scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@router.put("/api/admin/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, body: dict = Body(...)):
    conn = get_db()
    conn.execute("UPDATE scenarios SET name=?, description=? WHERE id=?",
                 (body.get("name",""), body.get("description",""), scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}