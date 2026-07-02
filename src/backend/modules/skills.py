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

from core.llm.chat_model import get_async_client, get_model_name
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


# ============================================================
# LLM 意图路由 — 核心方法
# ============================================================

async def route_skills(scenario_id: str, user_message: str, conversation_history: list[dict] = None) -> list[dict]:
    """
    通过 LLM 意图路由匹配技能包。
    
    流程：
    1. 从 DB 加载所有激活的 skill
    2. 构建 skill 摘要列表（id + name + trigger_condition）
    3. 调用 LLM 判断哪些 skill 匹配用户消息
    4. 返回匹配的 skill 完整内容
    
    优势（vs 关键词匹配）：
    - 语义理解："最近卖得不好" → 匹配"销售分析"
    - 上下文感知：结合对话历史判断意图
    - 精确匹配：避免关键词误触发
    - 多 skill 组合：可同时匹配多个技能
    """

    # 加载激活的 skill
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM skills WHERE scenario_id=? AND is_active=1 ORDER BY sort_order",
        (scenario_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # 构建 skill 摘要
    skill_summaries = []
    for r in rows:
        skill_summaries.append({
            "id": r["id"],
            "name": r["name"],
            "trigger_condition": r["trigger_condition"] or r["description"],
        })

    # 构建对话上下文（最近 3 轮）
    context = ""
    if conversation_history:
        recent = conversation_history[-6:]  # 最近 3 轮（每轮 user+assistant）
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                context += f"{role}: {content}\n"

    # 构建意图路由 prompt
    skill_list_str = "\n".join([
        f"- ID: {s['id']} | 名称: {s['name']} | 触发条件: {s['trigger_condition']}"
        for s in skill_summaries
    ])

    routing_prompt = f"""你是一个意图识别引擎。根据用户消息和对话上下文，判断应该激活以下哪些技能。

可用技能：
{skill_list_str}

对话上下文：
{context if context else "（无历史对话）"}

当前用户消息：{user_message}

请判断哪些技能与用户当前问题相关。注意：
1. 只选择真正相关的技能，不要过度匹配
2. 可以同时匹配多个技能
3. 如果没有匹配的技能，返回空列表
4. 严格按 JSON 格式返回

返回格式：
{{"matched": ["skill_id_1", "skill_id_2"], "reason": "匹配原因简述"}}"""

    # 调用 LLM
    try:
        response = await get_async_client().chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": routing_prompt}],
            temperature=0.1,  # 低温度，确保稳定输出
            max_tokens=256,
        )
        raw = response.choices[0].message.content or ""
    except Exception as e:
        print(f"[SkillRouter] LLM 调用失败: {e}")
        return []

    # 解析 LLM 返回
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            result = json.loads(match.group())
        else:
            result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[SkillRouter] JSON 解析失败: {raw}")
        return []

    matched_ids = result.get("matched", [])
    reason = result.get("reason", "")
    
    if not matched_ids:
        return []

    print(f"[SkillRouter] 匹配技能: {matched_ids}, 原因: {reason}")

    # 返回匹配的 skill 完整内容
    matched_skills = []
    for r in rows:
        if r["id"] in matched_ids:
            matched_skills.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "trigger_condition": r["trigger_condition"],
                "content": r["content"],
            })

    return matched_skills
