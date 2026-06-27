"""
Chat API v5 — 本体驱动的认知循环 + Plan-and-Execute
=====================================================
核心能力：
  - Plan-and-Execute：复杂问题自动分解为子步骤，逐步执行
  - Clarification：主动反问与需求对齐（SSE 事件 type=clarification）
  - Drill-down：下钻与原始数据穿透（SSE 事件 type=drilldown）
  - Action：洞察→行动闭环（SSE 事件 type=action_confirm / type=action_result）
  - Context：本体上下文自动注入（Data+Logic+Action）
"""

import json
import time
import uuid
import pandas as pd
from typing import Optional, Generator, Any
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.llm.chat_model import get_async_client, get_model_name
from core.ontology.ontology_engine import OntologyEngine
from core.ontology.data_query import DataQueryEngine
from core.db.db import get_db
from core.models.models import ChatRequest
from prompts.prompt import (
    init_prompt, get_system_prompt, get_system_tools, get_engine, get_query_engine,
)
from modules.metrics import lookup_metric as do_lookup_metric
from modules.glossary import match_glossary_terms
from modules.skills import route_skills
from modules.actions import find_matching_actions, _execute_action, get_available_actions
from agents.tools.python_analyize import python_analyze


router = APIRouter()


# ──────────────────────────────────────────────────────────
# 对话上下文管理
# ──────────────────────────────────────────────────────────
class ConversationContext:
    """管理单次对话的上下文信息"""

    def __init__(self, conversation_id: str = ""):
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.messages: list[dict] = []
        self.query_cache: dict = {}  # 缓存查询结果
        self.last_query_info: Optional[dict] = None
        self.created_at = time.time()

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_call_result(self, tool_call_id: str, result: str):
        pass  # 存储在 messages 中


# ──────────────────────────────────────────────────────────
# 对话上下文存储
# ──────────────────────────────────────────────────────────
_contexts: dict[str, ConversationContext] = {}


def get_or_create_context(conversation_id: str = "") -> ConversationContext:
    """获取或创建对话上下文"""
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
    if conversation_id not in _contexts:
        _contexts[conversation_id] = ConversationContext(conversation_id)
    return _contexts[conversation_id]


# ──────────────────────────────────────────────────────────
# 工具执行器
# ──────────────────────────────────────────────────────────
class ToolExecutor:
    """执行 LLM 调用的工具"""

    def __init__(self, oe: OntologyEngine, dq: DataQueryEngine):
        self.oe = oe
        self.dq = dq

    def execute(self, tool_name: str, args: dict, context: ConversationContext = None) -> str:
        """执行工具并返回结果字符串"""
        try:
            if tool_name == "get_ontology_schema":
                return self._get_ontology_schema(args)

            elif tool_name == "query_ontology_data":
                return self._query_ontology_data(args, context)

            elif tool_name == "get_field_types":
                return self._get_field_types(args)

            elif tool_name == "get_join_path":
                return self._get_join_path(args)

            elif tool_name == "fuzzy_search_values":
                return self._fuzzy_search_values(args)

            elif tool_name == "get_class_sample":
                return self._get_class_sample(args)

            elif tool_name == "python_analyze":
                return self._python_analyze(args, context)

            else:
                return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": f"工具执行失败: {str(e)}"}, ensure_ascii=False)

    def _get_ontology_schema(self, args: dict) -> str:
        """获取本体 schema"""
        class_id = args.get("class_id", "")
        if class_id:
            info = self.oe.get_class_info(class_id)
            if not info:
                return json.dumps({"error": f"未找到 class: {class_id}"}, ensure_ascii=False)
            result = {
                "class_id": class_id,
                "name_cn": info.get("name_cn", ""),
                "table_name": info.get("table_name", ""),
                "field_map": info.get("field_map", {}),
                "field_types": info.get("field_types", {}),
                "primary_key": info.get("primary_key", ""),
                "related_classes": self.oe.get_related_classes(class_id),
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            classes = []
            for cls in self.oe.list_classes():
                classes.append({
                    "id": cls.get("id", ""),
                    "name_cn": cls.get("name_cn", ""),
                    "description": cls.get("description", ""),
                })
            relationships = []
            for rel in self.oe.list_relationships():
                relationships.append({
                    "source": rel.get("source", ""),
                    "target": rel.get("target", ""),
                    "type": rel.get("type", ""),
                    "source_key": rel.get("source_key", ""),
                    "target_key": rel.get("target_key", ""),
                })
            metrics = self.oe.list_metrics()
            return json.dumps({
                "classes": classes,
                "relationships": relationships,
                "metrics": metrics,
            }, ensure_ascii=False, indent=2)

    def _query_ontology_data(self, args: dict, context: ConversationContext = None) -> str:
        """执行本体查询"""
        target_class = args.get("target_class", "")
        if not target_class:
            return json.dumps({"error": "target_class 不能为空"}, ensure_ascii=False)

        cache_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
        if context and cache_key in context.query_cache:
            return context.query_cache[cache_key]

        result = self.dq.execute_query(
            target_class=target_class,
            metrics=args.get("metrics", []),
            dimensions=args.get("dimensions", []),
            filters=args.get("filters", []),
            # join_class=args.get("join_class", ""),
            join_classes=args.get("join_classes", []),
            order_by=args.get("order_by", ""),
            limit=args.get("limit"),
            having=args.get("having", []),
        )

        if context:
            context.query_cache[cache_key] = json.dumps(result, ensure_ascii=False, default=str)
            context.last_query_info = result

        return json.dumps(result, ensure_ascii=False, default=str)

    def _get_field_types(self, args: dict) -> str:
        """获取字段类型"""
        class_id = args.get("class_id", "")
        if not class_id:
            return json.dumps({"error": "class_id 不能为空"}, ensure_ascii=False)
        field_types = self.oe.get_field_types(class_id)
        return json.dumps({
            "class_id": class_id,
            "field_types": field_types,
        }, ensure_ascii=False, indent=2)

    def _get_join_path(self, args: dict) -> str:
        """获取 JOIN 路径"""
        source = args.get("source", "")
        target = args.get("target", "")
        if not source or not target:
            return json.dumps({"error": "source 和 target 不能为空"}, ensure_ascii=False)
        path = self.oe.get_join_path(source, target)
        return json.dumps({
            "source": source,
            "target": target,
            "join_path": path,
            "hops": len(path),
        }, ensure_ascii=False, indent=2)

    def _fuzzy_search_values(self, args: dict) -> str:
        """模糊搜索字段值"""
        class_id = args.get("class_id", "")
        field_name = args.get("field_name", "")
        keyword = args.get("keyword", "")
        limit = args.get("limit", 10)

        if not class_id or not field_name or not keyword:
            return json.dumps({"error": "class_id, field_name, keyword 不能为空"}, ensure_ascii=False)

        result = self.dq.fuzzy_search_values(class_id, field_name, keyword, limit)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _get_class_sample(self, args: dict) -> str:
        """获取 class 样本数据"""
        class_id = args.get("class_id", "")
        limit = args.get("limit", 5)
        if not class_id:
            return json.dumps({"error": "class_id 不能为空"}, ensure_ascii=False)

        result = self.dq.get_class_sample(class_id, limit)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _python_analyze(self, args: dict, context: ConversationContext = None) -> Any:
        """执行 Python 分析"""
        code = args.get("code", "")
        if not code:
            return {"error": "code 不能为空"}

        query_history = args.get("query_history", [])
        all_query_data = json.dumps(query_history, ensure_ascii=False, default=str)
        
        last_result = query_history[-1]["result"] if query_history else []
        data_json = json.dumps(last_result, ensure_ascii=False, default=str)
        
        print(f"[python_analyze] Executing code with {len(query_history)} query results")
        return python_analyze(code=code, data_json=data_json, all_query_data=all_query_data)


MAX_TOOL_ROUNDS = 24


def _compress_messages(messages: list[dict], max_messages: int = 20) -> list[dict]:
    if len(messages) <= max_messages:
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    kept = non_system[-(max_messages - len(system_msgs)):]

    truncated = non_system[:len(non_system) - len(kept)]
    if truncated:
        summary_parts = []
        for m in truncated:
            role = m.get("role", "")
            content = m.get("content", "")
            if content and isinstance(content, str):
                summary_parts.append(f"[{role}]: {content[:100]}...")
        summary = "（历史对话摘要）" + "\\n".join(summary_parts[:5])
        kept.insert(0, {"role": "assistant", "content": summary})

    return system_msgs + kept


async def chat_stream_generator(req: ChatRequest):
    scenario_id = req.scenario_id
    conversation_id = req.conversation_id
    messages = req.messages

    TOOL_NAME_MAP = {
        # "get_ontology_schema": "模型结构分析",
        "query_ontology_data": "多维模型高阶查询",
        # "get_field_types": "字段类型校验",
        # "get_join_path": "关系拓扑推导",
        "fuzzy_search_values": "实体语义消歧",
        "get_class_sample": "明细数据穿透",
        "python_analyze": "深度策略分析(Python)"
    }

    try:
        init_prompt(scenario_id)
        oe = get_engine(scenario_id)
        dq = get_query_engine(scenario_id)

        executor = ToolExecutor(oe, dq)
        system_prompt = get_system_prompt(scenario_id)

        messages_to_send = [{"role": "system", "content": system_prompt}]

        last_user_msg = ""
        for m in reversed(req.messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        context = get_or_create_context(conversation_id)
        context.add_user_message(last_user_msg)

        if last_user_msg and scenario_id:
            glossary_matches = match_glossary_terms(scenario_id, last_user_msg)
            if glossary_matches:
                print(f"[glossary_matches] {glossary_matches}")
                glossary_note = "\n\n[术语匹配] 用户消息中包含以下专用术语：\n"
                for gm in glossary_matches:
                    glossary_note += f"  - 「{gm['term']}」→ 标准名: {gm['standard_name']}"
                    if gm.get("description"):
                        glossary_note += f"（{gm['description']}）"
                    glossary_note += "\n"
                messages_to_send.append({"role": "system", "content": glossary_note})

            matched_skills = await route_skills(scenario_id, last_user_msg)
            if matched_skills:
                skill_note = "\n\n[技能匹配] 以下技能包与用户问题相关：\n"
                for sk in matched_skills:
                    skill_note += f"  - **{sk['name']}**\n{sk['content'][:500]}\n\n"
                messages_to_send.append({"role": "system", "content": skill_note})

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                messages_to_send.append({"role": role, "content": content})

        messages_to_send = _compress_messages(messages_to_send)

        accumulated_plan_steps = []
        all_tool_results = []
        query_history = []

        for _round_idx in range(MAX_TOOL_ROUNDS):
            response = await get_async_client().chat.completions.create(
                model=get_model_name(),
                messages=messages_to_send,
                tools=get_system_tools(),
                tool_choice="auto",
                temperature=0.5,
                max_tokens=2048,
            )
            message = response.choices[0].message
            print(f"========== [{_round_idx+1}] LLM message: \n{message}\n==========")

            if not message.tool_calls:
                break

            messages_to_send.append(message.model_dump())

            round_step_indices = []
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except:
                    fn_args = {}

                step_id = f"step_{len(accumulated_plan_steps)}"
                new_step = {
                    "step_id": step_id,
                    "tool": TOOL_NAME_MAP.get(fn_name, fn_name),
                    "description": "等待认知引擎调度...",
                    "tool_args": fn_args,
                    "status": "pending",
                    "result": None
                }
                accumulated_plan_steps.append(new_step)
                round_step_indices.append(len(accumulated_plan_steps) - 1)

            for idx_in_round, tool_call in enumerate(message.tool_calls):
                fn_name = tool_call.function.name
                global_step_idx = round_step_indices[idx_in_round]
                
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except:
                    fn_args = {}

                # 【修复硬编码】精确匹配 prompt 中的 python_analyze 名称
                if fn_name == "python_analyze":
                    fn_args["query_history"] = query_history

                print(f"[Chat] Tool call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:200]})")

                accumulated_plan_steps[global_step_idx]["status"] = "running"
                accumulated_plan_steps[global_step_idx]["description"] = "正通过本体知识进行深层感知..."

                yield f"data: {json.dumps({'type': 'tool', 'name': TOOL_NAME_MAP.get(fn_name, fn_name), 'arguments': fn_args}, ensure_ascii=False)}\n\n"

                # 执行原始执行逻辑
                tool_result_raw = executor.execute(fn_name, fn_args, context)

                # 【智能解包中间件】将底层的 JSON 字符串形态无缝转换为原生 Dict/List
                tool_result = tool_result_raw
                if isinstance(tool_result_raw, str):
                    try:
                        tool_result = json.loads(tool_result_raw)
                    except Exception:
                        pass
                result_preview = json.dumps(tool_result, ensure_ascii=False, default=str)

                # 【修复硬编码】精确积累多维模型查询库
                if fn_name == "query_ontology_data" and (not isinstance(tool_result, dict) or "error" not in result_preview):
                    rows_data = tool_result.get("rows", tool_result) if isinstance(tool_result, dict) else tool_result
                    query_history.append({
                        "tool": fn_name,
                        "args": fn_args,
                        "result": rows_data,
                    })

                
                accumulated_plan_steps[global_step_idx]["status"] = "completed"
                accumulated_plan_steps[global_step_idx]["description"] = "数据抓取及校验闭环已完成"
                accumulated_plan_steps[global_step_idx]["result"] = tool_result

                yield f"data: {json.dumps({'type': 'tool_result', 'name': TOOL_NAME_MAP.get(fn_name, fn_name), 'result_preview': result_preview}, ensure_ascii=False)}\n\n"

                # ── 完美的卡片数据流派发 ──
                if isinstance(tool_result, dict):
                    if tool_result.get("type") == "clarification_needed":
                        yield f"data: {json.dumps({'type': 'clarification', 'data': tool_result}, ensure_ascii=False)}\n\n"
                    elif tool_result.get("type") == "drill_down_result":
                        yield f"data: {json.dumps({'type': 'drilldown', 'data': tool_result}, ensure_ascii=False)}\n\n"
                    elif tool_result.get("type") == "query_result" and tool_result.get("row_count", 0) > 0:
                        yield f"data: {json.dumps({'type': 'chart_data', 'data': tool_result}, ensure_ascii=False)}\n\n"

                all_tool_results.append({"tool": fn_name, "args": fn_args, "result": tool_result})

                messages_to_send.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                })

        for s in accumulated_plan_steps:
            if s["status"] != "completed":
                s["status"] = "completed"
        yield f"data: {json.dumps({'type': 'plan', 'title': '智能体 Plan-and-Execute 认知循环', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)}, ensure_ascii=False)}\n\n"

        final_stream = await get_async_client().chat.completions.create(
            model=get_model_name(),
            messages=messages_to_send,
            temperature=0.5,
            max_tokens=2048,
            stream=True
        )

        full_response_text = ""
        async for chunk in final_stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full_response_text += delta.content
                yield f"data: {json.dumps({'type': 'text', 'content': delta.content}, ensure_ascii=False)}\n\n"

        if full_response_text:
            try:
                matched_actions = await find_matching_actions(scenario_id, full_response_text)
                for act in matched_actions:
                    action_payload = {
                        "type": "action_confirm",
                        "data": {
                            "action_id": act["id"],
                            "action_name": act["name"],
                            "description": act.get("description", ""),
                            "action_type": act.get("action_type", "notification"),
                            "requires_confirm": act.get("requires_confirm", True),
                            "message": act.get("message", "")
                        }
                    }
                    yield f"data: {json.dumps(action_payload, ensure_ascii=False)}\n\n"
                    break
            except:
                pass

        yield f"data: {json.dumps({'type': 'done', 'tool_results': all_tool_results}, ensure_ascii=False)}\n\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"


@router.post("/api/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        chat_stream_generator(req), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })