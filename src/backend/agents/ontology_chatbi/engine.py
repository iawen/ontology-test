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
import time
import uuid
from typing import AsyncGenerator, Optional, Callable

from core.llm.chat_model import get_async_client, get_model_name
from core.models.models import ChatRequest
from prompts.prompt import (
    init_prompt, get_system_prompt, get_system_tools, get_engine, get_query_engine,
)
from modules.actions import find_matching_actions, _execute_action, get_available_actions

from .state import State, AgentState, ToolCallRecord
from .agents import (
    SchemaRetrieverAgent,
    GlossaryMatcherAgent,
    SkillRouterAgent,
    ContextCompressorAgent,
    EntityDisambiguatorAgent,
    ToolExecutor,
)


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

        try:
            while current_state != State.DONE:
                handler = handlers.get(current_state)
                if not handler:
                    current_state = State.ERROR
                    state.error = f"未知状态: {current_state}"
                    continue

                next_state = await handler(state)

                # 发送待处理的 SSE 事件
                for event in state.sse_events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                state.sse_events.clear()

                current_state = next_state

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    # ============================================================
    # 状态处理函数（每个函数返回下一个状态）
    # ============================================================
    async def _handle_init(self, state: AgentState) -> State:
        """初始化场景"""
        init_prompt(state.scenario_id)
        state.system_prompt = get_system_prompt(state.scenario_id)
        state.tools = get_system_tools()
        return State.CONTEXT_PREP

    async def _handle_context_prep(self, state: AgentState) -> State:
        """上下文准备：动态注入 Schema + 术语匹配 + 技能路由 + 实体消歧"""
        engine = get_engine(state.scenario_id)
        query_engine = get_query_engine(state.scenario_id)

        # 1. Schema 动态检索
        schema_result = await self.schema_agent.retrieve(state.user_message, engine)
        state.inject_context(schema_result["context"])
        relevant_classes = schema_result["relevant_classes"]

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
            return State.ERROR

        message = response.choices[0].message

        # 无工具调用 → 最终回答
        if not message.tool_calls:
            content = message.content or ""
            state.sse_events.append({"type": "text", "content": content})
            state.messages.append({"role": "assistant", "content": content})

            # 检查是否需要行动
            return await self._check_action(state)

        # 有工具调用 → 进入工具分发
        state.pending_tool_calls = message.tool_calls
        state.messages.append(message.model_dump())

        # 发送工具调用事件
        for tc in message.tool_calls:
            state.sse_events.append({
                "type": "tool",
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments) if tc.function.arguments else {},
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

            # 执行工具（含后置自动校正，死循环防线）
            result = await executor.execute(tool_name, args, query_engine, engine)

            # 记录工具调用
            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=args,
                result=result if not result.get("error") else None,
                error=result.get("error"),
                retry_count=1 if result.get("error") else 0,
            )
            state.tool_call_records.append(record)
            state.all_tool_results.append({"name": tool_name, "result": result})

            # 发送工具结果事件
            state.sse_events.append({
                "type": "tool_result",
                "name": tool_name,
                "result": result,
            })

            # 注入工具结果到消息
            result_str = json.dumps(result, ensure_ascii=False, default=str)
            if len(result_str) > 3000:
                result_str = result_str[:3000] + "...[结果过长已截断]"
            state.messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
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

    async def _check_action(self, state: AgentState) -> State:
        """检查是否需要触发行动"""
        try:
            actions = await find_matching_actions(state.scenario_id, state.user_message)
            if not actions:
                return State.DONE

            for act in actions:
                if act.get("is_active") and act.get("requires_confirm"):
                    state.sse_events.append({
                        "type": "action_confirm",
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
