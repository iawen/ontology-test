"""
Data Query Chat v3 - 本地调试 API 路由
==================
使用 session_id/agent_id/message 请求体，内部映射到状态机引擎并流式返回 SSE。
"""

import shortuuid
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from tools.logger import logger

from .engine import ChatEngineV3
from core.models.models import ChatRequest

router = APIRouter()  # 本路由仅供本地调试导入使用，不在 app.api.router 中注册。


@router.post("/api/chat")
async def chat_v3(req: ChatRequest):
    """
    Data Query 本地调试接口（状态机驱动 + 无状态子智能体）。

    改进：
      1. 动态上下文注入（解决 Schema 信息爆炸）
      2. 实体消歧 + 后置自动校正（解决 SQL 参数对齐）
      3. 状态机 + 字典路由（解决大循环难以优化）
      4. 无状态子智能体（严格输入输出契约）
      5. 死循环防线（retry_count 限制）
    """
    logger.info(
        "Chat API request received: agent_id=%s session_id=%s message_len=%d language=%s",
        req.agent_id,
        req.session_id,
        len(req.message),
        req.language,
    )
    query_id = "chat_" + str(shortuuid.random())
    req = req.model_copy(update={"query_id": query_id})
    return StreamingResponse(
        ChatEngineV3().stream_chat(req),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
