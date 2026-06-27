"""
Chat v3 - 状态机驱动 + 无状态子智能体
=====================================
融合方案：
  - 状态机骨架（9 状态 FSM）+ 字典路由（轻量级，避免样板代码）
  - 无状态子智能体（SchemaRetriever/Glossary/Skill/Entity/Compressor）
  - 后置自动校正 + 死循环防线（retry_count 限制）

使用示例：
    from chat_v3 import ChatEngineV3
    engine = ChatEngineV3()
    async for event in engine.stream_chat(req):
        yield event
"""

from .engine import ChatEngineV3
from .state import State, AgentState, ToolCallRecord
from .agents import (
    SchemaRetrieverAgent,
    GlossaryMatcherAgent,
    SkillRouterAgent,
    ContextCompressorAgent,
    EntityDisambiguatorAgent,
    ToolExecutor,
)

__all__ = [
    "ChatEngineV3",
    "State",
    "AgentState",
    "ToolCallRecord",
    "SchemaRetrieverAgent",
    "GlossaryMatcherAgent",
    "SkillRouterAgent",
    "ContextCompressorAgent",
    "EntityDisambiguatorAgent",
    "ToolExecutor",
]
