"""
Plan-and-Execute 引擎 + 工作流执行引擎
========================================
1. 多步规划与执行：将复杂问题分解为子步骤，逐步执行
2. 工作流引擎：支持多步骤工作流的定义、创建、执行和状态追踪
"""

import json
import uuid
import time
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, HTTPException

from core.db.db import get_db

router = APIRouter()


# ============================================================
# 工作流定义与状态
# ============================================================

class WorkflowStepStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    waiting_approval = "waiting_approval"


class WorkflowStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    paused = "paused"


# ── 工作流实例表（运行时） ──
WORKFLOW_INSTANCES_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_instances (
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    workflow_def_id TEXT DEFAULT '',
    workflow_name TEXT NOT NULL,
    action_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    current_step INTEGER DEFAULT 0,
    total_steps INTEGER DEFAULT 0,
    context TEXT DEFAULT '{}',
    steps_json TEXT DEFAULT '[]',
    result TEXT DEFAULT '',
    triggered_by TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# ── 工作流步骤执行日志 ──
WORKFLOW_STEP_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_step_logs (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    step_type TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'pending',
    input_data TEXT DEFAULT '{}',
    output_data TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT '',
    duration REAL DEFAULT 0
);
"""


def ensure_workflow_tables():
    """确保工作流表存在"""
    conn = get_db()
    conn.executescript(WORKFLOW_INSTANCES_TABLE + WORKFLOW_STEP_LOGS_TABLE)
    conn.commit()
    conn.close()


# ============================================================
# 工作流 API
# ============================================================

@router.get("/api/admin/scenarios/{scenario_id}/workflow_instances")
async def list_workflow_instances(scenario_id: str, status: str = "", page: int = 1, page_size: int = 20):
    """列出工作流实例"""
    ensure_workflow_tables()
    conn = get_db()
    offset = (page - 1) * page_size
    if status:
        rows = conn.execute(
            "SELECT * FROM workflow_instances WHERE scenario_id=? AND status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (scenario_id, status, page_size, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workflow_instances WHERE scenario_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (scenario_id, page_size, offset)
        ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["steps_json"] = json.loads(d.get("steps_json", "[]"))
        d["context"] = json.loads(d.get("context", "{}"))
        result.append(d)
    return result


@router.get("/api/admin/scenarios/{scenario_id}/workflow_instances/{instance_id}")
async def get_workflow_instance(scenario_id: str, instance_id: str):
    """获取工作流实例详情"""
    ensure_workflow_tables()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM workflow_instances WHERE id=? AND scenario_id=?",
        (instance_id, scenario_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "工作流实例不存在")
    d = dict(row)
    d["steps_json"] = json.loads(d.get("steps_json", "[]"))
    d["context"] = json.loads(d.get("context", "{}"))
    # 获取步骤日志
    conn = get_db()
    logs = conn.execute(
        "SELECT * FROM workflow_step_logs WHERE instance_id=? ORDER BY step_index",
        (instance_id,)
    ).fetchall()
    conn.close()
    d["step_logs"] = [dict(l) for l in logs]
    return d


# ============================================================
# 工作流执行引擎（核心）
# ============================================================

def _execute_workflow_instance(instance: dict) -> dict:
    """从当前步骤开始执行工作流"""
    steps = instance.get("steps_json", [])
    context = instance.get("context", {})
    current_step = instance.get("current_step", 0)
    instance_id = instance["id"]

    # 更新状态为 running
    _update_instance(instance_id, steps, context, current_step, "running")

    return _execute_workflow_from_step(instance, current_step)


def _execute_workflow_from_step(instance: dict, start_step: int) -> dict:
    """从指定步骤开始执行工作流"""
    steps = instance.get("steps_json", [])
    if isinstance(steps, str):
        steps = json.loads(steps)
    context = instance.get("context", {})
    if isinstance(context, str):
        context = json.loads(context)
    instance_id = instance["id"]
    scenario_id = instance["scenario_id"]

    for i in range(start_step, len(steps)):
        step = steps[i]
        step_name = step.get("name", f"步骤{i+1}")
        step_type = step.get("type", "auto")
        step_config = step.get("config", {})

        # 如果是审批步骤，暂停等待
        if step_type == "approval":
            step["status"] = "waiting_approval"
            steps[i] = step
            _update_instance(instance_id, steps, context, i, "paused")
            _log_step(instance_id, i, step_name, "waiting_approval", {}, {})
            return {
                "status": "paused",
                "reason": "waiting_approval",
                "step_index": i,
                "step_name": step_name,
                "message": f"步骤「{step_name}」需要审批，请确认后继续",
            }

        # 执行步骤
        step["status"] = "running"
        steps[i] = step
        _update_instance(instance_id, steps, context, i, "running")
        _log_step(instance_id, i, step_name, "running", step_config, {})

        start_time = time.time()
        try:
            step_result = _execute_workflow_step(scenario_id, step, context)
            step["status"] = "completed"
            # 将步骤输出合并到上下文
            if isinstance(step_result, dict):
                context.update(step_result.get("output", {}))
            duration = time.time() - start_time
            _log_step(instance_id, i, step_name, "completed", step_config, step_result, duration=duration)
        except Exception as e:
            step["status"] = "failed"
            duration = time.time() - start_time
            _log_step(instance_id, i, step_name, "failed", step_config, {}, error=str(e), duration=duration)
            steps[i] = step
            _update_instance(instance_id, steps, context, i, "failed")
            return {"status": "failed", "step_index": i, "step_name": step_name, "error": str(e)}

        steps[i] = step

    # 所有步骤完成
    _update_instance(instance_id, steps, context, len(steps), "completed")
    return {"status": "completed", "total_steps": len(steps), "context": context}


def _execute_workflow_step(scenario_id: str, step: dict, context: dict) -> dict:
    """执行单个工作流步骤"""
    step_type = step.get("type", "auto")
    config = step.get("config", {})

    if step_type == "auto" or step_type == "notification":
        # 自动步骤：发送通知
        from modules.actions import _execute_notification
        action = {
            "name": step.get("name", ""),
            "parameters": {
                "message_template": config.get("message_template", "工作流步骤执行：{step_name}"),
                "severity": config.get("severity", "info"),
            },
            "scenario_id": scenario_id,
        }
        merged_context = {**context, "step_name": step.get("name", "")}
        result = _execute_notification(action, merged_context)
        return {"output": result, "step_type": step_type}

    elif step_type == "webhook":
        from modules.actions import _execute_webhook
        action = {
            "name": step.get("name", ""),
            "parameters": config,
            "scenario_id": scenario_id,
        }
        result = _execute_webhook(action, context)
        return {"output": result, "step_type": step_type}

    elif step_type == "data_update":
        from modules.actions import _execute_data_update
        action = {
            "name": step.get("name", ""),
            "parameters": config,
            "target_object": config.get("target_object", ""),
            "scenario_id": scenario_id,
        }
        result = _execute_data_update(action, context)
        return {"output": result, "step_type": step_type}

    elif step_type == "delay":
        # 延迟步骤（配置等待时间）
        delay_seconds = config.get("delay_seconds", 0)
        return {"output": {"delayed": delay_seconds, "message": f"等待 {delay_seconds} 秒"}, "step_type": step_type}

    else:
        return {"output": {"message": f"未知步骤类型: {step_type}"}, "step_type": step_type}


def _update_instance(instance_id: str, steps: list, context: dict, current_step: int, status: str):
    """更新工作流实例状态"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        "UPDATE workflow_instances SET steps_json=?, context=?, current_step=?, status=?, updated_at=? WHERE id=?",
        (json.dumps(steps, ensure_ascii=False), json.dumps(context, ensure_ascii=False),
         current_step, status, now, instance_id),
    )
    conn.commit()
    conn.close()


def _log_step(instance_id: str, step_index: int, step_name: str, status: str,
              input_data: dict = {}, output_data: dict = {}, error: str = "", duration: float = 0):
    """记录步骤执行日志"""
    log_id = str(uuid.uuid4())[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO workflow_step_logs
               (id, instance_id, step_index, step_name, step_type, status,
                input_data, output_data, error_message, started_at, finished_at, duration)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (log_id, instance_id, step_index, step_name, "auto", status,
             json.dumps(input_data, ensure_ascii=False),
             json.dumps(output_data, ensure_ascii=False),
             error, now, now if status in ("completed", "failed", "skipped") else "", duration),
        )
        conn.commit()
    except Exception as e:
        print(f"[WorkflowStepLog] Error: {e}")
    finally:
        conn.close()


# ============================================================
# Plan-and-Execute 引擎
# ============================================================

def create_execution_plan(user_question: str, scenario_id: str) -> dict:
    """
    根据用户问题创建执行计划。
    将复杂问题分解为多个子步骤。
    """
    return {
        "question": user_question,
        "scenario_id": scenario_id,
        "steps": [],
        "status": "planning",
    }


def compress_context(messages: list[dict], max_messages: int = 20) -> list[dict]:
    """
    上下文压缩：当对话历史过长时，压缩早期消息。
    保留系统提示 + 最近的消息 + 工具调用结果摘要。
    """
    if len(messages) <= max_messages:
        return messages

    # 保留系统消息
    system_msgs = [m for m in messages if m.get("role") == "system"]

    # 保留最近的消息
    recent_msgs = messages[-max_messages:]

    # 如果有被截断的消息，插入一条压缩摘要
    skipped = messages[len(system_msgs):-max_messages]
    if skipped:
        # 提取工具调用摘要
        tool_calls_summary = []
        for m in skipped:
            if m.get("role") == "tool":
                try:
                    result = json.loads(m.get("content", "{}"))
                    tool_name = result.get("tool", "unknown")
                    tool_calls_summary.append(f"[{tool_name}]")
                except Exception:
                    pass

        summary = "（之前的对话已压缩）"
        if tool_calls_summary:
            summary += f" 已执行工具: {', '.join(tool_calls_summary[-5:])}"

        compressed_msg = {
            "role": "assistant",
            "content": summary,
        }
        return system_msgs + [compressed_msg] + recent_msgs

    return system_msgs + recent_msgs


def should_plan(user_question: str) -> bool:
    """
    判断用户问题是否需要多步规划。
    复杂问题特征：包含多个子问题、需要跨表关联、需要多步计算。
    """
    complex_indicators = [
        "并且", "同时", "对比", "分别", "以及", "还有",
        "和", "与", "又", "一方面", "另一方面",
        "综合", "整体", "全面", "多角度",
        "比较", "差异", "关联", "影响",
    ]
    question_lower = user_question.lower()
    indicator_count = sum(1 for ind in complex_indicators if ind in question_lower)
    return indicator_count >= 1 or len(user_question) > 50


def decompose_question(user_question: str) -> list[dict]:
    """
    将复杂问题分解为子问题列表。
    返回格式: [{"id": 1, "question": "...", "depends_on": []}]
    """
    # 简单的规则分解（实际生产中应由 LLM 完成）
    sub_questions = []

    # 按标点分割
    separators = ["并且", "同时", "以及", "还有", "，", "；"]
    parts = [user_question]
    for sep in separators:
        new_parts = []
        for part in parts:
            splits = part.split(sep)
            new_parts.extend([s.strip() for s in splits if s.strip()])
        parts = new_parts

    if len(parts) <= 1:
        # 无法分解，返回原问题
        return [{"id": 1, "question": user_question, "depends_on": []}]

    for i, part in enumerate(parts):
        sub_questions.append({
            "id": i + 1,
            "question": part,
            "depends_on": [] if i == 0 else [i],
        })

    return sub_questions


# ============================================================
# 从 Action 创建工作流的便捷方法
# ============================================================

def create_workflow_from_action(action: dict, context: dict = {}) -> dict:
    """
    从 Action 定义创建工作流实例。
    workflow 类型的 Action 的 parameters 中定义了 steps。
    """
    ensure_workflow_tables()
    params = action.get("parameters", {})
    workflow_id = params.get("workflow_id", action.get("id", ""))
    step_definitions = params.get("steps", [])

    if not step_definitions:
        # 如果没有定义步骤，创建一个简单的单步工作流
        step_definitions = [{"name": action.get("name", ""), "type": "notification", "config": params}]

    # 转换为工作流步骤格式
    steps = []
    for i, step_def in enumerate(step_definitions):
        if isinstance(step_def, str):
            steps.append({
                "name": step_def,
                "type": "approval" if "审批" in step_def or "确认" in step_def else "auto",
                "config": {},
            })
        elif isinstance(step_def, dict):
            steps.append({
                "name": step_def.get("name", f"步骤{i+1}"),
                "type": step_def.get("type", "auto"),
                "config": step_def.get("config", {}),
            })

    instance_id = str(uuid.uuid4())[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps_with_status = []
    for i, step in enumerate(steps):
        steps_with_status.append({
            "index": i,
            "name": step["name"],
            "type": step["type"],
            "config": step["config"],
            "status": "pending",
        })

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO workflow_instances
               (id, scenario_id, workflow_def_id, workflow_name, action_id, status,
                current_step, total_steps, context, steps_json, triggered_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (instance_id, action["scenario_id"], workflow_id, action.get("name", "工作流"),
             action.get("id", ""), "pending", 0, len(steps_with_status),
             json.dumps(context, ensure_ascii=False),
             json.dumps(steps_with_status, ensure_ascii=False),
             "action", now, now),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return {"status": "error", "error": str(e)}
    conn.close()

    # 立即执行
    instance = {
        "id": instance_id,
        "scenario_id": action["scenario_id"],
        "steps_json": steps_with_status,
        "context": context,
        "current_step": 0,
    }
    result = _execute_workflow_instance(instance)
    result["instance_id"] = instance_id
    return result
