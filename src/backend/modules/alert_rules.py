"""
告警规则（Alert Rules）管理 API
================================
基于本体论的告警规则，当数据满足条件时自动触发 Action。
实现"洞察→行动"的闭环。
"""

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from core.db.db import get_db
from core.models.models import AlertRuleCreate, AlertRuleUpdate

router = APIRouter()


@router.get("/api/admin/scenarios/{scenario_id}/alert_rules")
async def list_alert_rules(scenario_id: str):
    """列出场景下所有告警规则"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM alert_rules WHERE scenario_id=? ORDER BY created_at DESC",
        (scenario_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/scenarios/{scenario_id}/alert_rules")
async def create_alert_rule(scenario_id: str, req: AlertRuleCreate):
    """新增告警规则"""
    rule_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO alert_rules
               (id, scenario_id, name, description, target_class,
                condition_expression, action_id, severity, is_active, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rule_id, scenario_id, req.name, req.description, req.target_class,
             req.condition_expression, req.action_id, req.severity, 1, now),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"id": rule_id, "status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/alert_rules/{rule_id}")
async def update_alert_rule(scenario_id: str, rule_id: str, req: AlertRuleUpdate):
    """更新告警规则"""
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("target_class", req.target_class), ("condition_expression", req.condition_expression),
                  ("action_id", req.action_id), ("severity", req.severity)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.is_active is not None:
        sets.append("is_active=?")
        vals.append(1 if req.is_active else 0)
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.extend([rule_id, scenario_id])
    conn.execute(f"UPDATE alert_rules SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/alert_rules/{rule_id}")
async def delete_alert_rule(scenario_id: str, rule_id: str):
    """删除告警规则"""
    conn = get_db()
    conn.execute("DELETE FROM alert_rules WHERE id=? AND scenario_id=?", (rule_id, scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.post("/api/admin/scenarios/{scenario_id}/alert_rules/{rule_id}/test")
async def test_alert_rule(scenario_id: str, rule_id: str):
    """测试告警规则（手动触发一次检查）"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM alert_rules WHERE id=? AND scenario_id=?",
        (rule_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "规则不存在")

    rule = dict(row)
    result = check_alert_rule(rule)
    return result


# ============================================================
# 告警检查引擎
# ============================================================

def check_alert_rule(rule: dict) -> dict:
    """
    检查告警规则是否触发。
    根据 condition_expression 对 target_class 的数据执行检查。
    """
    scenario_id = rule["scenario_id"]
    target_class = rule["target_class"]
    condition = rule["condition_expression"]

    try:
        from prompts.prompt import init_prompt, get_query_engine
        init_prompt(scenario_id)
        query_engine = get_query_engine(scenario_id)

        # 构建检查 SQL
        sql = f'SELECT COUNT(*) AS alert_count FROM "{target_class}" WHERE {condition}'
        result = query_engine._execute_sql(sql)
        alert_count = result[0]["alert_count"] if result else 0

        triggered = alert_count > 0

        if triggered and rule["action_id"]:
            # 自动触发关联的 Action
            try:
                from modules.actions import _execute_action
                conn = get_db()
                action_row = conn.execute(
                    "SELECT * FROM actions WHERE id=?",
                    (rule["action_id"],)
                ).fetchone()
                conn.close()
                if action_row:
                    action = dict(action_row)
                    action["parameters"] = json.loads(action.get("parameters", "{}"))
                    _execute_action(action, {
                        "trigger_type": "alert",
                        "trigger_reason": f"告警规则 '{rule['name']}' 触发：{target_class} 中有 {alert_count} 条记录满足 {condition}",
                        "alert_count": alert_count,
                        "target_class": target_class,
                        "condition": condition,
                    })
            except Exception as e:
                print(f"[AlertRule] Action 执行失败: {e}")

            # 更新触发计数
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db()
            conn.execute(
                "UPDATE alert_rules SET last_triggered_at=?, trigger_count=trigger_count+1 WHERE id=?",
                (now, rule["id"])
            )
            conn.commit()
            conn.close()

        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "triggered": triggered,
            "alert_count": alert_count,
            "target_class": target_class,
            "condition": condition,
            "action_triggered": triggered and bool(rule["action_id"]),
        }
    except Exception as e:
        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "triggered": False,
            "error": str(e),
        }


def check_all_alert_rules(scenario_id: str) -> list[dict]:
    """检查场景下所有活跃的告警规则"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM alert_rules WHERE scenario_id=? AND is_active=1",
        (scenario_id,)
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        result = check_alert_rule(dict(r))
        results.append(result)
    return results
