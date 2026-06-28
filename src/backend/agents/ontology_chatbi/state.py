"""
Chat v3 - 状态定义与轻量状态机
==============================
设计原则：
  1. 轻量级字典路由：避免面向对象状态模式的样板代码过载
  2. 显式状态：9 状态 FSM，统一调度
  3. 可序列化：AgentState 可转为 dict，支持请求重放和单步调试
"""

import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


class State(str, Enum):
    """Chat 状态机状态"""
    INIT = "init"
    CONTEXT_PREP = "context_prep"
    LLM_CALL = "llm_call"
    TOOL_DISPATCH = "tool_dispatch"
    TOOL_EXECUTE = "tool_execute"
    CLARIFY = "clarify"
    ACTION_CONFIRM = "action_confirm"
    ACTION_EXECUTE = "action_execute"
    FINAL_STREAM = "final_stream"
    DONE = "done"
    ERROR = "error"


@dataclass
class ToolCallRecord:
    """单次工具调用记录（用于重试追踪）"""
    tool_name: str
    arguments: dict
    result: Optional[dict] = None
    error: Optional[str] = None
    retry_count: int = 0  # 死循环防线：严格限制最大重试次数
    disambiguated: bool = False  # 是否已经过实体消歧


@dataclass
class AgentState:
    """
    Chat 会话的全局状态。
    主控 Orchestrator 负责读写，子智能体只读不写。
    """
    # 基础信息
    scenario_id: str = ""
    conversation_id: str = ""
    user_message: str = ""
    messages: List[dict] = field(default_factory=list)  # 发给 LLM 的消息

    # LLM 配置
    system_prompt: str = ""
    tools: List[dict] = field(default_factory=list)
    max_rounds: int = 20
    current_round: int = 0

    # 上下文管理
    ontology_context: str = ""
    glossary_matches: List[dict] = field(default_factory=list)
    skill_matches: List[dict] = field(default_factory=list)
    entity_hints: List[dict] = field(default_factory=list)  # 实体消歧结果

    # 工具执行
    pending_tool_calls: List[dict] = field(default_factory=list)  # LLM 返回的 tool_calls
    tool_call_records: List[ToolCallRecord] = field(default_factory=list)
    all_tool_results: List[dict] = field(default_factory=list)
    tool_timings: Dict[str, dict] = field(default_factory=dict)

    # 持久化输出
    assistant_content: str = ""
    action_confirm: Optional[dict] = None

    # 流式事件
    sse_events: List[dict] = field(default_factory=list)

    # 错误
    error: Optional[str] = None

    # 调试与请求重放
    transition_log: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """可序列化，支持请求重放和调试"""
        return {
            "scenario_id": self.scenario_id,
            "conversation_id": self.conversation_id,
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "tool_calls_count": len(self.pending_tool_calls),
            "tool_results_count": len(self.all_tool_results),
            "entity_hints_count": len(self.entity_hints),
            "transition_log": self.transition_log,
            "error": self.error,
        }

    def record_transition(self, from_state: State, to_state: State):
        """记录状态跳变，便于事后分析和请求重放"""
        self.transition_log.append({
            "from": from_state.value if isinstance(from_state, State) else str(from_state),
            "to": to_state.value if isinstance(to_state, State) else str(to_state),
            "round": self.current_round,
            "pending_tools": len(self.pending_tool_calls),
            "tool_results": len(self.all_tool_results),
        })

    def inject_entity_hints(self, hints: List[dict]):
        """主控专用：注入实体消歧结果"""
        self.entity_hints.extend(hints)

    def inject_context(self, context: str):
        """主控专用：注入本体上下文"""
        self.ontology_context = context
