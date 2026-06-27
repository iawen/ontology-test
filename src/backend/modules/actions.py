"""
Action（行动）管理 API + 执行引擎
==================================
基于 Palantir Ontology 的 Action 理念：
  - Action 是本体论三大要素之一（Data + Logic + Action）
  - Action 将"洞察"转化为"行动"，实现数据分析闭环
  - 支持 5 种行动类型：通知、Webhook、邮件、数据更新、工作流
  - 支持人工确认和自动触发两种模式
"""

import json
import uuid
import time
from datetime import datetime
import re
from fastapi import APIRouter, HTTPException

from core.llm.chat_model import get_async_client, get_model_name # 动态导入配置与 OpenAI 异步客户端
from core.db.db import get_db
from core.models.models import ActionCreate, ActionUpdate, ActionExecuteRequest

router = APIRouter()


# ============================================================
# Action CRUD
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/actions")
async def list_actions(scenario_id: str):
    """列出场景下所有 Action"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM actions WHERE scenario_id=? ORDER BY sort_order, created_at",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["parameters"] = json.loads(d.get("parameters", "{}"))
        result.append(d)
    return result


@router.post("/api/admin/scenarios/{scenario_id}/actions")
async def create_action(scenario_id: str, req: ActionCreate):
    """新增 Action"""
    action_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO actions
               (id, scenario_id, name, description, action_type, trigger_condition,
                target_object, parameters, is_active, requires_confirm, sort_order, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (action_id, scenario_id, req.name, req.description, req.action_type,
             req.trigger_condition, req.target_object,
             json.dumps(req.parameters, ensure_ascii=False),
             1 if req.requires_confirm else 0, 1 if req.requires_confirm else 0,
             req.sort_order, now),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(400, f"创建失败: {e}")
    conn.close()
    return {"id": action_id, "status": "ok"}


@router.put("/api/admin/scenarios/{scenario_id}/actions/{action_id}")
async def update_action(scenario_id: str, action_id: str, req: ActionUpdate):
    """更新 Action"""
    conn = get_db()
    sets, vals = [], []
    for k, v in [("name", req.name), ("description", req.description),
                  ("action_type", req.action_type), ("trigger_condition", req.trigger_condition),
                  ("target_object", req.target_object)]:
        if v is not None and v != "":
            sets.append(f"{k}=?")
            vals.append(v)
    if req.parameters is not None:
        sets.append("parameters=?")
        vals.append(json.dumps(req.parameters, ensure_ascii=False))
    if req.is_active is not None:
        sets.append("is_active=?")
        vals.append(1 if req.is_active else 0)
    if req.requires_confirm is not None:
        sets.append("requires_confirm=?")
        vals.append(1 if req.requires_confirm else 0)
    if req.sort_order is not None:
        sets.append("sort_order=?")
        vals.append(req.sort_order)
    if not sets:
        conn.close()
        return {"status": "ok"}
    vals.extend([action_id, scenario_id])
    conn.execute(f"UPDATE actions SET {','.join(sets)} WHERE id=? AND scenario_id=?", vals)
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/admin/scenarios/{scenario_id}/actions/{action_id}")
async def delete_action(scenario_id: str, action_id: str):
    """删除 Action"""
    conn = get_db()
    conn.execute("DELETE FROM actions WHERE id=? AND scenario_id=?", (action_id, scenario_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ============================================================
# Action 执行
# ============================================================

@router.post("/api/admin/scenarios/{scenario_id}/actions/{action_id}/execute")
async def execute_action(scenario_id: str, action_id: str, req: ActionExecuteRequest):
    """执行 Action"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM actions WHERE id=? AND scenario_id=?",
        (action_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Action 不存在")

    action = dict(row)
    action["parameters"] = json.loads(action.get("parameters", "{}"))

    # 需要确认但未确认
    if action["requires_confirm"] and not req.confirmed:
        return {"status": "needs_confirm", "action": {
            "id": action["id"], "name": action["name"],
            "action_type": action["action_type"],
            "description": action["description"],
        }}

    # 执行 Action
    result = _execute_action(action, req.context)
    return result


@router.post("/api/admin/actions/execute")
async def execute_action_direct(req: ActionExecuteRequest):
    """直接执行 Action（Chat 模块调用）"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM actions WHERE id=? AND scenario_id=?",
        (req.action_id, req.scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Action 不存在")

    action = dict(row)
    action["parameters"] = json.loads(action.get("parameters", "{}"))

    # Chat 调用时默认已确认
    result = _execute_action(action, req.context)
    return result


# ============================================================
# Action 执行日志
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/action_logs")
async def list_action_logs(scenario_id: str, page: int = 1, page_size: int = 20):
    """查询 Action 执行日志"""
    conn = get_db()
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT * FROM action_logs WHERE scenario_id=? ORDER BY executed_at DESC LIMIT ? OFFSET ?",
        (scenario_id, page_size, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# Action 执行引擎
# ============================================================

def _execute_action(action: dict, context: dict = {}) -> dict:
    """
    执行 Action 的核心逻辑。
    根据 action_type 分发到不同的执行器。
    """
    log_id = str(uuid.uuid4())[:12]
    start_time = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    action_type = action["action_type"]
    params = action.get("parameters", {})
    result = {"status": "ok", "action_id": action["id"], "action_type": action_type}

    try:
        if action_type == "notification":
            # 通知类型：生成通知消息
            result["result"] = _execute_notification(action, context)
        elif action_type == "webhook":
            # Webhook 类型：调用外部 API
            result["result"] = _execute_webhook(action, context)
        elif action_type == "email":
            # 邮件类型：发送邮件通知
            result["result"] = _execute_email(action, context)
        elif action_type == "data_update":
            # 数据更新类型：更新数据库记录
            result["result"] = _execute_data_update(action, context)
        elif action_type == "workflow":
            # 工作流类型：触发工作流
            print("[Action] 触发工作流")
            result["result"] = _execute_workflow(action, context)
        else:
            result["result"] = f"未知的 action_type: {action_type}"
            result["status"] = "error"

        duration = time.time() - start_time
        _log_action_execution(
            log_id=log_id,
            scenario_id=action["scenario_id"],
            action_id=action["id"],
            action_name=action["name"],
            trigger_type=context.get("trigger_type", "manual"),
            trigger_reason=context.get("trigger_reason", ""),
            status="success",
            result=json.dumps(result["result"], ensure_ascii=False) if isinstance(result["result"], (dict, list)) else str(result["result"]),
            duration=duration,
            executed_at=now,
        )
    except Exception as e:
        duration = time.time() - start_time
        result["status"] = "error"
        result["error"] = str(e)
        _log_action_execution(
            log_id=log_id,
            scenario_id=action["scenario_id"],
            action_id=action["id"],
            action_name=action["name"],
            trigger_type=context.get("trigger_type", "manual"),
            trigger_reason=context.get("trigger_reason", ""),
            status="failed",
            result=str(e),
            duration=duration,
            executed_at=now,
        )

    return result


def _execute_notification(action: dict, context: dict) -> dict:
    """执行通知类型 Action"""
    params = action.get("parameters", {})
    message_template = params.get("message_template", "数据异常通知：{alert_message}")
    # 替换模板变量
    message = message_template
    for k, v in context.items():
        message = message.replace(f"{{{k}}}", str(v))

    return {
        "type": "notification",
        "title": action["name"],
        "message": message,
        "severity": params.get("severity", "info"),
    }


def _execute_webhook(action: dict, context: dict) -> dict:
    """执行 Webhook 类型 Action"""
    import urllib.request
    params = action.get("parameters", {})
    url = params.get("url", "")
    method = params.get("method", "POST")
    headers = params.get("headers", {})
    body = params.get("body_template", "{}")

    # 替换模板变量
    for k, v in context.items():
        body = body.replace(f"{{{k}}}", str(v))

    if not url:
        return {"type": "webhook", "message": "未配置 Webhook URL", "skipped": True}

    try:
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8") if method == "POST" else None,
            headers={"Content-Type": "application/json", **headers},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"type": "webhook", "status_code": resp.status, "response": resp.read().decode("utf-8")[:500]}
    except Exception as e:
        return {"type": "webhook", "error": str(e)}


def _execute_email(action: dict, context: dict) -> dict:
    """执行邮件类型 Action（预留接口）"""
    params = action.get("parameters", {})
    return {
        "type": "email",
        "to": params.get("to", ""),
        "subject": params.get("subject", action["name"]),
        "message": "邮件发送功能需要配置 SMTP 服务",
        "status": "pending_smtp_config",
    }


def _execute_data_update(action: dict, context: dict) -> dict:
    """执行数据更新类型 Action"""
    params = action.get("parameters", {})
    target = action.get("target_object", "")
    update_sql = params.get("update_sql", "")
    update_values = params.get("update_values", {})

    if not target and not update_sql:
        return {"type": "data_update", "message": "未配置更新目标", "skipped": True}

    # 如果有数据库连接，执行 SQL
    try:
        from modules.data_connections import get_active_connection
        active_conn = get_active_connection(action["scenario_id"])
        if active_conn and update_sql:
            from core.db.db_connector import execute_query
            for k, v in context.items():
                update_sql = update_sql.replace(f"{{{k}}}", str(v))
            result = execute_query(active_conn["connection_url"], update_sql)
            return {"type": "data_update", "affected_rows": result.get("row_count", 0), "sql": update_sql}
    except Exception as e:
        return {"type": "data_update", "error": str(e)}

    return {"type": "data_update", "message": "无可用数据库连接", "skipped": True}


def _execute_workflow(action: dict, context: dict) -> dict:
    """执行工作流类型 Action — 调用工作流引擎实际执行"""
    from modules.workflow_engine import create_and_run_workflow
    params = action.get("parameters", {})
    workflow_id = params.get("workflow_id", "")
    steps = params.get("steps", [])
    if not steps:
        return {
            "type": "workflow",
            "workflow_id": workflow_id,
            "message": f"工作流 '{workflow_id}' 已触发（无步骤定义，仅记录）",
            "status": "triggered_no_steps",
        }
    # 调用工作流引擎创建并执行
    result = create_and_run_workflow(action, context)
    return result


def _log_action_execution(log_id, scenario_id, action_id, action_name,
                           trigger_type, trigger_reason, status, result, duration, executed_at):
    """记录 Action 执行日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO action_logs
               (id, scenario_id, action_id, action_name, trigger_type, trigger_reason,
                status, result, duration, executed_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (log_id, scenario_id, action_id, action_name, trigger_type, trigger_reason,
             status, result, duration, executed_at, now),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ============================================================
# 内部工具：供 Chat 模块调用
# ============================================================

def get_available_actions(scenario_id: str) -> list[dict]:
    """获取场景下所有可用的 Action"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM actions WHERE scenario_id=? AND is_active=1 ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["parameters"] = json.loads(d.get("parameters", "{}"))
        result.append(d)
    return result


# ── 修改位置：替换原有的 find_matching_actions 函数 ──
async def find_matching_actions(scenario_id: str, user_intent: str) -> list[dict]:
    """
    【通用大模型算子】根据大模型的回答或上下文，利用LLM智能匹配可能需要的 Action，
    并动态生成个性化的“采用此Action的理由”。
    """
    

    # 1. 获取当前场景下所有激活的通用 Actions
    actions = get_available_actions(scenario_id)
    if not actions:
        return []

    # 2. 提取 Action 的元数据供大模型进行语义判定
    actions_meta = []
    for a in actions:
        actions_meta.append({
            "id": a["id"],
            "name": a["name"],
            "description": a.get("description", ""),
            "trigger_condition": a.get("trigger_condition", "")
        })

    # 3. 构建严谨的 System Prompt，要求模型进行语义对齐并输出标准 JSON 数组
    system_prompt = (
        "你是一个高智能的本体行动（Ontology Action）匹配与推理引擎。\n"
        "请分析给定的【大模型最新回答/上下文内容】，并对照给定的【可用行动列表】，判断当前上下文是否强烈建议、提及或隐含需要触发其中的某些行动。\n"
        "如果判定匹配成功，请为该行动生成一段简短、专业且具备说服力的‘采用此Action的理由’（1-2句话，直接以助手口吻对用户说，例如：‘检测到当前设备存在故障，建议立即发送设备维修工单以通知线下团队处理。’）。\n\n"
        "【严格输出规范】:\n"
        "必须以纯 JSON 数组格式返回结果，不要包含任何 markdown 标记（如 ```json）。格式示例如下：\n"
        "[\n"
        "  {\n"
        "    \"action_id\": \"匹配到的行动ID\",\n"
        "    \"reason\": \"生成的简短采用理由\"\n"
        "  }\n"
        "]\n"
        "如果没有任何行动符合当前上下文，请直接返回一个空数组：[]"
    )

    user_content = f"【可用行动列表】:\n{json.dumps(actions_meta, ensure_ascii=False)}\n\n【大模型最新回答/上下文内容】:\n{user_intent}"

    try:
        # 4. 调用大模型进行智能推理
        response = await get_async_client().chat.completions.create(
            model=get_model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2,  # 低温度确保稳定的结构化输出
            max_tokens=1024
        )

        res_text = response.choices[0].message.content.strip()
        # 清洗可能存在的 markdown 包裹
        res_text = re.sub(r'^```json\s*|```$', '', res_text, flags=re.IGNORECASE).strip()

        matched_results = json.loads(res_text)
        matched_actions = []

        if isinstance(matched_results, list):
            for item in matched_results:
                act_id = item.get("action_id")
                reason = item.get("reason")
                
                # 从底层的原始 Action 列表中找到对应的条目
                target_action = next((a for a in actions if a["id"] == act_id), None)
                if target_action:
                    act_copy = dict(target_action)
                    act_copy["message"] = reason  # 将大模型生成的动态推荐理由注入到对象中
                    matched_actions.append(act_copy)

        return matched_actions

    except Exception as e:
        print(f"LLM Action Matching Error: {e}")
        # 【降级兜底策略】如果大模型挂了或JSON解析失败，自动降级回原有的关键词模糊匹配，确保业务不中断
        matched = []
        intent_lower = user_intent.lower()
        for a in actions:
            condition = a.get("trigger_condition", "").lower()
            if not condition:
                continue
            keywords = [k.strip() for k in condition.split(",")]
            if any(k in intent_lower for k in keywords if k):
                act_copy = dict(a)
                act_copy["reason"] = f"系统检测到提及了关键词【{condition}】，建议触发此操作。"
                matched.append(act_copy)
        return matched