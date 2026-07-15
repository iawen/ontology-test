"""会话管理 API。"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from configs.global_config import Cfg
from core.db.db import get_db
from tools.helpers import verify_token

router = APIRouter()


@router.get("/api/conversations/{scenario_id}")
async def list_conversations(request: Request, scenario_id: str):
    """获取当前用户的会话列表"""
    user = verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE user_id=? AND scenario_id=? ORDER BY updated_at DESC",
        (user["sub"], scenario_id),
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
            (conv_id, user["sub"], scenario_id, title),
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
    user = verify_token(request, Cfg.jwt_secret)
    body = await request.json()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "会话标题不能为空")
    if len(title) > 100:
        raise HTTPException(400, "会话标题不能超过 100 个字符")
    conn = get_db()
    cursor = conn.execute(
        "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND user_id=?",
        (title, datetime.now(timezone.utc), conv_id, user["sub"]),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(404, "会话不存在")
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


@router.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str, request: Request):
    """获取会话的所有消息"""
    verify_token(request, Cfg.jwt_secret)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        item = {"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"]}
        if r["visualization"]:
            try:
                item["visualization"] = json.loads(r["visualization"])
            except Exception:
                pass
        if "answer_datasets" in r.keys() and r["answer_datasets"]:
            try:
                item["answer_datasets"] = json.loads(r["answer_datasets"])
            except Exception:
                pass
        if r["steps"]:
            try:
                item["steps"] = json.loads(r["steps"])
            except Exception:
                pass
        if r["action_confirm"]:
            try:
                item["action_confirm"] = json.loads(r["action_confirm"])
            except Exception:
                pass
        result.append(item)
    return result


