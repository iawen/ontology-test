"""
图表规则（Chart Rules）管理 API
=============================
按场景 CRUD，管理数据模式与图表类型的映射规则。
"""

from fastapi import APIRouter, HTTPException

from tools.db import get_db
from modules.models import ChartRuleCreate, ChartRuleUpdate

router = APIRouter()


@router.get("/api/admin/scenarios/{scenario_id}/chart_rules")
async def list_chart_rules(scenario_id: str):
    """列出场景下所有图表规则"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chart_rules WHERE scenario_id=? ORDER BY priority DESC",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/chart_rules")
async def create_chart_rule(scenario_id: str, req: ChartRuleCreate):
    """新增图表规则"""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO chart_rules
               (scenario_id, data_pattern, chart_type, description, priority)
               VALUES (?,?,?,?,?)""",
            (scenario_id, req.data_pattern, req.chart_type, req.description, req.priority),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/chart_rules/{rule_id}")
async def update_chart_rule(scenario_id: str, rule_id: int, req: ChartRuleUpdate):
    """更新图表规则"""
    conn = get_db()
    sets, vals = [], []
    for k, v in [("data_pattern", req.data_pattern), ("chart_type", req.chart_type),
                  ("description", req.description), ("priority", req.priority)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.extend([rule_id, scenario_id])
    conn.execute(
        f"UPDATE chart_rules SET {','.join(sets)} WHERE id=? AND scenario_id=?",
        vals
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/chart_rules/{rule_id}")
async def delete_chart_rule(scenario_id: str, rule_id: int):
    """删除图表规则"""
    conn = get_db()
    conn.execute(
        "DELETE FROM chart_rules WHERE id=? AND scenario_id=?",
        (rule_id, scenario_id)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ============================================================
# 图表规则查询工具（供 Chat 模块调用）
# ============================================================

def match_chart_rule(scenario_id: str, data_pattern: str) -> dict | None:
    """根据数据模式匹配最合适的图表规则"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chart_rules WHERE scenario_id=? ORDER BY priority DESC",
        (scenario_id,)
    ).fetchall()
    conn.close()

    for r in rows:
        # 简单匹配：data_pattern 中包含的模式关键词
        patterns = [p.strip() for p in r["data_pattern"].split(",")]
        if any(p.lower() in data_pattern.lower() for p in patterns if p):
            return dict(r)
    return None
