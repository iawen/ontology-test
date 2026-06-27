"""
会话管理 + 推荐问题 API
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.db.db import get_db
from configs.global_config import Cfg
from tools.helpers import verify_token

router = APIRouter()


# ============================================================
# 会话 CRUD
# ============================================================
@router.get("/api/conversations/{scenario_id}")
async def list_conversations(request: Request, scenario_id: str):
    """获取当前用户的会话列表"""
    user = verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE user_id=? AND scenario_id=? ORDER BY updated_at DESC",
        (user["sub"], scenario_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/conversations/{scenario_id}")
async def create_conversation(scenario_id: str, request: Request):
    """创建新会话"""

    user = verify_token(request, Cfg.jwt_secret)
    body = await request.json()
    title = body.get("title", "新对话")
    conv_id = body.get("id") or str(uuid.uuid4())[:8]
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO conversations (id, user_id, scenario_id, title) VALUES (?,?,?,?)",
            (conv_id, user["sub"], scenario_id, title)
        )
        conn.commit()
    except Exception as e:
        print(f"[ERROR] create_conversation: {e}")
        conn.close()
        raise HTTPException(400, "会话已存在")
    conn.close()
    return {"id": conv_id, "title": title}


@router.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    """更新会话标题"""
    verify_token(request, Cfg.jwt_secret)
    body = await request.json()
    conn = get_db()
    conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                 (body.get("title", ""), datetime.now(timezone.utc), conv_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str, request: Request):
    """删除会话及其消息"""
    verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ============================================================
# 消息 CRUD
# ============================================================

@router.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, request: Request):
    """获取会话的所有消息"""
    verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    rows = conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (conv_id,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        item = {"id": r["id"], "role": r["role"], "content": r["content"]}
        if r["visualization"]:
            try:
                item["visualization"] = json.loads(r["visualization"])
            except:
                pass
        if r["steps"]:
            try:
                item["steps"] = json.loads(r["steps"])
            except:
                pass
        if r["action_confirm"]:
            try:
                item["action_confirm"] = json.loads(r["action_confirm"])
            except:
                pass
        result.append(item)
    return result


@router.post("/api/conversations/{conv_id}/messages")
async def save_message(conv_id: str, request: Request):
    """保存一条消息"""
    verify_token(request, Cfg.jwt_secret)
    body = await request.json()
    conn = get_db()
    conn.executemany(
        "INSERT INTO messages (id, conversation_id, role, content, visualization, steps, action_confirm) VALUES (?,?,?,?,?,?,?)",
        [
            (
                msg.get("id") or str(uuid.uuid4())[:8], 
                conv_id, 
                msg.get("role", "user"), 
                msg.get("content", ""), 
                json.dumps(msg.get("visualization"), ensure_ascii=False, default=str) if msg.get("visualization") else "", 
                json.dumps(msg.get("steps"), ensure_ascii=False, default=str) if msg.get("steps") else "",
                json.dumps(msg.get("action_confirm"), ensure_ascii=False, default=str) if msg.get("action_confirm") else ""
            ) 
            for msg in body.get("messages", [{}])
        ]
    )
    # 更新会话时间
    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (datetime.now(timezone.utc), conv_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ============================================================
# 推荐问题
# ============================================================
@router.get("/api/suggestions/{scenario_id}")
async def list_suggestions(request: Request, scenario_id: str):
    """获取当前用户的会话列表"""
    user = verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM suggested_questions WHERE scenario_id=? ORDER BY sort_order ASC",
        (scenario_id, )
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/suggested-questions")
async def add_suggested_question(request: Request):
    """新增推荐问题"""
    verify_token(request, Cfg.jwt_secret)
    body = await request.json()
    conn = get_db()
    conn.execute(
        "INSERT INTO suggested_questions (scenario_id, icon, question, sort_order) VALUES (?,?,?,?)",
        (body.get("scenario_id"), body.get("icon", "💬"), body.get("question", ""), body.get("sort_order", 99))
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.delete("/api/suggested-questions/{qid}")
async def delete_suggested_question(qid: int, request: Request):
    """删除推荐问题"""
    verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    conn.execute("DELETE FROM suggested_questions WHERE id=?", (qid,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@router.post("/api/suggested-questions/generate")
async def generate_suggested_questions(request: Request):
    """基于当前场景的 Schema，用 LLM 生成推荐问题"""
    verify_token(request, Cfg.jwt_secret)
    params = await request.json()
    from prompts.prompt import get_engine
    from configs.global_config import client

    engine = get_engine()
    schema_summary = engine.get_schema_summary()

    try:
        resp = await client.chat.completions.create(
            model=Cfg.model_name,
            messages=[
                {"role": "system", "content": "你是一个数据分析专家。根据给定的业务本体，生成8个用户最可能问的分析问题。每个问题一行，格式：图标|问题文字。图标从 📊📈🔄⚠️📦💰🏪🗂️ 中选。"},
                {"role": "user", "content": f"业务本体：\n{schema_summary}\n\n请生成8个推荐问题。"}
            ],
            temperature=0.7,
            max_tokens=500,
        )
        text = resp.choices[0].message.content or ""
        questions = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|", 1)
                questions.append({"icon": parts[0].strip(), "question": parts[1].strip()})
            elif line:
                questions.append({"icon": "💬", "question": line})

        # 存入数据库
        conn = get_db()
        # 先清除旧的
        conn.execute("DELETE FROM suggested_questions WHERE scenario_id=?", (params.get("scenario_id"),))
        for i, q in enumerate(questions):
            conn.execute(
                "INSERT INTO suggested_questions (scenario_id, icon, question, sort_order) VALUES (?,?,?,?)",
                (params.get("scenario_id"), q["icon"], q["question"], i + 1)
            )
        conn.commit()
        conn.close()
        return {"status": "ok", "count": len(questions)}
    except Exception as e:
        raise HTTPException(500, f"生成失败: {e}")
