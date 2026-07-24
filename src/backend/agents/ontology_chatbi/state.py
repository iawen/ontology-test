"""
Chat v3 - 状态定义与轻量状态机
==============================
设计原则：
  1. 轻量级字典路由：避免面向对象状态模式的样板代码过载
    2. 显式状态：包含 Schema/查询规划阶段的 FSM，统一调度
  3. 可序列化：AgentState 可转为 dict，支持请求重放和单步调试
"""

from dataclasses import dataclass, field
from enum import StrEnum

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionMessageToolCall, ChatCompletionToolParam


class State(StrEnum):
    """Chat 状态机状态"""

    INIT = "init"
    CONTEXT_PREP = "context_prep"
    METRIC_PLAN_EXECUTE = "metric_plan_execute"
    SCHEMA_PLAN = "schema_plan"
    QUERY_PLAN = "query_plan"
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
    result: dict | None = None
    error: str | None = None
    retry_count: int = 0  # 死循环防线：严格限制最大重试次数
    disambiguated: bool = False  # 是否已经过实体消歧


@dataclass
class AgentState:
    """
    Chat 会话的全局状态。
    主控 Orchestrator 负责读写，子智能体只读不写。
    """

    # 基础信息
    agent_id: str = ""
    session_id: str = ""
    query_id: str = ""
    user_message: str = ""
    messages: list[ChatCompletionMessageParam] = field(default_factory=list)  # 发给 LLM 的消息

    # LLM 配置
    system_prompt: str = ""
    tools: list[ChatCompletionToolParam] = field(default_factory=list)
    max_rounds: int = 20
    current_round: int = 0

    # 上下文管理
    ontology_context: str = ""
    glossary_matches: list[dict] = field(default_factory=list)
    skill_matches: list[dict] = field(default_factory=list)
    entity_hints: list[dict] = field(default_factory=list)  # 实体消歧结果
    schema_context: str = ""
    metric_context: str = ""
    metric_candidates: list[str] = field(default_factory=list)
    routing_candidate_class_ids: list[str] = field(default_factory=list)
    concept_context: str = ""
    concept_candidates: list[dict] = field(default_factory=list)
    analysis_plan: dict = field(default_factory=dict)
    concept_metric_plan_started_at_ms: int | None = None
    query_scope: dict = field(default_factory=dict)
    query_plan: dict = field(default_factory=dict)
    scope_validation: dict = field(default_factory=dict)
    plan_validation: dict = field(default_factory=dict)
    planning_attempts: dict[str, int] = field(default_factory=dict)
    ontology_planning_started_at_ms: int | None = None
    execution_mode_started_at_ms: int | None = None
    planned_query_args: dict | None = None
    query_executed: bool = False
    clarification: dict | None = None
    clarification_reason: str = ""
    missing_required_dimensions: list[dict] = field(default_factory=list)
    missing_dimension_groups: list[dict] = field(default_factory=list)
    dimension_selections: dict[str, dict] = field(default_factory=dict)
    dimension_resolution: dict = field(default_factory=dict)

    # Metrics Plan-Execute：复杂指标问题的受控多证据执行账本
    execution_mode: str = "single_query"
    metric_plan: dict = field(default_factory=dict)
    metric_plan_phase: str = ""
    metric_plan_iteration: int = 0
    metric_subquestions: list[dict] = field(default_factory=list)
    metric_plan_judgments: list[dict] = field(default_factory=list)
    metric_plan_terminal_reason: str = ""
    metric_query_attempts: int = 0

    # 工具执行
    pending_tool_calls: list[ChatCompletionMessageToolCall] = field(default_factory=list)  # LLM 返回的 tool_calls
    tool_call_records: list[ToolCallRecord] = field(default_factory=list)
    all_tool_results: list[dict] = field(default_factory=list)
    tool_timings: dict[str, dict] = field(default_factory=dict)
    tool_reasoning_steps: list[dict] = field(default_factory=list)
    analysis_payload: list[dict] = field(default_factory=list)
    analysis_processed_count: int = 0

    # 持久化输出
    assistant_content: str = ""
    final_reason: str = "normal"
    final_answer_duration_ms: int | None = None
    action_confirm: dict | None = None

    # 流式事件
    sse_events: list[dict] = field(default_factory=list)

    # 错误
    error: str | None = None

    # 调试与请求重放
    transition_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """可序列化，支持请求重放和调试"""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "tool_calls_count": len(self.pending_tool_calls),
            "tool_results_count": len(self.all_tool_results),
            "entity_hints_count": len(self.entity_hints),
            "query_scope": self.query_scope,
            "query_plan": self.query_plan,
            "scope_validation": self.scope_validation,
            "plan_validation": self.plan_validation,
            "execution_mode": self.execution_mode,
            "routing_candidate_class_ids": self.routing_candidate_class_ids,
            "analysis_plan": self.analysis_plan,
            "metric_plan_iteration": self.metric_plan_iteration,
            "metric_plan_phase": self.metric_plan_phase,
            "metric_subquestions_count": len(self.metric_subquestions),
            "metric_query_attempts": self.metric_query_attempts,
            "metric_plan_terminal_reason": self.metric_plan_terminal_reason,
            "clarification_reason": self.clarification_reason,
            "missing_required_dimensions": self.missing_required_dimensions,
            "missing_dimension_groups": self.missing_dimension_groups,
            "dimension_resolution": self.dimension_resolution,
            "transition_log": self.transition_log,
            "error": self.error,
        }

    def record_transition(self, from_state: State, to_state: State):
        """记录状态跳变，便于事后分析和请求重放"""
        self.transition_log.append(
            {
                "from": from_state.value if isinstance(from_state, State) else str(from_state),
                "to": to_state.value if isinstance(to_state, State) else str(to_state),
                "round": self.current_round,
                "pending_tools": len(self.pending_tool_calls),
                "tool_results": len(self.all_tool_results),
            }
        )

    def inject_entity_hints(self, hints: list[dict]):
        """主控专用：注入实体消歧结果"""
        self.entity_hints.extend(hints)

    def inject_context(self, context: str):
        """主控专用：注入本体上下文"""
        self.ontology_context = context
