"""
Chat v3 - API 路由
==================
保持与原 /api/chat 接口兼容，内部使用状态机引擎。
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.models.models import ChatRequest
from tools.logger import logger
from .engine import ChatEngineV3


router = APIRouter()


@router.post("/api/chat")
async def chat_v3(req: ChatRequest):
    """
    Chat v3 接口（状态机驱动 + 无状态子智能体）。

    改进：
      1. 动态上下文注入（解决 Schema 信息爆炸）
      2. 实体消歧 + 后置自动校正（解决 SQL 参数对齐）
      3. 状态机 + 字典路由（解决大循环难以优化）
      4. 无状态子智能体（严格输入输出契约）
      5. 死循环防线（retry_count 限制）
    """
    logger.info(
        "Chat API request received: scenario_id=%s conversation_id=%s messages=%d",
        req.scenario_id,
        req.conversation_id,
        len(req.messages or []),
    )
    engine = ChatEngineV3()
    return StreamingResponse(
        engine.stream_chat(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
