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

import asyncio
import json
import math
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, Callable

from core.db.db import get_db
from core.llm.chat_model import get_async_client, get_model_name
from core.models.models import ChatRequest
from prompts.prompt import (
    init_prompt, get_system_prompt, get_system_tools, get_engine, get_query_engine,
)
from modules.actions import find_matching_actions, _execute_action, get_available_actions
from tools.logger import logger

from .state import State, AgentState, ToolCallRecord
from .agents import (
    SchemaRetrieverAgent,
    GlossaryMatcherAgent,
    SkillRouterAgent,
    ContextCompressorAgent,
    EntityDisambiguatorAgent,
    ToolExecutor,
)


TOOL_PURPOSES = {
    "query_ontology_data": "按指标、维度和筛选条件查询业务数据。",
    "python_analyze": "基于查询结果做进一步统计、计算或归纳分析。",
    "execute_action": "根据分析结果触发需要确认的业务动作。",
}


def get_tool_purpose(tool_name: str) -> str:
    return TOOL_PURPOSES.get(tool_name, "执行当前分析步骤所需的辅助能力。")


class ChatEngineV3:
    """状态机驱动的 Chat 引擎（字典路由+扁平循环）"""

    def __init__(self):
        self.client = get_async_client()
        self.model_name = get_model_name()

        # 无状态子智能体实例化
        self.schema_agent = SchemaRetrieverAgent()
        self.glossary_agent = GlossaryMatcherAgent()
        self.skill_agent = SkillRouterAgent()
        self.compressor_agent = ContextCompressorAgent()
        self.entity_agent = EntityDisambiguatorAgent()

    async def stream_chat(self, req: ChatRequest) -> AsyncGenerator[str, None]:
        """
        状态机驱动的流式聊天。

        使用字典路由替代 if-elif 链，保持主循环扁平。
        """
        state = AgentState(
            scenario_id=req.scenario_id,
            conversation_id=req.conversation_id or str(uuid.uuid4()),
            messages=req.messages or [],
        )

        # 提取最后一条用户消息
        for m in reversed(req.messages or []):
            if m.get("role") == "user":
                state.user_message = m.get("content", "")
                break

        # 状态路由表（轻量级字典路由，替代 if-elif 链）
        handlers = {
            State.INIT: self._handle_init,
            State.CONTEXT_PREP: self._handle_context_prep,
            State.LLM_CALL: self._handle_llm_call,
            State.TOOL_DISPATCH: self._handle_tool_dispatch,
            State.TOOL_EXECUTE: self._handle_tool_execute,
            State.CLARIFY: self._handle_clarify,
            State.ACTION_CONFIRM: self._handle_action_confirm,
            State.ACTION_EXECUTE: self._handle_action_execute,
            State.FINAL_STREAM: self._handle_final_stream,
            State.DONE: self._handle_done,
            State.ERROR: self._handle_error,
        }

        current_state = State.INIT
        start_time = time.time()
        transition_count = 0
        max_transitions = 50

        logger.info(
            "Chat stream started: scenario_id=%s conversation_id=%s messages=%d user_message_len=%d",
            state.scenario_id,
            state.conversation_id,
            len(req.messages or []),
            len(state.user_message),
        )

        try:
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
                    "Chat state enter: conversation_id=%s state=%s round=%d",
                    state.conversation_id,
                    current_state.value,
                    state.current_round,
                )
                next_state = await handler(state)
                state.record_transition(current_state, next_state)

                # 发送待处理的 SSE 事件
                for event in state.sse_events:
                    yield f"data: {self._json_dumps(event)}\n\n"
                state.sse_events.clear()

                current_state = next_state

            done_event = {
                "type": "done",
                "tool_results": state.all_tool_results,
            }
            self._schedule_conversation_persistence(state)
            yield f"data: {self._json_dumps(done_event)}\n\n"
            logger.info(
                "Chat stream completed: scenario_id=%s conversation_id=%s rounds=%d tools=%d duration_ms=%d",
                state.scenario_id,
                state.conversation_id,
                state.current_round,
                len(state.all_tool_results),
                int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.exception(
                "Chat stream failed: scenario_id=%s conversation_id=%s state=%s error=%s",
                state.scenario_id,
                state.conversation_id,
                current_state.value if isinstance(current_state, State) else current_state,
                str(e),
            )
            yield f"data: {self._json_dumps({'type': 'error', 'content': str(e)})}\n\n"

    # ============================================================
    # 状态处理函数（每个函数返回下一个状态）
    # ============================================================
    async def _handle_init(self, state: AgentState) -> State:
        """初始化场景"""
        init_prompt(state.scenario_id)
        state.system_prompt = get_system_prompt(state.scenario_id)
        state.tools = get_system_tools()
        logger.info(
            "Chat initialized: scenario_id=%s conversation_id=%s tools=%d system_prompt_len=%d",
            state.scenario_id,
            state.conversation_id,
            len(state.tools or []),
            len(state.system_prompt or ""),
        )
        return State.CONTEXT_PREP

    async def _handle_context_prep(self, state: AgentState) -> State:
        """上下文准备：动态注入 Schema + 术语匹配 + 技能路由 + 实体消歧"""
        engine = get_engine(state.scenario_id)
        query_engine = get_query_engine(state.scenario_id)

        # 1. Schema 动态检索
        schema_result = await self.schema_agent.retrieve(state.user_message, engine)
        state.inject_context(schema_result["context"])
        relevant_classes = schema_result["relevant_classes"]
        logger.info(
            "Chat schema context prepared: conversation_id=%s relevant_classes=%d context_len=%d",
            state.conversation_id,
            len(relevant_classes),
            len(schema_result.get("context", "")),
        )

        # 2. 术语匹配
        glossary_matches = await self.glossary_agent.match(state.scenario_id, state.user_message)
        state.glossary_matches = glossary_matches

        # 3. 技能路由
        skill_matches = await self.skill_agent.route(state.scenario_id, state.user_message)
        state.skill_matches = skill_matches

        # 4. 实体消歧
        entity_hints = await self.entity_agent.disambiguate(
            state.user_message, relevant_classes, query_engine
        )
        state.inject_entity_hints(entity_hints)
        logger.info(
            "Chat dynamic context matched: conversation_id=%s glossary=%d skills=%d entity_hints=%d",
            state.conversation_id,
            len(glossary_matches),
            len(skill_matches),
            len(entity_hints),
        )

        # 5. 构建完整 system prompt
        full_prompt = state.system_prompt + "\n\n" + state.ontology_context

        # 6. 注入术语和技能
        if glossary_matches:
            glossary_note = "\n\n[术语匹配] 用户消息中包含以下专用术语：\n"
            for gm in glossary_matches:
                glossary_note += f"  - 「{gm['term']}」→ 标准名: {gm['standard_name']}"
                if gm.get("description"):
                    glossary_note += f"（{gm['description']}）"
                glossary_note += "\n"
            full_prompt += glossary_note

        if skill_matches:
            skill_note = "\n\n[技能匹配] 以下技能包与用户问题相关：\n"
            for sk in skill_matches:
                skill_note += f"  - **{sk['name']}**\n{sk['content'][:500]}\n\n"
            full_prompt += skill_note

        # 7. 注入实体消歧提示
        if entity_hints:
            entity_note = "\n\n[实体消歧提示] 用户提到的实体值对应数据库标准值：\n"
            for eh in entity_hints:
                entity_note += f"  - 用户说的「{eh['user_value']}」→ 数据库标准值: 「{eh['standard_value']}」(字段: {eh['field']}, 相似度: {eh['similarity']:.2f})\n"
            full_prompt += entity_note

        # 8. 上下文压缩
        full_prompt = await self.compressor_agent.compress(full_prompt)
        logger.info(
            "Chat prompt ready: conversation_id=%s prompt_len=%d history_messages=%d",
            state.conversation_id,
            len(full_prompt),
            len(state.messages),
        )

        # 9. 构建消息列表
        state.messages = [{"role": "system", "content": full_prompt}] + [
            m for m in state.messages if m.get("role") in ("user", "assistant") and m.get("content")
        ]

        return State.LLM_CALL

    async def _handle_llm_call(self, state: AgentState) -> State:
        """调用 LLM"""
        if state.current_round >= state.max_rounds:
            state.sse_events.append({
                "type": "text",
                "content": "已达到最大工具调用轮次，请尝试简化问题。"
            })
            return State.DONE

        state.current_round += 1
        logger.info(
            "Chat LLM call started: conversation_id=%s round=%d messages=%d tools=%d",
            state.conversation_id,
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
                "Chat LLM call failed: conversation_id=%s round=%d error=%s",
                state.conversation_id,
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
            state.assistant_content += content
            state.sse_events.append({"type": "text", "content": content})
            state.messages.append({"role": "assistant", "content": content})
            logger.info(
                "Chat LLM final answer: conversation_id=%s round=%d content_len=%d",
                state.conversation_id,
                state.current_round,
                len(content),
            )

            # 检查是否需要行动
            return await self._check_action(state)

        # 有工具调用 → 进入工具分发
        state.pending_tool_calls = message.tool_calls
        state.messages.append(message.model_dump())
        logger.info(
            "Chat LLM tool calls: conversation_id=%s round=%d tool_count=%d names=%s",
            state.conversation_id,
            state.current_round,
            len(message.tool_calls),
            ",".join(tc.function.name for tc in message.tool_calls),
        )

        # 发送工具调用事件
        for tc in message.tool_calls:
            try:
                tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                tool_args = {}
                logger.warning(
                    "Chat tool arguments are not valid JSON: conversation_id=%s tool=%s raw=%s",
                    state.conversation_id,
                    tc.function.name,
                    tc.function.arguments,
                )
            state.tool_timings[tc.id] = {
                "started_at": llm_started_at,
                "planning_finished_at": llm_finished_at,
                "planning_duration_ms": planning_duration_ms,
            }
            state.sse_events.append({
                "type": "tool",
                "name": tc.function.name,
                "description": get_tool_purpose(tc.function.name),
                "arguments": tool_args,
                "started_at": llm_started_at,
                "planning_finished_at": llm_finished_at,
                "planning_duration_ms": planning_duration_ms,
            })

        return State.TOOL_DISPATCH

    async def _handle_tool_dispatch(self, state: AgentState) -> State:
        """工具分发：路由到对应执行器"""
        # 直接进入执行（工具执行器内部处理分发）
        return State.TOOL_EXECUTE

    async def _handle_tool_execute(self, state: AgentState) -> State:
        """工具执行：含后置自动校正"""
        engine = get_engine(state.scenario_id)
        query_engine = get_query_engine(state.scenario_id)
        executor = ToolExecutor(state.scenario_id, self.entity_agent)

        for tc in state.pending_tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}

            if tool_name == "python_analyze" and "query_history" not in args:
                args = {
                    **args,
                    "query_history": [
                        item for item in state.all_tool_results
                        if item.get("name") == "query_ontology_data"
                    ],
                }

            logger.info(
                "Chat tool execution started: conversation_id=%s tool=%s args=%s",
                state.conversation_id,
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
                    "Chat tool execution returned error: conversation_id=%s tool=%s error=%s",
                    state.conversation_id,
                    tool_name,
                    result.get("error"),
                )
            else:
                logger.info(
                    "Chat tool execution completed: conversation_id=%s tool=%s result_len=%d",
                    state.conversation_id,
                    tool_name,
                    len(result_preview),
                )

            result_error = result.get("error") if isinstance(result, dict) else None

            # 记录工具调用
            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=args,
                result=result if not result_error else None,
                error=result_error,
                retry_count=1 if result_error else 0,
            )
            state.tool_call_records.append(record)
            state.all_tool_results.append({
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
            })

            # 发送工具结果事件
            state.sse_events.append({
                "type": "tool_result",
                "name": tool_name,
                "description": get_tool_purpose(tool_name),
                "result": result,
                "result_preview": result_preview,
                "started_at": total_started_at,
                "planning_finished_at": planning_finished_at,
                "planning_duration_ms": planning_duration_ms,
                "execution_started_at": execution_started_at,
                "finished_at": tool_finished_at,
                "execution_duration_ms": execution_duration_ms,
                "duration_ms": total_duration_ms,
            })

            if isinstance(result, dict):
                if result.get("type") == "clarification_needed":
                    state.sse_events.append({"type": "clarification", "data": result})
                elif result.get("type") == "drill_down_result":
                    state.sse_events.append({"type": "drilldown", "data": result})
                elif result.get("type") == "query_result" and result.get("row_count", 0) > 0:
                    state.sse_events.append({"type": "chart_data", "data": result})

            # 注入工具结果到消息
            state.messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_preview,
            })

        state.pending_tool_calls = []
        return State.LLM_CALL

    async def _handle_clarify(self, state: AgentState) -> State:
        """主动反问"""
        # 反问事件已在 LLM_CALL 中发送
        return State.DONE

    async def _handle_action_confirm(self, state: AgentState) -> State:
        """行动确认"""
        return State.DONE

    async def _handle_action_execute(self, state: AgentState) -> State:
        """行动执行"""
        return State.DONE

    async def _handle_final_stream(self, state: AgentState) -> State:
        """最终流式输出"""
        return State.DONE

    async def _handle_done(self, state: AgentState) -> State:
        """完成"""
        state.sse_events.append({
            "type": "done",
            "tool_results": state.all_tool_results,
        })
        return State.DONE

    async def _handle_error(self, state: AgentState) -> State:
        """错误处理"""
        state.sse_events.append({
            "type": "error",
            "content": state.error or "未知错误",
        })
        return State.DONE

    # ============================================================
    # 辅助函数
    # ============================================================

    @classmethod
    def _json_dumps(cls, value) -> str:
        return json.dumps(cls._make_json_safe(value), ensure_ascii=False, default=str, allow_nan=False)

    @classmethod
    def _make_json_safe(cls, value):
        try:
            import pandas as pd
            import numpy as np
        except Exception:
            pd = None
            np = None

        if pd is not None:
            if isinstance(value, pd.DataFrame):
                return value.head(100).to_dict("records")
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
            return value if math.isfinite(value) else None
        if pd is not None and (value is pd.NA or value is pd.NaT):
            return None

        if isinstance(value, dict):
            return {str(k): cls._make_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._make_json_safe(item) for item in value]
        return value

    async def _check_action(self, state: AgentState) -> State:
        """检查是否需要触发行动"""
        try:
            assistant_messages = [
                m.get("content", "")
                for m in state.messages
                if m.get("role") == "assistant" and m.get("content")
            ]
            action_text = assistant_messages[-1] if assistant_messages else state.user_message
            actions = await find_matching_actions(state.scenario_id, action_text)
            if not actions:
                return State.DONE

            for act in actions:
                if act.get("is_active") and act.get("requires_confirm"):
                    state.action_confirm = {
                        "action_id": act["id"],
                        "action_name": act["name"],
                        "description": act.get("description", ""),
                        "action_type": act.get("action_type", "notification"),
                        "requires_confirm": act.get("requires_confirm", True),
                        "message": act.get("message", ""),
                    }
                    state.sse_events.append({
                        "type": "action_confirm",
                        "data": state.action_confirm,
                        "action": {
                            "id": act["id"],
                            "name": act["name"],
                            "description": act.get("description", ""),
                            "action_type": act.get("action_type", "notification"),
                            "message": act.get("message", ""),
                        }
                    })
                    return State.ACTION_CONFIRM
        except:
            pass

        return State.DONE

    def _schedule_conversation_persistence(self, state: AgentState):
        """在流式响应完成时异步保存本轮用户消息和助手回复。"""
        payload = self._build_conversation_payload(state)
        if not payload:
            return
        task = asyncio.create_task(self._persist_conversation_messages(payload))
        task.add_done_callback(self._log_persistence_task_result)

    def _build_conversation_payload(self, state: AgentState) -> Optional[dict]:
        if not state.conversation_id or not state.user_message:
            return None

        assistant_message = {
            "id": f"a-{uuid.uuid4().hex[:12]}",
            "role": "assistant",
            "content": state.assistant_content,
            "visualization": self._pick_latest_valid_query_result(state.all_tool_results),
            "steps": self._build_tool_steps(state.all_tool_results),
            "action_confirm": state.action_confirm,
        }
        user_message = {
            "id": f"u-{uuid.uuid4().hex[:12]}",
            "role": "user",
            "content": state.user_message,
        }
        return {
            "conversation_id": state.conversation_id,
            "messages": [user_message, assistant_message],
        }

    @staticmethod
    def _normalize_query_result(value: dict) -> Optional[dict]:
        if not isinstance(value, dict) or value.get("type") != "query_result" or value.get("error"):
            return None

        rows = value.get("rows") if isinstance(value.get("rows"), list) else []
        columns = value.get("columns") if isinstance(value.get("columns"), list) else []
        if not columns and rows and isinstance(rows[0], dict):
            columns = list(rows[0].keys())

        if not rows or not columns:
            return None

        normalized = dict(value)
        normalized["class_id"] = value.get("class_id") or value.get("target_class") or "query_result"
        normalized["class_name"] = value.get("class_name") or value.get("target_class") or "查询结果"
        normalized["columns"] = columns
        normalized["rows"] = rows
        normalized["total"] = value.get("total") if isinstance(value.get("total"), int) else value.get("row_count", len(rows))
        return normalized

    def _pick_latest_valid_query_result(self, tool_results: list[dict]) -> Optional[dict]:
        for item in reversed(tool_results or []):
            normalized = self._normalize_query_result(item.get("result") if isinstance(item, dict) else None)
            if normalized:
                return normalized
        return None

    @staticmethod
    def _build_tool_steps(tool_results: list[dict]) -> list[dict]:
        steps = []
        for item in tool_results or []:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            steps.append({
                "name": item.get("name", ""),
                "description": item.get("description") or get_tool_purpose(item.get("name", "")),
                "args": item.get("arguments", {}),
                "status": "failed" if isinstance(result, dict) and result.get("error") else "completed",
                "result": result,
                "startedAt": item.get("started_at"),
                "planningFinishedAt": item.get("planning_finished_at"),
                "planningDurationMs": item.get("planning_duration_ms"),
                "executionStartedAt": item.get("execution_started_at"),
                "executionDurationMs": item.get("execution_duration_ms"),
                "finishedAt": item.get("finished_at"),
                "durationMs": item.get("duration_ms"),
            })
        return steps

    async def _persist_conversation_messages(self, payload: dict):
        await asyncio.to_thread(self._persist_conversation_messages_sync, payload)

    @staticmethod
    def _persist_conversation_messages_sync(payload: dict):
        conn = get_db()
        try:
            conv_id = payload["conversation_id"]
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
                        json.dumps(msg.get("action_confirm"), ensure_ascii=False, default=str) if msg.get("action_confirm") else "",
                    )
                    for msg in payload.get("messages", [])
                ],
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (datetime.now(timezone.utc), conv_id))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _log_persistence_task_result(task: asyncio.Task):
        try:
            task.result()
        except Exception as exc:
            logger.exception("Chat conversation persistence failed: error=%s", str(exc))
