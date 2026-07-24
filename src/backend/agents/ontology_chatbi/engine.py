"""
Chat v3 - 主控 Orchestrator
============================
设计原则：
  1. 轻量级字典路由：避免面向对象状态模式的样板代码过载
  2. 扁平循环：主循环通过函数映射实现插件化，调用栈浅
  3. 主控算状态，子智能体算算子：子 Agent 严格无状态

状态流转：
  INIT → CONTEXT_PREP → LLM_CALL → TOOL_DISPATCH → TOOL_EXECUTE → LLM_CALL (循环)
                                                          ↓
                                              CLARIFY / ACTION / FINAL_STREAM / DONE
"""

import json
import math
import re
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import cast

import shortuuid
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionMessageToolCall

from core.llm.chat_model import get_async_client, get_model_name
from tools.logger import logger
from core.db.db import get_db

from .constants import (
    CAUSE_QUERY_KEYWORDS,
    COMPARISON_EVIDENCE_KEYWORDS,
    COMPARISON_QUERY_KEYWORDS,
    DIRECT_ANSWER_MAX_JSON_CHARS,
    DIRECT_ANSWER_MAX_ROWS,
    FINAL_AFTER_TOOL_MAX_ROWS,
    FINAL_ANSWER_MAX_CONTEXT_CHARS,
    FINAL_ANSWER_SAMPLE_HEAD_ROWS,
    FINAL_ANSWER_SAMPLE_TAIL_ROWS,
    METRIC_PLAN_MAX_INITIAL_SUBQUESTIONS,
    METRIC_PLAN_MAX_ITERATIONS,
    METRIC_PLAN_MAX_QUERY_ATTEMPTS,
    METRIC_PLAN_MAX_SUBQUESTION_CHARS,
    PYTHON_ANALYZE_COMPLEX_PATTERNS,
    RATIO_EVIDENCE_KEYWORDS,
    RATIO_QUERY_KEYWORDS,
    SSE_CACHE_TTL_SECONDS,
    TIME_DIMENSION_KEYWORDS,
)
from .helper import get_tool_display_name, get_tool_purpose, metric_target_classes
from core.models.models import ChatRequest
from .node import (
    AnalysisOrganizerTool,
    ClarifyAgent,
    ConceptMetricPlanner,
    ContextCompressorAgent,
    EntityDisambiguatorAgent,
    GlossaryMatcherAgent,
    OntologyAgent,
    PlanExecuteAgent,
    SchemaRetrieverAgent,
    ToolExecutor,
)
from .prompt import (
    FINAL_ANSWER_PROMPT,
    get_engine,
    get_query_engine,
    get_system_tools,
    init_prompt,
)
from .state import AgentState, State, ToolCallRecord


class ChatEngineV3:
    """状态机驱动的 Chat 引擎（字典路由+扁平循环）"""

    def __init__(self):
        self.client = get_async_client()
        self.model_name = get_model_name()

        # 无状态子智能体实例化
        self.schema_agent = SchemaRetrieverAgent()
        self.glossary_agent = GlossaryMatcherAgent()
        self.compressor_agent = ContextCompressorAgent()
        self.entity_agent = EntityDisambiguatorAgent()
        self.ontology_agent = OntologyAgent(self.client, self.model_name)
        self.plan_execute_agent = PlanExecuteAgent(self.client, self.model_name)
        self.concept_metric_planner = ConceptMetricPlanner()
        self.clarify_agent = ClarifyAgent()
        self.analysis_tool = AnalysisOrganizerTool(self.client, self.model_name)

    async def stream_chat(self, req: ChatRequest) -> AsyncGenerator[str, None]:
        """
        状态机驱动的流式聊天。

        使用字典路由替代 if-elif 链，保持主循环扁平。
        """
        query_id = req.query_id or str(shortuuid.random())
        session_id = req.session_id or str(shortuuid.random())
        agent_id = req.agent_id or ""
        user_message = req.message or ""
        checkpoint_id = str((req.options or {}).get("clarification_checkpoint_id") or "").strip()
        print(f"\n\n++++++++++++++++++++++\nuser_message： {user_message}\n++++++++++++++++++++++\n\n")
        history_messages = await self._load_conversation_history(session_id, exclude_query_id=query_id)
        current_user_message = {"role": "user", "content": user_message}
        llm_messages = [*history_messages, current_user_message]

        state = AgentState(
            agent_id=agent_id,
            session_id=session_id,
            query_id=query_id,
            user_message=user_message,
            messages=cast(list[ChatCompletionMessageParam], llm_messages),
        )
        resume_checkpoint = self._claim_clarification_checkpoint(checkpoint_id, session_id, agent_id) if checkpoint_id else None
        if checkpoint_id and not resume_checkpoint:
            state.error = "澄清会话已失效、已完成或不属于当前会话，请重新发起查询。"
        elif resume_checkpoint:
            state = self._restore_clarification_checkpoint(
                resume_checkpoint, query_id=query_id, session_id=session_id, agent_id=agent_id
            )
        answers = (req.options or {}).get("clarification_answers") or []
        if isinstance(answers, list):
            state.dimension_selections = {
                str(answer.get("group_id")): {
                    "option_value": str(answer.get("option_value") or ""),
                    "selection_value": str(answer.get("selection_value") or ""),
                }
                for answer in answers
                if isinstance(answer, dict) and answer.get("group_id") and answer.get("option_value")
            }
        persisted_user_content = str(
            (req.options or {}).get("clarification_display") or user_message
        )
        self._persist_conversation_message(session_id, "user", persisted_user_content)

        # 状态路由表（轻量级字典路由，替代 if-elif 链）
        handlers = {
            State.INIT: self._handle_init,
            State.CONTEXT_PREP: self._handle_context_prep,
            State.METRIC_PLAN_EXECUTE: self._handle_metric_plan_execute,
            State.SCHEMA_PLAN: self._handle_schema_plan,
            State.QUERY_PLAN: self._handle_query_plan,
            State.LLM_CALL: self._handle_llm_call,
            State.TOOL_DISPATCH: self._handle_tool_dispatch,
            State.TOOL_EXECUTE: self._handle_tool_execute,
            State.CLARIFY: self._handle_clarify,
            State.FINAL_STREAM: self._handle_final_stream,
            State.DONE: self._handle_done,
            State.ERROR: self._handle_error,
        }

        current_state = State.ERROR if state.error else State.INIT
        if resume_checkpoint:
            current_state = self._resume_after_clarification(state)
        start_time = time.time()
        transition_count = 0
        max_transitions = 50

        logger.info(
            "Chat stream started: agent_id=%s session_id=%s query_id=%s messages=%d(%s) user_message_len=%d",
            state.agent_id,
            state.session_id,
            state.query_id,
            len(state.messages),
            user_message,
            len(state.user_message),
        )

        try:
            start_event = {
                "type": "query_started",
                "query_id": state.query_id,
                "session_id": state.session_id,
                "status": "running",
            }
            yield await self._format_sse_event(state.query_id, start_event)

            while current_state != State.DONE:
                transition_count += 1
                if transition_count > max_transitions:
                    state.error = f"状态机跳转超过上限 {max_transitions}，已熔断以避免死循环。"
                    current_state = State.ERROR

                handler = handlers.get(current_state)
                if not handler:
                    current_state = State.ERROR
                    state.error = f"未知状态: {current_state}"
                    continue

                logger.debug(
                    "Chat state enter: session_id=%s state=%s round=%d",
                    state.session_id,
                    current_state.value,
                    state.current_round,
                )
                next_state = await handler(state)
                state.record_transition(current_state, next_state)

                # 发送待处理的 SSE 事件
                for event in state.sse_events:
                    yield await self._format_sse_event(state.query_id, event)
                state.sse_events.clear()

                current_state = next_state

            # await self._emit_analysis_event(state)
            for event in state.sse_events:
                yield await self._format_sse_event(state.query_id, event)
            state.sse_events.clear()

            supporting_datasets = self._build_supporting_datasets(state)
            persisted_steps = self._build_persisted_tool_steps(state.all_tool_results)
            final_answer = self._build_final_answer(
                state.assistant_content,
                supporting_datasets=supporting_datasets,
                duration_ms=state.final_answer_duration_ms,
            )
            done_event = {
                "type": "done",
                "query_id": state.query_id,
                "final_answer": final_answer,
                "total_duration_ms": int((time.time() - start_time) * 1000),
                "steps": persisted_steps,
                # "tool_results": state.all_tool_results,
            }
            self._persist_conversation_message(
                session_id,
                "assistant",
                state.assistant_content,
                answer_datasets=self._build_persisted_answer_datasets(supporting_datasets),
                visualization=self._build_persisted_visualization(supporting_datasets),
                steps=persisted_steps,
                action_confirm=state.action_confirm,
            )
            yield await self._format_sse_event(state.query_id, done_event)
            logger.info(
                "Chat stream completed: agent_id=%s session_id=%s query_id=%s rounds=%d tools=%d duration_ms=%d",
                state.agent_id,
                state.session_id,
                state.query_id,
                state.current_round,
                len(state.all_tool_results),
                int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.exception(
                "Chat stream failed: agent_id=%s session_id=%s state=%s error=%s",
                state.agent_id,
                state.session_id,
                current_state.value if isinstance(current_state, State) else current_state,
                str(e),
            )
            error_event = {"type": "error", "query_id": query_id, "content": str(e)}
            self._persist_conversation_message(session_id, "assistant", f"智能体系统异常: {e}")
            yield await self._format_sse_event(query_id, error_event)

    # ============================================================
    # 状态处理函数（每个函数返回下一个状态）
    # ============================================================
    async def _handle_init(self, state: AgentState) -> State:
        """初始化场景"""
        await init_prompt(state.agent_id)
        state.tools = get_system_tools()
        logger.info(
            "Chat initialized: agent_id=%s session_id=%s tools=%d planning_mode=staged",
            state.agent_id,
            state.session_id,
            len(state.tools or []),
        )
        return State.CONTEXT_PREP

    async def _handle_context_prep(self, state: AgentState) -> State:
        """准备术语与候选 Schema 摘要，不向规划模型注入全量本体。"""
        glossary_matches = await self.glossary_agent.match(state.agent_id, state.user_message)
        state.glossary_matches = glossary_matches
        engine = get_engine(state.agent_id)
        schema_result = await self.schema_agent.retrieve(state.user_message, engine)
        state.schema_context = str(schema_result.get("schema_context") or "")
        state.metric_context = str(schema_result.get("metric_context") or "")
        state.metric_candidates = [
            str(metric_id)
            for metric_id in schema_result.get("relevant_metrics", [])
            if isinstance(metric_id, str) and metric_id
        ]
        state.concept_metric_plan_started_at_ms = int(time.time() * 1000)
        concept_retrieval = self.concept_metric_planner.build_retrieval_context(
            state.user_message, engine
        )
        state.concept_context = str(concept_retrieval.get("concept_context") or "")
        state.concept_candidates = list(concept_retrieval.get("relevant_concepts") or [])
        state.analysis_plan = self.concept_metric_planner.build_analysis_plan(
            state.user_message,
            engine,
            state.metric_candidates,
            concept_retrieval,
        )
        state.ontology_context = state.schema_context

        logger.info(
            "Chat planning context ready: session_id=%s glossary=%d schema_context_len=%d metric_context_len=%d concept_candidates=%d bundles=%d",
            state.session_id,
            len(glossary_matches),
            len(state.schema_context),
            len(state.metric_context),
            len(state.concept_candidates),
            len(state.analysis_plan.get("metric_bundles", [])),
        )
        state.execution_mode_started_at_ms = int(time.time() * 1000)
        try:
            routing = await self.plan_execute_agent.decide_execution_mode(
                state.user_message,
                state.schema_context,
                state.metric_context,
                state.glossary_matches,
                has_metric_evidence=bool(state.metric_context.strip() or state.metric_candidates or engine.list_metrics()),
                session_id=state.session_id,
            )
        except Exception as exc:
            logger.warning(
                "Query mode routing failed; falling back to single query: session_id=%s error=%s",
                state.session_id,
                str(exc),
            )
            routing = {
                "mode": "single_query",
                "reason": "路由判定异常，降级为单任务查询。",
                "single_query_sufficient": True,
                "required_evidence": [],
                "confidence": "low",
                "decision_source": "fallback",
                "matched_rule": "",
            }
        state.execution_mode = "metric_plan_execute" if routing.get("mode") == "plan_execute" else "single_query"
        state.routing_candidate_class_ids = self._string_list(
            routing.get("candidate_class_ids")
        ) or list(schema_result.get("relevant_classes") or [])
        self._emit_execution_mode_routing_event(state, routing)
        if state.execution_mode == "metric_plan_execute":
            if state.analysis_plan:
                self._append_metric_plan_step(
                    state,
                    "concept_metric_analysis_plan",
                    "已根据业务 Concept 生成候选分析域、指标组合与分析轴。",
                    state.analysis_plan,
                    started_at_ms=state.concept_metric_plan_started_at_ms,
                )
            logger.info(
                "Metric Plan-Execute selected: session_id=%s glossary_matches=%d metric_candidates=%d routing=%s",
                state.session_id,
                len(state.glossary_matches),
                len(state.metric_candidates),
                self._json_dumps(routing),
            )
            return State.METRIC_PLAN_EXECUTE
        logger.info("Single-query route selected: session_id=%s routing=%s", state.session_id, self._json_dumps(routing))
        return State.SCHEMA_PLAN

    async def _handle_metric_plan_execute(self, state: AgentState) -> State:
        """Bounded multi-evidence orchestration for glossary-grounded complex metric questions."""
        engine = get_engine(state.agent_id)
        query_engine = get_query_engine(state.agent_id)
        executor = ToolExecutor(state.agent_id, self.entity_agent)

        if not state.metric_plan:
            state.metric_plan_phase = "planning"
            metric_plan_started_at_ms = int(time.time() * 1000)
            logger.info(
                "Metric Plan-Execute planning started: session_id=%s glossary_terms=%s candidate_metrics=%s",
                state.session_id,
                self._json_dumps([item.get("standard_name") or item.get("term") for item in state.glossary_matches]),
                self._json_dumps(state.metric_candidates),
            )
            initial_plan = await self.plan_execute_agent.plan_metric_subquestions(
                state.user_message,
                state.glossary_matches,
                state.metric_context,
                [
                    metric
                    for metric_id in state.metric_candidates
                    if (metric := engine.get_metric_info(metric_id))
                ],
                state.analysis_plan,
                session_id=state.session_id,
            )
            accepted = self._accept_metric_subquestions(
                initial_plan.get("subquestions"),
                state,
                allowed_metric_ids=state.metric_candidates,
                allowed_bundles=state.analysis_plan.get("metric_bundles", []),
            )
            if not accepted:
                logger.info(
                    "Metric plan did not yield valid subquestions; falling back to single query: session_id=%s",
                    state.session_id,
                )
                state.execution_mode = "single_query"
                return State.SCHEMA_PLAN

            state.metric_plan = {
                "plan_id": f"metric-plan-{shortuuid.random()}",
                "objective": str(initial_plan.get("objective") or state.user_message),
                "coverage_requirements": self._string_list(initial_plan.get("coverage_requirements")),
                "analysis_plan": state.analysis_plan,
            }
            state.metric_subquestions.extend(accepted)
            logger.info(
                "Metric plan accepted: session_id=%s plan_id=%s subquestions=%s coverage=%s",
                state.session_id,
                state.metric_plan["plan_id"],
                self._json_dumps([item["intent"] for item in accepted]),
                self._json_dumps(state.metric_plan["coverage_requirements"]),
            )
            self._append_metric_plan_step(
                state,
                "metric_plan",
                "已基于企业术语与候选指标生成数据证据计划。",
                {
                    "plan": state.metric_plan,
                    "subquestions": accepted,
                },
                started_at_ms=metric_plan_started_at_ms,
            )
            return State.METRIC_PLAN_EXECUTE

        pending = next((item for item in state.metric_subquestions if item.get("status") == "pending"), None)
        if pending:
            if state.metric_query_attempts >= METRIC_PLAN_MAX_QUERY_ATTEMPTS:
                pending["status"] = "skipped"
                pending["error"] = "已达到 Plan-Execute 查询次数上限。"
                self._append_metric_plan_step(state, "subquestion_query_plan", "已达到查询次数上限，子问题未执行。", pending)
                logger.warning(
                    "Metric subquestion skipped by query budget: session_id=%s subquestion_id=%s attempts=%d limit=%d",
                    state.session_id,
                    pending.get("id"),
                    state.metric_query_attempts,
                    METRIC_PLAN_MAX_QUERY_ATTEMPTS,
                )
            else:
                state.metric_plan_phase = "executing"
                logger.info(
                    "Metric subquestion execution started: session_id=%s plan_id=%s iteration=%d subquestion_id=%s intent=%s",
                    state.session_id,
                    state.metric_plan.get("plan_id"),
                    state.metric_plan_iteration,
                    pending.get("id"),
                    pending.get("intent"),
                )
                await self._execute_metric_subquestion(state, pending, executor, query_engine, engine)
                if state.clarification:
                    return State.CLARIFY
            return State.METRIC_PLAN_EXECUTE

        state.metric_plan_phase = "judging"
        evidence_packet = self._build_metric_evidence_packet(state)
        can_expand = state.metric_plan_iteration < METRIC_PLAN_MAX_ITERATIONS and (
            state.metric_query_attempts < METRIC_PLAN_MAX_QUERY_ATTEMPTS
        )
        metric_plan_started_at_ms = int(time.time() * 1000)
        judgment = await self.plan_execute_agent.judge_metric_evidence(
            state.user_message,
            state.metric_plan,
            evidence_packet,
            state.metric_plan_iteration,
            can_expand,
            session_id=state.session_id,
        )
        decision = str(judgment.get("decision") or "limited").lower()
        if decision not in {"sufficient", "add", "limited"}:
            decision = "limited"
            judgment["limitation"] = "证据充分性判定返回了无效决策。"
        judgment["decision"] = decision
        state.metric_plan_judgments.append(judgment)
        logger.info(
            "Metric evidence judgment: session_id=%s plan_id=%s iteration=%d decision=%s can_expand=%s missing=%s",
            state.session_id,
            state.metric_plan.get("plan_id"),
            state.metric_plan_iteration,
            decision,
            can_expand,
            self._json_dumps(judgment.get("missing_evidence", [])),
        )
        self._append_metric_plan_step(state, "evidence_judgment", "已审核当前数据证据覆盖情况。", judgment)

        if decision == "add" and can_expand:
            additions = self._accept_metric_subquestions(
                judgment.get("additional_subquestions"),
                state,
                limit=2,
                allowed_metric_ids=state.metric_candidates,
                allowed_bundles=state.analysis_plan.get("metric_bundles", []),
            )
            if additions:
                state.metric_plan_iteration += 1
                for item in additions:
                    item["added_reason"] = self._string_list(judgment.get("missing_evidence"))
                    item["iteration"] = state.metric_plan_iteration
                state.metric_subquestions.extend(additions)
                logger.info(
                    "Metric plan expanded: session_id=%s plan_id=%s iteration=%d additions=%s",
                    state.session_id,
                    state.metric_plan.get("plan_id"),
                    state.metric_plan_iteration,
                    self._json_dumps([item["intent"] for item in additions]),
                )
                self._append_metric_plan_step(
                    state,
                    "metric_plan",
                    "当前证据不足，已追加补充数据子问题。",
                    {"iteration": state.metric_plan_iteration, "subquestions": additions, "gaps": judgment.get("missing_evidence", [])},
                    started_at_ms=metric_plan_started_at_ms,
                )
                return State.METRIC_PLAN_EXECUTE

        state.metric_plan_phase = "completed"
        state.metric_plan_terminal_reason = (
            "sufficient" if decision == "sufficient" else ("budget_exhausted" if not can_expand else "limited")
        )
        logger.info(
            "Metric Plan-Execute completed: session_id=%s plan_id=%s terminal_reason=%s subquestions=%d query_attempts=%d",
            state.session_id,
            state.metric_plan.get("plan_id"),
            state.metric_plan_terminal_reason,
            len(state.metric_subquestions),
            state.metric_query_attempts,
        )
        self._append_metric_plan_step(
            state,
            "metric_plan_complete",
            "指标证据计划已结束。",
            {
                "terminal_reason": state.metric_plan_terminal_reason,
                "coverage": judgment.get("coverage", []),
                "limitation": judgment.get("limitation", ""),
            },
        )
        state.final_reason = f"metric_plan_{state.metric_plan_terminal_reason}"
        state.assistant_content = "已完成多证据指标分析，正在基于已验证数据生成答复。"
        return State.FINAL_STREAM

    async def _handle_schema_plan(self, state: AgentState) -> State:
        """第一阶段：只识别主实体与显式关联实体，禁止执行查询。"""
        engine = get_engine(state.agent_id)
        if state.ontology_planning_started_at_ms is None:
            state.ontology_planning_started_at_ms = int(time.time() * 1000)
        feedback = str(state.scope_validation.get("error") or "")
        validation = await self.ontology_agent.plan_schema_scope(
            state.user_message,
            state.schema_context,
            state.glossary_matches,
            engine,
            candidate_class_ids=state.routing_candidate_class_ids,
            feedback=feedback,
            session_id=state.session_id,
        )
        state.scope_validation = validation
        if not validation["valid"]:
            attempts = state.planning_attempts.get("schema_scope", 0) + 1
            state.planning_attempts["schema_scope"] = attempts
            if attempts < 2:
                logger.warning(
                    "Schema scope plan rejected, replanning: session_id=%s attempt=%d error=%s",
                    state.session_id,
                    attempts,
                    validation["error"],
                )
                return State.SCHEMA_PLAN
            state.error = f"无法确定可执行的查询实体范围：{validation['error']}"
            return State.ERROR

        validation = self._align_scope_to_metric_candidates(
            validation, state.metric_candidates, engine
        )
        state.query_scope = {
            "target_class": validation["target_class"],
            "join_classes": validation["join_classes"],
            "join_paths": validation["join_paths"],
        }
        logger.info(
            "Schema scope planned: session_id=%s target_class=%s join_classes=%s",
            state.session_id,
            state.query_scope["target_class"],
            self._json_dumps(state.query_scope["join_classes"]),
        )
        return State.QUERY_PLAN

    async def _handle_query_plan(self, state: AgentState) -> State:
        """第二阶段：在已校验的 Schema Scope 内识别指标、维度和条件。"""
        engine = get_engine(state.agent_id)
        query_engine = get_query_engine(state.agent_id)
        feedback = str(state.plan_validation.get("error") or "")
        validation = await self.ontology_agent.plan_query_details(
            state.user_message,
            state.query_scope,
            state.metric_candidates,
            engine,
            query_engine,
            feedback=feedback,
            session_id=state.session_id,
        )
        state.plan_validation = validation
        if not validation["valid"]:
            attempts = state.planning_attempts.get("query_details", 0) + 1
            state.planning_attempts["query_details"] = attempts
            if attempts < 2:
                logger.warning(
                    "Query detail plan rejected, replanning: session_id=%s attempt=%d error=%s",
                    state.session_id,
                    attempts,
                    validation["error"],
                )
                return State.QUERY_PLAN
            state.error = f"无法生成可执行的查询参数：{validation['error']}"
            return State.ERROR

        state.query_scope = validation["query_scope"]
        state.query_plan = validation["query_plan"]
        if self._prepare_required_dimension_clarification(state, engine):
            return State.CLARIFY
        self._build_planned_query_args(state)
        self._emit_ontology_recognition_event(state, engine)
        logger.info(
            "Query plan validated: session_id=%s target_class=%s metrics=%s dimensions=%s filters=%d joins=%s",
            state.session_id,
            state.planned_query_args["target_class"],
            self._json_dumps(state.planned_query_args.get("metrics", [])),
            self._json_dumps(state.planned_query_args.get("dimensions", [])),
            len(state.planned_query_args.get("filters", [])),
            self._json_dumps(state.planned_query_args.get("join_classes", [])),
        )
        return State.TOOL_EXECUTE

    async def _execute_metric_subquestion(self, state: AgentState, subquestion: dict, executor, query_engine, engine) -> None:
        """Run one business subquestion through the existing retrieve -> validate -> execute boundary."""
        subquestion["status"] = "planning"
        intent = str(subquestion.get("intent") or "").strip()
        focused_question = f"原始问题：{state.user_message}\n\n本次需补充的业务证据：{intent}"
        schema_result = await self.schema_agent.retrieve(focused_question, engine)
        schema_context = str(schema_result.get("schema_context") or "")
        metric_candidates = [
            str(metric_id)
            for metric_id in schema_result.get("relevant_metrics", [])
            if isinstance(metric_id, str) and metric_id
        ]
        planned_metric_ids = self._string_list(subquestion.get("metric_ids"))
        preferred_metric_ids = []
        for metric_id in planned_metric_ids:
            metric_info = engine.get_metric_info(metric_id)
            if metric_info:
                preferred_metric_ids.append(metric_id)
                continue
            for candidate in engine.list_metrics():
                definition = candidate.get("definition") or {}
                if any(
                    isinstance(output, dict) and metric_id in {output.get("id"), output.get("output_name")}
                    for output in definition.get("outputs", [])
                ):
                    preferred_metric_ids.append(str(candidate.get("id") or metric_id))
                    break
        metric_candidates = list(
            dict.fromkeys([*preferred_metric_ids, *metric_candidates])
        )
        logger.info(
            "Metric subquestion retrieval completed: session_id=%s subquestion_id=%s relevant_classes=%s metric_candidates=%s",
            state.session_id,
            subquestion.get("id"),
            self._json_dumps(schema_result.get("relevant_classes", [])),
            self._json_dumps(metric_candidates),
        )

        reuse_decision = await self._decide_metric_subquestion_reuse(state, subquestion)
        reusable_plan = self._reusable_subquestion_by_id(
            state, reuse_decision.get("reuse_subquestion_id")
        ) if reuse_decision.get("reuse_scope_and_filters") else None

        scope_validation: dict = {}
        if reusable_plan:
            # A completed parent stores the normalized query scope, not the
            # transient validator envelope. Reconstruct that envelope before
            # the common validation branch below so reuse does not fail merely
            # because `query_scope` has no `valid` field.
            scope_validation = {
                **reusable_plan["query_scope"],
                "valid": True,
                "error": "",
            }
        else:
            for attempt in range(2):
                scope_validation = await self.ontology_agent.plan_schema_scope(
                    focused_question,
                    schema_context,
                    state.glossary_matches,
                    engine,
                    candidate_class_ids=state.routing_candidate_class_ids,
                    feedback=str(scope_validation.get("error") or ""),
                    session_id=state.session_id,
                )
                if scope_validation.get("valid"):
                    break
        if not scope_validation.get("valid"):
            subquestion.update(status="failed", error=str(scope_validation.get("error") or "无法确定 Schema 范围"))
            self._append_metric_plan_step(state, "subquestion_scope", "子问题 Schema 范围校验失败。", subquestion)
            return
        scope_validation = self._align_scope_to_metric_candidates(
            scope_validation, metric_candidates, engine
        )
        if reusable_plan and reuse_decision.get("reuse_metrics"):
            metric_candidates = list(dict.fromkeys([
                *self._string_list(reusable_plan["query_plan"].get("metrics")),
                *metric_candidates,
            ]))
        logger.info(
            "Metric subquestion scope validated: session_id=%s subquestion_id=%s target_class=%s join_classes=%s reused_from=%s",
            state.session_id,
            subquestion.get("id"),
            scope_validation["target_class"],
            self._json_dumps(scope_validation["join_classes"]),
            reusable_plan.get("subquestion_id") if reusable_plan else "",
        )
        self._append_metric_plan_step(
            state,
            "subquestion_scope",
            "子问题 Schema 范围已验证。",
            {
                "subquestion_id": subquestion.get("id"),
                "intent": intent,
                "query_scope": {
                    "target_class": scope_validation["target_class"],
                    "join_classes": scope_validation["join_classes"],
                },
            },
        )

        query_validation: dict = {}
        for attempt in range(2):
            query_validation = await self.ontology_agent.plan_query_details(
                focused_question,
                {
                    "target_class": scope_validation["target_class"],
                    "join_classes": scope_validation["join_classes"],
                    "join_paths": scope_validation["join_paths"],
                },
                metric_candidates,
                engine,
                query_engine,
                reusable_query_plan=reusable_plan.get("query_plan") if reusable_plan else None,
                reuse_metrics=bool(reusable_plan and reuse_decision.get("reuse_metrics")),
                trusted_reusable_filters=reusable_plan.get("locked_filters") if reusable_plan else None,
                feedback=str(query_validation.get("error") or ""),
                session_id=state.session_id,
            )
            if query_validation.get("valid"):
                break
        if not query_validation.get("valid"):
            subquestion.update(status="failed", error=str(query_validation.get("error") or "无法确定查询参数"))
            self._append_metric_plan_step(state, "subquestion_query_plan", "子问题查询参数校验失败。", subquestion)
            return
        state.query_scope = query_validation["query_scope"]
        state.query_plan = query_validation["query_plan"]
        if self._prepare_required_dimension_clarification(state, engine):
            subquestion.update(
                status="needs_clarification",
                query_scope=query_validation["query_scope"],
                query_plan=query_validation["query_plan"],
            )
            self._append_metric_plan_step(
                state,
                "subquestion_query_plan",
                "子问题缺少指标必要维度，等待用户澄清。",
                {
                    "subquestion_id": subquestion.get("id"),
                    "missing_dimensions": state.missing_required_dimensions,
                },
            )
            return
        logger.info(
            "Metric subquestion query plan validated: session_id=%s subquestion_id=%s metrics=%s dimensions=%s filters=%d",
            state.session_id,
            subquestion.get("id"),
            self._json_dumps(query_validation["query_plan"].get("metrics", [])),
            self._json_dumps(query_validation["query_plan"].get("dimensions", [])),
            len(query_validation["query_plan"].get("filters", [])),
        )
        self._append_metric_plan_step(
            state,
            "subquestion_query_plan",
            "子问题查询参数已验证。" if not reusable_plan else "子问题查询参数已验证，并复用了同实体参考参数。",
            {
                "subquestion_id": subquestion.get("id"),
                "intent": intent,
                "query_plan": query_validation["query_plan"],
                "reused_from_subquestion_id": reusable_plan.get("subquestion_id") if reusable_plan else "",
            },
        )

        arguments = self._with_query_context(
            "query_ontology_data",
            {
                "target_class": query_validation["query_scope"]["target_class"],
                "join_classes": query_validation["query_scope"]["join_classes"],
                **query_validation["query_plan"],
            },
            focused_question,
            state.glossary_matches,
        )
        if reusable_plan and reusable_plan.get("locked_filters"):
            arguments["_locked_shared_filters"] = reusable_plan["locked_filters"]
        fingerprint = self._metric_query_fingerprint(arguments)
        if any(item.get("query_fingerprint") == fingerprint for item in state.metric_subquestions if item is not subquestion):
            subquestion.update(status="skipped", error="受控查询参数与已有子问题重复。", query_fingerprint=fingerprint)
            self._append_metric_plan_step(state, "subquestion_query_plan", "子问题与已有查询重复，已跳过。", subquestion)
            return

        subquestion.update(
            status="executing",
            query_scope=query_validation["query_scope"],
            query_plan=query_validation["query_plan"],
            arguments=arguments,
            query_fingerprint=fingerprint,
            reused_query_plan_from=reusable_plan.get("subquestion_id") if reusable_plan else "",
            reuse_decision=reuse_decision,
        )
        state.metric_query_attempts += 1
        started_at = int(time.time() * 1000)
        result = self._make_json_safe(await executor.execute("query_ontology_data", arguments, query_engine, engine))
        finished_at = int(time.time() * 1000)
        result_error = result.get("error") if isinstance(result, dict) else "查询返回了无效结果"
        logger.info(
            "Metric subquestion query completed: session_id=%s subquestion_id=%s status=%s row_count=%s duration_ms=%d",
            state.session_id,
            subquestion.get("id"),
            "error" if result_error else "success",
            result.get("row_count") if isinstance(result, dict) else None,
            finished_at - started_at,
        )
        subquestion.update(
            status="failed" if result_error else "completed",
            error=str(result_error) if result_error else "",
            result=result,
            started_at=started_at,
            finished_at=finished_at,
        )
        recorded_arguments = {
            **arguments,
            "_metric_plan": {
                "plan_id": state.metric_plan.get("plan_id"),
                "subquestion_id": subquestion.get("id"),
                "iteration": subquestion.get("iteration", 0),
                "intent": intent,
            },
        }
        state.tool_call_records.append(
            ToolCallRecord(
                tool_name="query_ontology_data",
                arguments=recorded_arguments,
                result=result if not result_error else None,
                error=str(result_error) if result_error else None,
                retry_count=1 if result_error else 0,
            )
        )
        state.all_tool_results.append(
            {
                "name": "query_ontology_data",
                "description": f"子问题：{intent}",
                "arguments": recorded_arguments,
                "result": result,
                "started_at": started_at,
                "planning_finished_at": started_at,
                "planning_duration_ms": 0,
                "execution_started_at": started_at,
                "execution_duration_ms": finished_at - started_at,
                "finished_at": finished_at,
                "duration_ms": finished_at - started_at,
            }
        )
        state.sse_events.append(
            {
                "type": "tools",
                "tool_name": get_tool_display_name("query_ontology_data"),
                "description": f"子问题：{intent}",
                "begin_time": self._format_event_time(started_at),
                "payload": result,
                "duration": round((finished_at - started_at) / 1000, 3),
                "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
            }
        )

    @staticmethod
    def _string_list(value) -> list[str]:
        return [str(item).strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []

    def _accept_metric_subquestions(
        self,
        raw_subquestions,
        state: AgentState,
        limit: int = METRIC_PLAN_MAX_INITIAL_SUBQUESTIONS,
        allowed_metric_ids: list[str] | None = None,
        allowed_bundles: list[dict] | None = None,
    ) -> list[dict]:
        if not isinstance(raw_subquestions, list):
            return []
        accepted = []
        allowed_metrics = set(allowed_metric_ids or [])
        bundle_by_id = {
            str(bundle.get("id")): bundle
            for bundle in allowed_bundles or []
            if isinstance(bundle, dict) and bundle.get("id")
        }
        known_intents = {str(item.get("intent") or "").strip().lower() for item in state.metric_subquestions}
        for index, raw in enumerate(raw_subquestions):
            if len(accepted) >= limit or not isinstance(raw, dict):
                break
            intent = str(raw.get("intent") or "").strip()
            if not intent or len(intent) > METRIC_PLAN_MAX_SUBQUESTION_CHARS or intent.lower() in known_intents:
                continue
            if re.search(r"\b(select|insert|update|delete|from|join)\b|[;{}]", intent, re.IGNORECASE):
                continue
            subquestion_id = str(raw.get("id") or f"sq-{len(state.metric_subquestions) + len(accepted) + 1}").strip()
            metric_ids = [
                metric_id
                for metric_id in self._string_list(raw.get("metric_ids"))
                if not allowed_metrics or metric_id in allowed_metrics
            ]
            metric_bundle_ids = [
                bundle_id
                for bundle_id in self._string_list(raw.get("metric_bundle_ids"))
                if bundle_id in bundle_by_id
            ]
            for bundle_id in metric_bundle_ids:
                for metric_id in bundle_by_id[bundle_id].get("metric_ids", []):
                    if not allowed_metrics or metric_id in allowed_metrics:
                        metric_ids.append(metric_id)
            accepted.append(
                {
                    "id": subquestion_id,
                    "intent": intent,
                    "metric_ids": list(dict.fromkeys(metric_ids)),
                    "metric_bundle_ids": list(dict.fromkeys(metric_bundle_ids)),
                    "analysis_role": str(raw.get("analysis_role") or ""),
                    "expected_evidence": str(raw.get("expected_evidence") or ""),
                    "priority": int(raw.get("priority") or index + 1),
                    "iteration": state.metric_plan_iteration,
                    "status": "pending",
                }
            )
            known_intents.add(intent.lower())
        return accepted

    @staticmethod
    def _metric_query_fingerprint(arguments: dict) -> str:
        return json.dumps(
            {
                key: arguments.get(key)
                for key in ("target_class", "join_classes", "metrics", "dimensions", "filters", "having", "order_by")
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    async def _decide_metric_subquestion_reuse(
        self, state: AgentState, subquestion: dict
    ) -> dict:
        """Ask the LLM to select a semantic parent before planning a later subquestion."""
        candidates = self._reusable_subquestion_candidates(state, subquestion)
        if not candidates:
            return {}
        try:
            decision = await self.plan_execute_agent.decide_subquestion_reuse(
                state.user_message,
                str(subquestion.get("intent") or ""),
                candidates,
                session_id=state.session_id,
            )
        except Exception as exc:
            logger.warning(
                "Subquestion reuse decision failed; using independent plan: session_id=%s subquestion_id=%s error=%s",
                state.session_id, subquestion.get("id"), str(exc),
            )
            return {}
        valid_ids = {str(item["id"]) for item in candidates}
        if decision.get("reuse_subquestion_id") not in valid_ids:
            return {}
        return decision

    @staticmethod
    def _reusable_subquestion_candidates(state: AgentState, current_subquestion: dict) -> list[dict]:
        candidates = []
        for previous in state.metric_subquestions:
            if previous is current_subquestion or previous.get("status") != "completed":
                continue
            arguments = previous.get("arguments")
            query_scope = previous.get("query_scope")
            query_plan = previous.get("query_plan")
            if not isinstance(arguments, dict) or not isinstance(query_scope, dict) or not isinstance(query_plan, dict):
                continue
            candidates.append({
                "id": str(previous.get("id") or ""),
                "intent": str(previous.get("intent") or ""),
                "target_class": query_scope.get("target_class"),
                "join_classes": query_scope.get("join_classes") or [],
                "metrics": query_plan.get("metrics") or [],
                "executed_filters": arguments.get("filters") or [],
            })
        return candidates[-3:]

    @classmethod
    def _reusable_subquestion_by_id(cls, state: AgentState, subquestion_id: str) -> dict | None:
        for previous in state.metric_subquestions:
            if str(previous.get("id") or "") != str(subquestion_id or ""):
                continue
            if previous.get("status") != "completed":
                return None
            arguments = previous.get("arguments")
            query_scope = previous.get("query_scope")
            query_plan = previous.get("query_plan")
            if not isinstance(arguments, dict) or not isinstance(query_scope, dict) or not isinstance(query_plan, dict):
                return None
            locked_filters = cls._string_filter_dicts(arguments.get("filters"))
            return {
                "subquestion_id": str(previous.get("id") or ""),
                "query_scope": query_scope,
                # Query planning originally stores model-proposed filters. Replace
                # them with the executor-resolved values so the reusable base and
                # the trusted-filter exemption describe the same SQL semantics.
                "query_plan": {**query_plan, "filters": locked_filters},
                "locked_filters": locked_filters,
            }
        return None

    @staticmethod
    def _find_reusable_subquestion_plan(
        state: AgentState,
        current_subquestion: dict,
        query_scope: dict,
        metric_candidates: list[str],
    ) -> dict | None:
        """Find a compatible validated plan to seed a later subquestion.

        This is intentionally a planning hint rather than a final argument cache.
        The current subquestion still gets a new LLM plan, ontology validation and
        deterministic filter alignment, so a child condition such as ``T40`` can
        safely extend the parent query instead of inheriting it unchanged.
        """
        target_class = str(query_scope.get("target_class") or "")
        join_classes = sorted(str(value) for value in query_scope.get("join_classes") or [])
        current_metrics = set(metric_candidates) | set(
            ChatEngineV3._string_list(current_subquestion.get("metric_ids"))
        )
        if not target_class or not current_metrics:
            return None

        for previous in reversed(state.metric_subquestions):
            if previous is current_subquestion or previous.get("status") not in {
                "completed", "executing", "needs_clarification",
            }:
                continue
            previous_scope = previous.get("query_scope")
            previous_plan = previous.get("query_plan")
            if not isinstance(previous_scope, dict) or not isinstance(previous_plan, dict):
                continue
            if str(previous_scope.get("target_class") or "") != target_class:
                continue
            previous_joins = sorted(
                str(value) for value in previous_scope.get("join_classes") or []
            )
            if previous_joins != join_classes:
                continue
            previous_metrics = set(ChatEngineV3._string_list(previous.get("metric_ids")))
            previous_metrics.update(ChatEngineV3._string_list(previous_plan.get("metrics")))
            if current_metrics.isdisjoint(previous_metrics):
                continue
            return {
                "subquestion_id": str(previous.get("id") or ""),
                "query_plan": previous_plan,
                "locked_filters": ChatEngineV3._string_filter_dicts(
                    previous.get("arguments", {}).get("filters")
                    if isinstance(previous.get("arguments"), dict)
                    else previous_plan.get("filters")
                ),
            }
        return None

    @staticmethod
    def _string_filter_dicts(value) -> list[dict]:
        """Copy only complete parent filters for the child shared-parameter contract."""
        return [
            dict(item)
            for item in value or []
            if isinstance(item, dict) and item.get("field") and item.get("operator")
        ]

    def _build_metric_evidence_packet(self, state: AgentState) -> list[dict]:
        evidence = []
        for item in state.metric_subquestions:
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            evidence.append(
                {
                    "subquestion_id": item.get("id"),
                    "intent": item.get("intent"),
                    "metric_ids": item.get("metric_ids") or [],
                    "expected_evidence": item.get("expected_evidence"),
                    "status": item.get("status"),
                    "error": item.get("error") or "",
                    "query_scope": item.get("query_scope") or {},
                    "query_plan": item.get("query_plan") or {},
                    "result": self._compact_result_payload(result) if result else {},
                }
            )
        return evidence

    def _append_metric_plan_step(
        self,
        state: AgentState,
        name: str,
        description: str,
        result: dict,
        started_at_ms: int | None = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        arguments = {
            "plan_id": state.metric_plan.get("plan_id"),
            "iteration": state.metric_plan_iteration,
            "execution_mode": state.execution_mode,
        }
        record = {
            "name": name,
            "description": description,
            "arguments": arguments,
            "result": self._make_json_safe(result),
        }
        if started_at_ms is not None:
            duration_ms = max(0, now_ms - started_at_ms)
            record.update(
                {
                    "started_at": started_at_ms,
                    "planning_finished_at": now_ms,
                    "planning_duration_ms": duration_ms,
                    "execution_started_at": now_ms,
                    "execution_duration_ms": 0,
                    "finished_at": now_ms,
                    "duration_ms": duration_ms,
                }
            )
        state.all_tool_results.append(record)
        event = {
            "type": "tools",
            "tool_name": name,
            "description": description,
            "begin_time": self._format_event_time(started_at_ms or now_ms),
            "payload": result,
            "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
        }
        if started_at_ms is not None:
            event["duration"] = round(max(0, now_ms - started_at_ms) / 1000, 3)
        state.sse_events.append(event)

    async def _handle_llm_call(self, state: AgentState) -> State:
        """调用 LLM"""
        if state.current_round >= state.max_rounds:
            if state.all_tool_results:
                state.final_reason = "max_rounds"
                state.assistant_content = "工具规划已达到最大轮次，请基于已有查询和分析结果尽力回答。"
                return State.FINAL_STREAM
            state.assistant_content = "已达到最大工具调用轮次，请尝试简化问题。"
            return State.DONE

        state.current_round += 1
        logger.info(
            "Chat LLM call started: session_id=%s round=%d messages=%d tools=%d",
            state.session_id,
            state.current_round,
            len(state.messages),
            len(state.tools or []),
        )

        llm_started_at = int(time.time() * 1000)
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=state.messages,
                tools=state.tools,
                tool_choice="auto",
                temperature=0.5,
                max_tokens=2048,
            )
        except Exception as e:
            state.error = f"LLM 调用失败: {str(e)}"
            logger.exception(
                "Chat LLM call failed: session_id=%s round=%d error=%s",
                state.session_id,
                state.current_round,
                str(e),
            )
            return State.ERROR
        llm_finished_at = int(time.time() * 1000)
        planning_duration_ms = llm_finished_at - llm_started_at

        message = response.choices[0].message

        # 无工具调用 → 最终回答
        if not message.tool_calls:
            content = message.content or ""
            if state.all_tool_results:
                state.final_reason = "normal"
                state.assistant_content = content
                logger.info(
                    "Chat LLM ready for final answer rewrite: session_id=%s round=%d draft_len=%d tool_results=%d",
                    state.session_id,
                    state.current_round,
                    len(content),
                    len(state.all_tool_results),
                )
                return State.FINAL_STREAM
            state.assistant_content += content
            state.messages.append(cast(ChatCompletionMessageParam, {"role": "assistant", "content": content}))
            logger.info(
                "Chat LLM final answer: session_id=%s round=%d content_len=%d",
                state.session_id,
                state.current_round,
                len(content),
            )
            return State.DONE

        # 有工具调用 → 进入工具分发
        tool_calls = cast(list[ChatCompletionMessageToolCall], message.tool_calls)
        state.pending_tool_calls = tool_calls
        state.messages.append(cast(ChatCompletionMessageParam, message.model_dump()))
        planning_text = message.content or ""
        logger.info(
            "Chat LLM tool calls: session_id=%s round=%d tool_count=%d names=%s",
            state.session_id,
            state.current_round,
            len(tool_calls),
            ",".join(tc.function.name for tc in tool_calls),
        )

        for tc in tool_calls:
            try:
                _ = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Chat tool arguments are not valid JSON: session_id=%s tool=%s raw=%s",
                    state.session_id,
                    tc.function.name,
                    tc.function.arguments,
                )
            state.tool_timings[tc.id] = {
                "started_at": llm_started_at,
                "planning_finished_at": llm_finished_at,
                "planning_duration_ms": planning_duration_ms,
            }
            state.tool_reasoning_steps.append(
                {
                    "tool_call_id": tc.id,
                    "tool_name": tc.function.name,
                    "tool_display_name": get_tool_display_name(tc.function.name),
                    "planning_text": planning_text,
                    "arguments": self._parse_tool_arguments(tc.function.arguments),
                    "round": state.current_round,
                }
            )

        return State.TOOL_DISPATCH

    async def _handle_tool_dispatch(self, state: AgentState) -> State:
        """工具分发：路由到对应执行器"""
        # 直接进入执行（工具执行器内部处理分发）
        return State.TOOL_EXECUTE

    async def _handle_tool_execute(self, state: AgentState) -> State:
        """工具执行：含后置自动校正"""
        engine = get_engine(state.agent_id)
        query_engine = get_query_engine(state.agent_id)
        executor = ToolExecutor(state.agent_id, self.entity_agent)

        if state.planned_query_args and not state.query_executed:
            return await self._execute_validated_query_plan(state, executor, query_engine, engine)

        for tool_index, tc in enumerate(state.pending_tool_calls, start=1):
            tool_name = tc.function.name
            args = self._parse_tool_arguments(tc.function.arguments)
            args = self._with_query_context(tool_name, args, state.user_message, state.glossary_matches)

            logger.info(
                "========== TOOL CALL START | session_id=%s round=%d index=%d call_id=%s tool=%s ==========",
                state.session_id,
                state.current_round,
                tool_index,
                tc.id,
                tool_name,
            )

            if tool_name == "python_analyze" and "query_history" not in args:
                args = {
                    **args,
                    "query_history": [
                        item for item in state.all_tool_results if item.get("name") == "query_ontology_data"
                    ],
                }

            if tool_name == "python_analyze":
                direct_result = self._build_direct_answer_result(args)
                if direct_result:
                    now_ms = int(time.time() * 1000)
                    state.tool_call_records.append(
                        ToolCallRecord(
                            tool_name=tool_name,
                            arguments=args,
                            result=direct_result,
                        )
                    )
                    state.all_tool_results.append(
                        {
                            "name": tool_name,
                            "description": direct_result["reason"],
                            "arguments": args,
                            "result": direct_result,
                            "started_at": now_ms,
                            "planning_finished_at": now_ms,
                            "planning_duration_ms": 0,
                            "execution_started_at": now_ms,
                            "finished_at": now_ms,
                            "execution_duration_ms": 0,
                            "duration_ms": 0,
                            "skipped": True,
                        }
                    )
                    state.messages.append(
                        cast(
                            ChatCompletionMessageParam,
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": self._json_dumps(direct_result),
                            },
                        )
                    )
                    state.sse_events.append(
                        {
                            "type": "tools",
                            "tool_name": get_tool_display_name(tool_name),
                            "description": direct_result["reason"],
                            "begin_time": self._format_event_time(now_ms),
                            "payload": self._json_dumps(direct_result),
                            "duration": 0,
                            "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
                        }
                    )
                    logger.info(
                        "Python analyze skipped for direct answer: session_id=%s rows=%d json_chars=%d",
                        state.session_id,
                        direct_result["row_count"],
                        direct_result["data_json_chars"],
                    )
                    logger.info(
                        "========== TOOL CALL END | session_id=%s call_id=%s tool=%s status=skipped ==========",
                        state.session_id,
                        tc.id,
                        tool_name,
                    )
                    continue

            logger.info(
                "Chat tool execution started: session_id=%s tool=%s args=%s",
                state.session_id,
                tool_name,
                json.dumps(args, ensure_ascii=False, default=str)[:1000],
            )

            # 执行工具（含后置自动校正，死循环防线）
            timing = state.tool_timings.get(tc.id, {})
            total_started_at = timing.get("started_at") or int(time.time() * 1000)
            planning_finished_at = timing.get("planning_finished_at")
            planning_duration_ms = timing.get("planning_duration_ms")
            execution_started_at = int(time.time() * 1000)
            result = self._make_json_safe(await executor.execute(tool_name, args, query_engine, engine))
            tool_finished_at = int(time.time() * 1000)
            execution_duration_ms = tool_finished_at - execution_started_at
            total_duration_ms = tool_finished_at - total_started_at
            result_preview = self._json_dumps(result)
            if len(result_preview) > 3000:
                result_preview = result_preview[:3000] + "...[结果过长已截断]"

            if isinstance(result, dict) and result.get("error"):
                logger.warning(
                    "Chat tool execution returned error: session_id=%s tool=%s error=%s",
                    state.session_id,
                    tool_name,
                    result.get("error"),
                )
            else:
                logger.info(
                    "Chat tool execution completed: session_id=%s tool=%s result_len=%d",
                    state.session_id,
                    tool_name,
                    len(result_preview),
                )

            result_error = result.get("error") if isinstance(result, dict) else None
            logger.info(
                "========== TOOL CALL END | session_id=%s call_id=%s tool=%s status=%s duration_ms=%d ==========",
                state.session_id,
                tc.id,
                tool_name,
                "error" if result_error else "success",
                total_duration_ms,
            )

            # 记录工具调用
            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=args,
                result=result if not result_error else None,
                error=result_error,
                retry_count=1 if result_error else 0,
            )
            state.tool_call_records.append(record)
            state.all_tool_results.append(
                {
                    "name": tool_name,
                    "description": get_tool_purpose(tool_name),
                    "arguments": args,
                    "result": result,
                    "started_at": total_started_at,
                    "planning_finished_at": planning_finished_at,
                    "planning_duration_ms": planning_duration_ms,
                    "execution_started_at": execution_started_at,
                    "finished_at": tool_finished_at,
                    "execution_duration_ms": execution_duration_ms,
                    "duration_ms": total_duration_ms,
                }
            )

            # 发送工具结果事件
            state.sse_events.append(
                {
                    "type": "tools",
                    "tool_name": get_tool_display_name(tool_name),
                    "description": get_tool_purpose(tool_name),
                    "begin_time": self._format_event_time(total_started_at),
                    "payload": result if tool_name == "query_ontology_data" else (result if not result_error else None),
                    "duration": round(total_duration_ms / 1000, 3),
                    "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
                }
            )

            # 注入工具结果到消息
            tool_content = self._build_tool_message_content(state, tool_name, args, result, result_preview)
            state.messages.append(
                cast(
                    ChatCompletionMessageParam,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_content,
                    },
                )
            )

        state.pending_tool_calls = []
        if self._should_finalize_after_tool_execute(state):
            state.final_reason = "small_tool_result"
            state.assistant_content = "本轮查询结果行数较少，已足够支持直接回答用户问题。"
            logger.info(
                "Chat final answer shortcut after tool execution: session_id=%s tool_results=%d",
                state.session_id,
                len(state.all_tool_results),
            )
            return State.FINAL_STREAM
        return State.LLM_CALL

    async def _execute_validated_query_plan(self, state: AgentState, executor, query_engine, engine) -> State:
        """Execute only the deterministic plan approved by both planning stages."""
        arguments = dict(state.planned_query_args or {})
        state.planned_query_args = None
        state.query_executed = True
        started_at = int(time.time() * 1000)

        result = self._make_json_safe(await executor.execute("query_ontology_data", arguments, query_engine, engine))
        finished_at = int(time.time() * 1000)
        duration_ms = finished_at - started_at
        result_error = result.get("error") if isinstance(result, dict) else "查询返回了无效结果"
        result_preview = self._json_dumps(result)
        if len(result_preview) > 3000:
            result_preview = result_preview[:3000] + "...[结果过长已截断]"

        state.tool_call_records.append(
            ToolCallRecord(
                tool_name="query_ontology_data",
                arguments=arguments,
                result=result if not result_error else None,
                error=result_error,
                retry_count=1 if result_error else 0,
            )
        )
        state.all_tool_results.append(
            {
                "name": "query_ontology_data",
                "description": get_tool_purpose("query_ontology_data"),
                "arguments": arguments,
                "result": result,
                "started_at": started_at,
                "planning_finished_at": started_at,
                "planning_duration_ms": 0,
                "execution_started_at": started_at,
                "finished_at": finished_at,
                "execution_duration_ms": duration_ms,
                "duration_ms": duration_ms,
            }
        )
        state.sse_events.append(
            {
                "type": "tools",
                "tool_name": get_tool_display_name("query_ontology_data"),
                "description": get_tool_purpose("query_ontology_data"),
                "begin_time": self._format_event_time(started_at),
                "payload": result,
                "duration": round(duration_ms / 1000, 3),
                "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
            }
        )
        logger.info(
            "session_id=%s status=%s duration_ms=%d",
            state.session_id,
            "error" if result_error else "success",
            duration_ms,
        )
        if result_error:
            state.error = str(result_error)
            return State.ERROR
        if self._should_finalize_after_tool_execute(state):
            state.final_reason = "small_tool_result"
            state.assistant_content = "查询结果已完成，可直接生成答复。"
            return State.FINAL_STREAM

        query_result_context = self._build_tool_message_content(
            state,
            "query_ontology_data",
            arguments,
            result,
            result_preview,
        )
        state.messages = cast(
            list[ChatCompletionMessageParam],
            [
                {
                    "role": "system",
                    "content": (
                        "你是数据分析助手。已完成受控 SQL 查询，不能再次调用 query_ontology_data。"
                        "如数据较大且需要聚合、排序、对比或计算，只可调用 python_analyze；"
                        "否则直接基于查询结果回答。"
                    ),
                },
                {"role": "user", "content": state.user_message},
                {"role": "user", "content": f"已验证查询结果：{query_result_context}"},
            ],
        )
        state.tools = [tool for tool in state.tools if tool.get("function", {}).get("name") == "python_analyze"]
        return State.LLM_CALL

    async def _handle_clarify(self, state: AgentState) -> State:
        """Emit an explicit, governed clarification question and stop execution."""
        clarification = state.clarification
        if not clarification:
            state.error = "需要澄清的问题为空。"
            return State.ERROR
        checkpoint_id = self._create_clarification_checkpoint(state)
        clarification = {**clarification, "checkpoint_id": checkpoint_id}
        state.clarification = clarification
        state.assistant_content = str(clarification["question"])
        state.final_reason = "needs_clarification"
        state.sse_events.append(
            {
                "type": "clarification",
                "query_id": state.query_id,
                "data": clarification,
            }
        )
        logger.info(
            "Clarification requested: session_id=%s group=%s requirements=%s",
            state.session_id,
            clarification.get("field"),
            self._json_dumps(
                state.missing_dimension_groups or state.missing_required_dimensions
            ),
        )
        return State.DONE

    def _build_planned_query_args(self, state: AgentState) -> None:
        """Build the controlled query payload from an already validated query plan."""
        state.planned_query_args = self._with_query_context(
            "query_ontology_data",
            {
                "target_class": state.query_scope["target_class"],
                "join_classes": state.query_scope["join_classes"],
                **state.query_plan,
            },
            state.user_message,
            state.glossary_matches,
        )

    def _resume_after_clarification(self, state: AgentState) -> State:
        """Apply governed answers to a paused single-query plan without replanning it."""
        if state.execution_mode == "metric_plan_execute":
            paused = next(
                (item for item in state.metric_subquestions if item.get("status") == "needs_clarification"),
                None,
            )
            if not paused:
                state.error = "未找到等待澄清的指标子问题，请重新发起查询。"
                return State.ERROR
            # Keep the completed evidence ledger and retry only the paused subquestion.
            paused["status"] = "pending"
            state.clarification = None
            logger.info(
                "Metric clarification checkpoint resumed: session_id=%s query_id=%s subquestion_id=%s",
                state.session_id,
                state.query_id,
                paused.get("id"),
            )
            return State.METRIC_PLAN_EXECUTE
        state.clarification = None
        state.missing_dimension_groups = []
        state.missing_required_dimensions = []
        engine = get_engine(state.agent_id)
        if self._prepare_required_dimension_clarification(state, engine):
            return State.CLARIFY
        self._build_planned_query_args(state)
        self._emit_ontology_recognition_event(state, engine)
        logger.info(
            "Clarification checkpoint resumed at tool execution: session_id=%s query_id=%s",
            state.session_id,
            state.query_id,
        )
        return State.TOOL_EXECUTE

    @staticmethod
    def _checkpoint_state_fields() -> tuple[str, ...]:
        return (
            "user_message", "ontology_context", "glossary_matches", "skill_matches", "entity_hints",
            "schema_context", "metric_context", "metric_candidates", "query_scope", "query_plan",
            "scope_validation", "plan_validation", "planning_attempts", "dimension_resolution",
            "execution_mode", "metric_plan", "metric_plan_phase", "metric_plan_iteration",
            "metric_subquestions", "metric_plan_judgments", "metric_plan_terminal_reason",
            "metric_query_attempts", "all_tool_results", "tool_timings", "tool_reasoning_steps",
            "analysis_payload", "analysis_processed_count", "transition_log",
        )

    def _create_clarification_checkpoint(self, state: AgentState) -> str:
        """Store only the validated planning state required to continue a governed query."""
        checkpoint_id = f"clarify_{shortuuid.random()}"
        snapshot = {
            field: self._make_json_safe(getattr(state, field))
            for field in self._checkpoint_state_fields()
        }
        db = get_db()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS chat_clarification_checkpoints ("
                "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, agent_id TEXT NOT NULL, "
                "state_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
                "created_at TEXT DEFAULT CURRENT_TIMESTAMP, consumed_at TEXT DEFAULT '')"
            )
            db.execute(
                "INSERT INTO chat_clarification_checkpoints (id, session_id, agent_id, state_json, status) VALUES (?, ?, ?, ?, 'pending')",
                (checkpoint_id, state.session_id, state.agent_id, json.dumps(snapshot, ensure_ascii=False)),
            )
            db.commit()
            return checkpoint_id
        finally:
            db.close()

    def _claim_clarification_checkpoint(self, checkpoint_id: str, session_id: str, agent_id: str) -> dict | None:
        """Atomically consume a checkpoint so duplicate Continue clicks cannot execute it twice."""
        db = get_db()
        try:
            row = db.execute(
                "SELECT state_json FROM chat_clarification_checkpoints WHERE id = ? AND session_id = ? AND agent_id = ? AND status = 'pending'",
                (checkpoint_id, session_id, agent_id),
            ).fetchone()
            if not row:
                return None
            claimed = db.execute(
                "UPDATE chat_clarification_checkpoints SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
                (checkpoint_id,),
            ).rowcount
            db.commit()
            return json.loads(row["state_json"]) if claimed else None
        finally:
            db.close()

    def _restore_clarification_checkpoint(self, snapshot: dict, *, query_id: str, session_id: str, agent_id: str) -> AgentState:
        state = AgentState(agent_id=agent_id, session_id=session_id, query_id=query_id)
        for field in self._checkpoint_state_fields():
            if field in snapshot:
                setattr(state, field, snapshot[field])
        return state

    async def _handle_final_stream(self, state: AgentState) -> State:
        """最终流式输出"""
        final_started_at = time.time()
        final_prompt = self._build_final_answer_prompt(state)
        logger.info(
            "Chat final answer generation started: session_id=%s reason=%s prompt_len=%d tool_results=%d",
            state.session_id,
            state.final_reason,
            len(final_prompt),
            len(state.all_tool_results),
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": FINAL_ANSWER_PROMPT,
                    },
                    {"role": "user", "content": final_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )
        except Exception as e:
            state.final_answer_duration_ms = int((time.time() - final_started_at) * 1000)
            if state.assistant_content:
                logger.warning(
                    "Chat final answer generation failed, using draft: session_id=%s error=%s",
                    state.session_id,
                    str(e),
                )
                return State.DONE
            state.error = f"最终答复生成失败: {str(e)}"
            logger.exception(
                "Chat final answer generation failed: session_id=%s error=%s",
                state.session_id,
                str(e),
            )
            return State.ERROR

        content = response.choices[0].message.content or ""
        state.final_answer_duration_ms = int((time.time() - final_started_at) * 1000)
        state.assistant_content = content
        state.messages.append(cast(ChatCompletionMessageParam, {"role": "assistant", "content": content}))
        logger.info(
            "Chat final answer generated: session_id=%s content_len=%d",
            state.session_id,
            len(content),
        )
        return State.DONE

    async def _handle_done(self, state: AgentState) -> State:
        """完成"""
        state.sse_events.append(
            {
                "type": "done",
                "tool_results": state.all_tool_results,
            }
        )
        return State.DONE

    async def _handle_error(self, state: AgentState) -> State:
        """错误处理"""
        state.sse_events.append(
            {
                "type": "error",
                "content": state.error or "未知错误",
            }
        )
        return State.DONE

    # ============================================================
    # 辅助函数
    # ============================================================

    @staticmethod
    def _align_scope_to_metric_candidates(
        validation: dict, metric_candidates: list[str], engine
    ) -> dict:
        """Use a single governed Metric anchor when schema retrieval is unambiguous.

        A raw-field match can otherwise select a fact table with no governed
        Metrics. That makes the query planner reject the raw field before the
        required DimensionGroup resolver has an opportunity to ask the user.
        """
        candidate_anchors = []
        for metric_id in metric_candidates:
            metric = engine.get_metric_info(metric_id)
            if not metric:
                continue
            anchors = metric_target_classes(metric)
            if anchors:
                candidate_anchors.append(anchors[0])
        anchors = list(dict.fromkeys(anchor for anchor in candidate_anchors if anchor))
        target_class = str(validation.get("target_class") or "")
        if len(anchors) != 1 or anchors[0] == target_class:
            return validation
        logger.info(
            "Schema scope aligned to governed Metric anchor: selected=%s anchor=%s candidates=%s",
            target_class,
            anchors[0],
            json.dumps(metric_candidates, ensure_ascii=False),
        )
        return {
            **validation,
            "target_class": anchors[0],
            "join_classes": [],
            "join_paths": {},
        }

    def _prepare_required_dimension_clarification(self, state: AgentState, engine) -> bool:
        """Resolve DimensionGroups and retain legacy field checks during migration."""
        resolution = self.clarify_agent.resolve_dimension_groups(
            state.query_plan,
            state.user_message,
            engine,
            state.dimension_selections,
        )
        resolution["groups"] = list(engine.schema.get("dimension_groups", []))
        state.dimension_resolution = resolution
        state.query_plan = self.clarify_agent.apply_resolved_selections(
            state.query_plan, resolution
        )
        if resolution["unresolved_groups"]:
            state.missing_dimension_groups = resolution["unresolved_groups"]
            state.clarification_reason = "missing_dimension_groups"
            state.clarification = self.clarify_agent.build_dimension_group_question(
                state.missing_dimension_groups
            )
            return True

        missing = resolution["legacy_missing_dimensions"]
        if not missing:
            return False
        state.missing_required_dimensions = missing
        state.clarification_reason = "missing_required_dimensions"
        state.clarification = self.clarify_agent.build_required_dimension_question(
            missing
        )
        return True

    def _emit_execution_mode_routing_event(self, state: AgentState, routing: dict) -> None:
        """Publish exactly one execution-mode decision event for every request."""
        now_ms = int(time.time() * 1000)
        started_at = state.execution_mode_started_at_ms or now_ms
        duration_ms = max(0, now_ms - started_at)
        is_plan_execute = state.execution_mode == "metric_plan_execute"
        description = (
            "已识别为多任务，进入 Plan-Execute 证据拆解。"
            if is_plan_execute
            else "已识别为单任务，使用单条受控查询执行。"
        )
        payload = {
            "stage": "execution_mode_routing",
            "status": "completed",
            "execution_mode": state.execution_mode,
            "is_multi_task": is_plan_execute,
            "decision_source": routing.get("decision_source") or "unknown",
            "matched_rule": routing.get("matched_rule") or "",
            "reason": routing.get("reason") or "",
            "required_evidence": routing.get("required_evidence") or [],
            "confidence": routing.get("confidence") or "low",
        }
        state.all_tool_results.append(
            {
                "name": "execution_mode_routing",
                "description": description,
                "arguments": {"user_question": state.user_message},
                "result": payload,
                "started_at": started_at,
                "planning_finished_at": now_ms,
                "planning_duration_ms": duration_ms,
                "execution_started_at": now_ms,
                "execution_duration_ms": 0,
                "finished_at": now_ms,
                "duration_ms": duration_ms,
            }
        )
        state.sse_events.append(
            {
                "type": "tools",
                "tool_name": "任务路由",
                "description": description,
                "begin_time": self._format_event_time(started_at),
                "payload": payload,
                "duration": round(duration_ms / 1000, 3),
                "status": "completed",
                "stage": "execution_mode_routing",
                "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
            }
        )

    def _emit_ontology_recognition_event(self, state: AgentState, engine) -> None:
        """Expose the completed ontology plan as a frontend tool-progress event."""
        finished_at = int(time.time() * 1000)
        started_at = state.ontology_planning_started_at_ms or finished_at
        duration_seconds = round(max(0, finished_at - started_at) / 1000, 3)
        class_by_id = {str(item.get("id") or ""): item for item in engine.list_classes()}
        metric_by_id = {str(item.get("id") or ""): item for item in engine.list_metrics()}

        def schema_summary(class_id: str) -> dict:
            schema = class_by_id.get(class_id, {})
            return {
                "id": class_id,
                "name": schema.get("name_cn") or schema.get("name") or class_id,
            }

        def metric_summary(metric_id: str) -> dict:
            metric = metric_by_id.get(metric_id, {})
            return {
                "id": metric_id,
                "name": metric.get("name") or metric.get("name_cn") or metric_id,
            }

        target_class = str(state.query_scope.get("target_class") or "")
        join_classes = [str(class_id) for class_id in state.query_scope.get("join_classes") or [] if class_id]
        candidate_metrics = [
            metric_summary(metric_id) for metric_id in state.metric_candidates if metric_id in metric_by_id
        ]
        selected_metrics = [str(metric) for metric in state.query_plan.get("metrics") or [] if metric]
        dimensions = [str(dimension) for dimension in state.query_plan.get("dimensions") or [] if dimension]
        filters = list(state.query_plan.get("filters") or [])
        having = list(state.query_plan.get("having") or [])
        target_schema = schema_summary(target_class)
        join_schemas = [schema_summary(class_id) for class_id in join_classes]

        recognized_schemas = [target_schema, *join_schemas]
        schema_text = "、".join(item["name"] for item in recognized_schemas if item["id"]) or "未识别"
        candidate_metric_text = "、".join(item["name"] for item in candidate_metrics) or "无"
        selected_metric_text = "、".join(selected_metrics) or "无"
        dimension_text = "、".join(dimensions) or "无"
        payload = {
            "stage": "ontology_recognition",
            "status": "completed",
            "target_schema": target_schema,
            "join_schemas": join_schemas,
            "candidate_metrics": candidate_metrics,
            "selected_metrics": selected_metrics,
            "dimensions": dimensions,
            "filters": filters,
            "having": having,
            "order_by": state.query_plan.get("order_by") or "",
            "duration_ms": finished_at - started_at,
        }
        recognition_description = (
            f"已识别 Schema：{schema_text}；候选指标：{candidate_metric_text}；"
            f"查询指标：{selected_metric_text}；维度：{dimension_text}；筛选条件：{len(filters)} 个。"
        )
        state.all_tool_results.append(
            {
                "name": "ontology_recognition",
                "description": recognition_description,
                "arguments": {
                    "user_question": state.user_message,
                    "target_class": target_class,
                    "join_classes": join_classes,
                    "metrics": selected_metrics,
                    "dimensions": dimensions,
                    "filters": filters,
                    "having": having,
                    "order_by": state.query_plan.get("order_by") or "",
                },
                "result": payload,
                "started_at": started_at,
                "planning_finished_at": finished_at,
                "planning_duration_ms": finished_at - started_at,
                "execution_started_at": finished_at,
                "execution_duration_ms": 0,
                "finished_at": finished_at,
                "duration_ms": finished_at - started_at,
            }
        )
        state.sse_events.append(
            {
                "type": "tools",
                "tool_name": "本体语义识别",
                "description": recognition_description,
                "begin_time": self._format_event_time(started_at),
                "payload": payload,
                "duration": duration_seconds,
                "status": "completed",
                "stage": "ontology_recognition",
                "step": self._build_persisted_tool_steps(state.all_tool_results)[-1],
            }
        )

    async def _format_sse_event(self, query_id: str, event: dict) -> str:
        event_payload = dict(event)
        event_payload.setdefault("query_id", query_id)
        payload = self._json_dumps(event_payload)
        return f"data: {payload}\n\n"


    async def _emit_analysis_event(self, state: AgentState):
        pending_steps = state.tool_reasoning_steps[state.analysis_processed_count :]
        if not pending_steps:
            return

        payload = await self.analysis_tool.organize(state.user_message, pending_steps)
        if not payload:
            state.analysis_processed_count = len(state.tool_reasoning_steps)
            return

        state.analysis_payload.extend(payload)
        state.analysis_processed_count = len(state.tool_reasoning_steps)
        state.sse_events.append(
            {
                "type": "analysis",
                "payload": payload,
            }
        )

    @staticmethod
    def _parse_tool_arguments(arguments: str) -> dict:
        if not arguments:
            return {}
        parsed: object = arguments
        for _ in range(2):
            if not isinstance(parsed, str):
                break
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _with_user_question(tool_name: str, args: dict, user_message: str) -> dict:
        if tool_name != "query_ontology_data" or not user_message.strip():
            return args
        if str(args.get("user_question") or "").strip():
            return args
        return {**args, "user_question": user_message}

    @classmethod
    def _with_query_context(
        cls,
        tool_name: str,
        args: dict,
        user_message: str,
        glossary_matches: list[dict],
    ) -> dict:
        args = cls._with_user_question(tool_name, args, user_message)
        if tool_name != "query_ontology_data" or not glossary_matches or args.get("glossary_matches"):
            return args
        return {**args, "glossary_matches": glossary_matches}

    @classmethod
    def _build_direct_answer_result(cls, args: dict) -> dict | None:
        query_history = args.get("query_history")
        if not isinstance(query_history, list) or not query_history:
            return None

        last_query = query_history[-1]
        if not isinstance(last_query, dict):
            return None
        raw_query_args = last_query.get("arguments")
        query_args = raw_query_args if isinstance(raw_query_args, dict) else {}
        query_result = last_query.get("result") if isinstance(last_query.get("result"), dict) else None
        if not query_result or query_result.get("error"):
            return None

        rows = query_result.get("rows")
        if not isinstance(rows, list):
            return None
        row_count = query_result.get("row_count")
        if not isinstance(row_count, int):
            row_count = len(rows)

        metrics = query_args.get("metrics") or []
        dimensions = query_args.get("dimensions") or []
        is_aggregated = bool(metrics)
        if not is_aggregated or row_count > DIRECT_ANSWER_MAX_ROWS:
            return None

        data_json = cls._json_dumps(rows)
        if len(data_json) > DIRECT_ANSWER_MAX_JSON_CHARS:
            return None

        code = str(args.get("code") or "").lower()
        if any(pattern in code for pattern in PYTHON_ANALYZE_COMPLEX_PATTERNS):
            return None

        source_metadata = cls._source_metadata_for_prompt(query_result)
        return {
            "type": "direct_answer_recommended",
            "reason": "查询结果已是小规模聚合数据，跳过 Python 分析，交由大模型直接基于结果回答。",
            "row_count": row_count,
            "data_json_chars": len(data_json),
            "columns": query_result.get("columns", []),
            "data_sources": source_metadata["data_sources"],
            "table_descriptions": source_metadata["table_descriptions"],
            "dimensions": dimensions,
            "metrics": metrics,
            "rows": rows,
            "instruction": (
                "这些数据已经是聚合后的完整查询结果，且数据量较小。"
                "请不要再次要求调用 python_analyze，直接基于 rows、dimensions、metrics、data_sources "
                "和 table_descriptions 回答用户问题；"
                "如需做简单排序、最大/最小值、占比或文字归纳，可在回答中直接完成。"
            ),
        }

    @classmethod
    def _should_finalize_after_tool_execute(cls, state: AgentState) -> bool:
        if not state.all_tool_results:
            return False

        last_result = state.all_tool_results[-1]
        tool_name = last_result.get("name")
        result = last_result.get("result")
        if not isinstance(result, dict) or result.get("error"):
            return False

        if tool_name == "python_analyze":
            return True

        if tool_name != "query_ontology_data":
            return False

        rows = result.get("rows")
        row_count = result.get("row_count")
        if not isinstance(row_count, int):
            row_count = len(rows) if isinstance(rows, list) else None
        if row_count is None or row_count >= FINAL_AFTER_TOOL_MAX_ROWS:
            return False
        if not (isinstance(rows, list) or row_count == 0):
            return False
        return cls._query_result_satisfies_user_need(state, last_result, result, row_count)

    @classmethod
    def _query_result_satisfies_user_need(
        cls,
        state: AgentState,
        tool_result: dict,
        query_result: dict,
        row_count: int,
    ) -> bool:
        question = state.user_message.lower()
        evidence_text = cls._query_evidence_text(tool_result, query_result).lower()

        if any(keyword in question for keyword in CAUSE_QUERY_KEYWORDS):
            logger.info(
                "Small query result does not finalize because question asks for causal explanation: session_id=%s",
                state.session_id,
            )
            return False

        asks_comparison = any(keyword in question for keyword in COMPARISON_QUERY_KEYWORDS)
        if asks_comparison and not cls._has_comparison_evidence(evidence_text, query_result, row_count):
            logger.info(
                "Small query result does not finalize because comparison evidence is insufficient: session_id=%s",
                state.session_id,
            )
            return False

        if any(keyword in question for keyword in RATIO_QUERY_KEYWORDS) and not any(
            keyword in evidence_text for keyword in RATIO_EVIDENCE_KEYWORDS
        ):
            logger.info(
                "Small query result does not finalize because ratio evidence is insufficient: session_id=%s",
                state.session_id,
            )
            return False

        return True

    @classmethod
    def _has_comparison_evidence(cls, evidence_text: str, query_result: dict, row_count: int) -> bool:
        if any(keyword in evidence_text for keyword in COMPARISON_EVIDENCE_KEYWORDS):
            return True
        if row_count < 2:
            return False

        rows = query_result.get("rows")
        if not isinstance(rows, list):
            return False
        return any(keyword in evidence_text for keyword in TIME_DIMENSION_KEYWORDS)

    @classmethod
    def _query_evidence_text(cls, tool_result: dict, query_result: dict) -> str:
        source_metadata = cls._source_metadata_for_prompt(query_result)
        compact = {
            "arguments": tool_result.get("arguments", {}),
            "columns": query_result.get("columns", []),
            "dimensions": query_result.get("dimensions", []),
            "metrics": query_result.get("metrics", []),
            "data_sources": source_metadata["data_sources"],
            "table_descriptions": source_metadata["table_descriptions"],
        }
        rows = query_result.get("rows")
        if isinstance(rows, list) and rows:
            compact["sample_rows"] = rows[: min(len(rows), 3)]
        return cls._json_dumps(compact)

    @classmethod
    def _build_tool_message_content(
        cls,
        state: AgentState,
        tool_name: str,
        arguments: dict,
        result: dict,
        result_preview: str,
    ) -> str:
        guidance = cls._build_post_tool_guidance(state, tool_name, arguments, result)
        if not guidance:
            return result_preview
        return cls._json_dumps(
            {
                "result": result_preview,
                "next_step_guidance": guidance,
            }
        )

    @classmethod
    def _build_post_tool_guidance(
        cls,
        state: AgentState,
        tool_name: str,
        arguments: dict,
        result: dict,
    ) -> str:
        if tool_name != "query_ontology_data" or not isinstance(result, dict) or result.get("error"):
            return ""

        rows = result.get("rows")
        row_count = result.get("row_count")
        if not isinstance(row_count, int):
            row_count = len(rows) if isinstance(rows, list) else 0

        question = state.user_message.lower()
        is_comparison = any(keyword in question for keyword in COMPARISON_QUERY_KEYWORDS)
        is_ratio = any(keyword in question for keyword in RATIO_QUERY_KEYWORDS)
        is_large = row_count >= FINAL_AFTER_TOOL_MAX_ROWS

        if is_large and is_comparison:
            return (
                "本次 query_ontology_data 已返回完整但较大的比较类数据。下一步应调用 python_analyze，"
                "基于 df/df_1/df_2 计算当前值、对比期值、差值、变化方向和变化率；不要再次查询或截断数据。"
            )
        if is_large:
            return (
                "本次 query_ontology_data 已返回完整但较大的数据。下一步应调用 python_analyze，"
                "按用户问题对 df/df_1/df_2 做聚合、筛选、排序、Top、占比或必要计算；不要再次查询或截断数据。"
            )
        if is_ratio:
            return (
                "用户问题涉及占比/比例/贡献率。如果当前结果未直接包含占比字段，下一步应调用 python_analyze "
                "基于 df 计算总量和占比后再回答。"
            )
        comparison_evidence_text = cls._query_evidence_text({"arguments": arguments}, result).lower()
        if is_comparison and not cls._has_comparison_evidence(comparison_evidence_text, result, row_count):
            return (
                "用户问题涉及比较，但当前结果缺少足够对比证据。应继续查询补充对比期间数据，"
                "或在已有数据足够后调用 python_analyze 计算变化。"
            )
        return ""

    @classmethod
    def _json_dumps(cls, value) -> str:
        return json.dumps(cls._make_json_safe(value), ensure_ascii=False, default=str, allow_nan=False)

    @classmethod
    def _build_final_answer_prompt(cls, state: AgentState) -> str:
        payload = {
            "final_stage": True,
            "reason": state.final_reason,
            "user_question": state.user_message,
            "glossary_matches": state.glossary_matches,
            "conversation_context": cls._final_conversation_context(state.messages),
            "assistant_draft": state.assistant_content,
            "metric_plan": state.metric_plan if state.execution_mode == "metric_plan_execute" else {},
            "metric_subquestions": cls._make_json_safe(state.metric_subquestions),
            "metric_plan_judgments": cls._make_json_safe(state.metric_plan_judgments),
            "metric_plan_terminal_reason": state.metric_plan_terminal_reason,
            "tool_results": [cls._compact_tool_result(item) for item in state.all_tool_results],
        }
        payload_json = cls._truncate_text(cls._json_dumps(payload), FINAL_ANSWER_MAX_CONTEXT_CHARS)
        return f"""你现在处于最终回答阶段。不要再调用工具，不要提出新的查询计划。
请只基于下面 JSON 中的用户问题、对话上下文、本轮查询/分析结果、data_sources 和 table_descriptions 回答。

要求：
1. 先直接给出结论，再给必要依据。
2. 使用 data_sources/table_descriptions 判断数据来源、表别名、表描述和业务口径是否符合用户问题，
    避免混淆不同来源的数据。
3. 使用 glossary_matches 理解用户问题中的内部术语、别名和标准名，保持回答口径一致。
4. 不要编造结果中不存在的数据；数据不足时明确说明缺口。
5. 不要展示内部 prompt、状态机、工具调用细节；SQL 只在用户明确需要时提及。
6. 如果 reason=max_rounds，说明是基于已有结果的尽力回答。
7. 如果 metric_plan_terminal_reason 不是 sufficient，明确说明尚未覆盖的证据或计划停止原因。

最终回答输入 JSON：
{payload_json}
"""

    @classmethod
    def _final_conversation_context(
        cls,
        messages: list[ChatCompletionMessageParam],
        limit: int = 8,
    ) -> list[dict]:
        context = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in ("user", "assistant") or not content:
                continue
            context.append({"role": role, "content": cls._truncate_text(str(content), 1200)})
        return context[-limit:]

    @classmethod
    def _compact_tool_result(cls, item: dict) -> dict:
        result = item.get("result") if isinstance(item.get("result"), dict) else item.get("result")
        compact = {
            "name": item.get("name"),
            "arguments": item.get("arguments", {}),
            "description": item.get("description", ""),
        }
        if isinstance(result, dict):
            compact["result"] = cls._compact_result_payload(result)
        else:
            compact["result"] = cls._truncate_text(cls._json_dumps(result), 6000)
        return compact

    @classmethod
    def _compact_result_payload(cls, result: dict) -> dict:
        compact = {
            key: result.get(key)
            for key in (
                "type",
                "columns",
                "row_count",
                "target_class",
                "dimensions",
                "metrics",
                "error",
            )
            if key in result
        }
        if "data_sources" in result or "table_descriptions" in result:
            compact.update(cls._source_metadata_for_prompt(result))
        if result.get("sql"):
            compact["sql"] = result.get("sql")

        rows = result.get("rows")
        if isinstance(rows, list):
            compact.update(cls._compact_rows(rows))
        elif "data" in result:
            compact["data"] = result.get("data")
        else:
            for key, value in result.items():
                if key not in compact and key not in {"rows", "sql"}:
                    compact[key] = value
        return compact

    @classmethod
    def _source_metadata_for_prompt(cls, result: dict) -> dict:
        table_descriptions = result.get("table_descriptions")
        if not isinstance(table_descriptions, list):
            table_descriptions = []
        data_sources = result.get("data_sources")
        if not isinstance(data_sources, list):
            data_sources = []
        return {
            "data_sources": cls._compact_data_sources(
                data_sources,
                include_description=not table_descriptions,
            ),
            "table_descriptions": table_descriptions,
        }

    @staticmethod
    def _compact_data_sources(data_sources: list, include_description: bool = False) -> list[dict]:
        keys = ["class_id", "name_cn", "table_name", "table_alias", "table_name", "data_source"]
        if include_description:
            keys.append("description")

        compact_sources = []
        for source in data_sources:
            if not isinstance(source, dict):
                continue
            compact_sources.append({key: source.get(key) for key in keys if source.get(key) not in (None, "")})
        return compact_sources

    @staticmethod
    def _compact_rows(rows: list) -> dict:
        if len(rows) <= FINAL_ANSWER_SAMPLE_HEAD_ROWS:
            return {"rows": rows}
        return {
            "rows_sample": rows[:FINAL_ANSWER_SAMPLE_HEAD_ROWS],
            "rows_tail_sample": rows[-FINAL_ANSWER_SAMPLE_TAIL_ROWS:],
            "rows_truncated": True,
            "rows_omitted_count": max(
                0,
                len(rows) - FINAL_ANSWER_SAMPLE_HEAD_ROWS - FINAL_ANSWER_SAMPLE_TAIL_ROWS,
            ),
        }

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "...[truncated]"

    @staticmethod
    def _format_event_time(timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def _build_supporting_datasets(cls, state: AgentState) -> list[dict]:
        datasets = []
        for index, item in enumerate(state.all_tool_results):
            if item.get("name") != "query_ontology_data":
                continue
            result = item.get("result")
            if not isinstance(result, dict) or result.get("error"):
                continue

            rows = result.get("rows")
            if not isinstance(rows, list):
                continue

            raw_arguments = item.get("arguments")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            dataset = {
                "dataset_index": len(datasets),
                "tool_result_index": index,
                "source_tool": "query_ontology_data",
                "arguments": arguments,
                "target_class": result.get("target_class") or arguments.get("target_class"),
                "columns": result.get("columns", []),
                "rows": rows,
                "row_count": result.get("row_count", len(rows)),
                "sql": result.get("sql", ""),
                "data_sources": result.get("data_sources", []),
                "table_descriptions": result.get("table_descriptions", []),
            }
            for key in ("data_scope", "is_full_result", "handled_by"):
                if key in result:
                    dataset[key] = result.get(key)
            datasets.append(cls._make_json_safe(dataset))
        return datasets

    @classmethod
    def _build_final_answer(
        cls,
        content: str,
        supporting_datasets: list[dict] | None = None,
        duration_ms: int | None = None,
    ) -> dict:
        content = cls._sanitize_final_answer_content(content)
        return {
            "format": "markdown",
            "plain_text_summary": cls._build_plain_text_summary(content),
            "sanitized": True,
            "final_answer": content,
            "supporting_datasets": supporting_datasets or [],
            "duration_ms": duration_ms,
        }

    @classmethod
    def _sanitize_final_answer_content(cls, content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return ""

        fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(?P<body>[\s\S]*?)\n```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group("body").strip()

        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(payload, dict):
                answer = payload.get("final_answer") or payload.get("content") or payload.get("answer")
                if isinstance(answer, str):
                    return cls._sanitize_final_answer_content(answer)
        return text

    @staticmethod
    def _build_plain_text_summary(content: str, max_length: int = 80) -> str:
        plain_text = " ".join(line.strip("#*`>- ") for line in content.splitlines() if line.strip())
        if len(plain_text) <= max_length:
            return plain_text
        return plain_text[:max_length].rstrip() + "..."

    @classmethod
    def _make_json_safe(cls, value):
        try:
            import numpy as np
            import pandas as pd
        except Exception:
            pd = None
            np = None

        if pd is not None:
            if isinstance(value, pd.DataFrame):
                return cls._make_json_safe(value.head(100).to_dict("records"))
            if isinstance(value, pd.Series):
                return {
                    "name": value.name,
                    "index": [str(item) for item in value.head(100).index.tolist()],
                    "values": [cls._make_json_safe(item) for item in value.head(100).tolist()],
                    "total": int(len(value)),
                }
        if np is not None:
            if isinstance(value, np.generic):
                return cls._make_json_safe(value.item())
            if isinstance(value, np.ndarray):
                return cls._make_json_safe(value.tolist())

        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return cls._normalize_float(value)
        if pd is not None and (value is pd.NA or value is pd.NaT):
            return None

        if isinstance(value, dict):
            return {str(k): cls._make_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._make_json_safe(item) for item in value]
        return value

    @staticmethod
    def _normalize_float(value: float):
        if not math.isfinite(value):
            return None
        nearest_integer = round(value)
        if math.isclose(value, nearest_integer, rel_tol=0.0, abs_tol=1e-9):
            return int(nearest_integer)
        return value

    async def _load_conversation_history(
        self,
        session_id: str,
        limit: int = 20,
        exclude_query_id: str | None = None,
    ) -> list[dict[str, str]]:
        if not session_id:
            return []

        conn = None
        try:
            conn = get_db()
            rows = conn.execute(
                """SELECT role, content
                   FROM messages
                   WHERE conversation_id=? AND role IN ('user', 'assistant')
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        except Exception as exc:
            logger.warning(
                "Chat history load from messages failed: session_id=%s error=%s",
                session_id,
                str(exc),
            )
            return []
        finally:
            if conn is not None:
                conn.close()

        history = []
        for message in reversed(rows):
            content = self._history_content_for_llm(message["role"], message["content"])
            if content:
                history.append({"role": message["role"], "content": content})
        return history

    @staticmethod
    def _build_persisted_answer_datasets(supporting_datasets: list[dict]) -> list[dict]:
        """Convert internal query results into the JSON shape consumed by `normalizeAnswerDatasets`."""
        datasets = []
        for index, dataset in enumerate(supporting_datasets):
            if not isinstance(dataset, dict):
                continue
            rows = dataset.get("rows")
            columns = dataset.get("columns")
            if not isinstance(rows, list) or not isinstance(columns, list):
                continue
            target_class = str(dataset.get("target_class") or "query_result")
            data = {
                "type": "query_result",
                "class_id": target_class,
                "class_name": target_class,
                "target_class": target_class,
                "columns": columns,
                "rows": rows,
                "total": dataset.get("row_count", len(rows)),
                "row_count": dataset.get("row_count", len(rows)),
                "sql": dataset.get("sql") or "",
            }
            datasets.append(
                {
                    "id": f"query_{index + 1}",
                    "name": target_class,
                    "arguments": dataset.get("arguments") or {},
                    "data": data,
                }
            )
        return datasets

    @classmethod
    def _build_persisted_visualization(cls, supporting_datasets: list[dict]) -> dict | None:
        """Keep a legacy visualization payload for clients without answer-dataset support."""
        datasets = cls._build_persisted_answer_datasets(supporting_datasets)
        return datasets[0]["data"] if datasets else None

    @classmethod
    def _build_persisted_tool_steps(cls, tool_results: list[dict]) -> list[dict]:
        """Normalize internal execution records to the `ToolStep` shape used on conversation reload."""
        steps = []
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            result = cls._make_json_safe(item.get("result"))
            steps.append(
                {
                    "name": item.get("name") or "工具调用",
                    "description": item.get("description") or "",
                    "args": cls._make_json_safe(item.get("arguments") or {}),
                    "result": result,
                    "status": "failed" if isinstance(result, dict) and result.get("error") else "completed",
                    "startedAt": item.get("started_at"),
                    "planningFinishedAt": item.get("planning_finished_at"),
                    "planningDurationMs": item.get("planning_duration_ms"),
                    "executionStartedAt": item.get("execution_started_at"),
                    "executionDurationMs": item.get("execution_duration_ms"),
                    "finishedAt": item.get("finished_at"),
                    "durationMs": item.get("duration_ms"),
                }
            )
        return steps

    @staticmethod
    def _persist_conversation_message(
        session_id: str,
        role: str,
        content: str,
        answer_datasets: list[dict] | None = None,
        visualization: dict | None = None,
        steps: list[dict] | None = None,
        action_confirm: dict | None = None,
    ) -> None:
        """Persist messages in the application's existing conversation store."""
        if not session_id or (not content and role != "assistant"):
            return

        conn = None
        try:
            conn = get_db()
            payload = (
                content,
                json.dumps(visualization, ensure_ascii=False, default=str) if visualization else "",
                json.dumps(answer_datasets or [], ensure_ascii=False, default=str),
                json.dumps(steps or [], ensure_ascii=False, default=str),
                json.dumps(action_confirm, ensure_ascii=False, default=str) if action_confirm else "",
            )
            conn.execute(
                """INSERT INTO messages
                         (id, conversation_id, role, content, visualization, answer_datasets, steps, action_confirm)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), session_id, role, *payload),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (session_id,),
            )
            conn.commit()
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            logger.warning(
                "Chat message persistence failed: session_id=%s role=%s error=%s",
                session_id,
                role,
                str(exc),
            )
        finally:
            if conn is not None:
                conn.close()

    @classmethod
    def _history_content_for_llm(cls, role: str, content: object) -> str:
        if isinstance(content, str):
            return content
        if role == "assistant" and isinstance(content, dict):
            final_answer = content.get("final_answer")
            if isinstance(final_answer, dict):
                answer = final_answer.get("final_answer") or final_answer.get("plain_text_summary")
                return str(answer) if answer else ""
            if isinstance(final_answer, str):
                return final_answer
        if isinstance(content, dict) and isinstance(content.get("content"), str):
            return content["content"]
        return cls._json_dumps(content) if content is not None else ""
