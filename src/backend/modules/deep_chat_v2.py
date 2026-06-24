"""
Chat API v6 - Official Deep Agents harness
==========================================
独立于 modules/chat.py 的新版 /api/chat 实现：
  - 基于 deepagents.create_deep_agent，而不是手写 LangGraph 循环
  - 复用 configs.global_config 的异步 OpenAI client
  - 保留原 chat.py 的业务工具、SSE 事件、图表/下钻/澄清卡片和行动确认
"""

import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from configs.global_config import Cfg, client
from modules.actions import find_matching_actions
from modules.glossary import match_glossary_terms
from modules.metrics import lookup_metric as do_lookup_metric
from modules.models import ChatRequest
from modules.skills import route_skills
from prompts.prompt import get_engine, get_query_engine, get_system_prompt, get_system_tools, init_prompt
from tools.python_analyize import python_analyze as run_python_analyze


router = APIRouter()
MAX_CONTEXT_MESSAGES = 24
DEEP_AGENT_RECURSION_LIMIT = 120

TOOL_NAME_MAP = {
    "get_ontology_schema": "模型结构分析",
    "query_ontology_data": "多维模型高阶查询",
    "get_field_types": "字段类型校验",
    "get_join_path": "关系拓扑推导",
    "fuzzy_search_values": "实体语义消歧",
    "get_class_sample": "明细数据穿透",
    "lookup_metric": "指标定义检索",
    "python_analyze": "深度策略分析(Python)",
    "write_todos": "任务规划",
    "task": "子智能体委派",
    "ls": "虚拟文件列表",
    "read_file": "虚拟文件读取",
    "write_file": "虚拟文件写入",
    "edit_file": "虚拟文件编辑",
    "glob": "虚拟文件匹配",
    "grep": "虚拟文件检索",
}


class ConversationContext:
    def __init__(self, conversation_id: str = ""):
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.query_cache: dict[str, str] = {}
        self.last_query_info: Optional[dict] = None


_contexts: dict[str, ConversationContext] = {}


def get_or_create_context(conversation_id: str = "") -> ConversationContext:
    conversation_id = conversation_id or str(uuid.uuid4())
    if conversation_id not in _contexts:
        _contexts[conversation_id] = ConversationContext(conversation_id)
    return _contexts[conversation_id]


def _new_llm(temperature: float = 0.3, streaming: bool = False) -> ChatOpenAI:
    return ChatOpenAI(
        model=Cfg.model_name,
        api_key=Cfg.openai_api_key,
        base_url=Cfg.openai_base_url,
        root_async_client=client,
        async_client=client.chat.completions,
        temperature=temperature,
        streaming=streaming,
    )


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _try_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _last_user_message(req: ChatRequest) -> str:
    for message in reversed(req.messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _compress_messages(messages: list[dict], max_messages: int = MAX_CONTEXT_MESSAGES) -> list[dict]:
    if len(messages) <= max_messages:
        return messages

    kept = messages[-max_messages:]
    dropped = messages[:len(messages) - len(kept)]
    summary_lines = []
    for message in dropped[:8]:
        content = str(message.get("content", ""))
        if content:
            summary_lines.append(f"[{message.get('role', '')}]: {content[:120]}")

    if summary_lines:
        kept.insert(0, {
            "role": "assistant",
            "content": "（历史对话摘要）\n" + "\n".join(summary_lines),
        })
    return kept


def _request_messages(req: ChatRequest) -> list[dict]:
    messages = []
    for message in req.messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return _compress_messages(messages)


def _to_langchain_messages(messages: list[dict]) -> list:
    result = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not content:
            continue
        if role == "assistant":
            result.append(AIMessage(content=content))
        elif role == "system":
            result.append(SystemMessage(content=content))
        else:
            result.append(HumanMessage(content=content))
    return result


async def _build_context_notes(scenario_id: str, user_message: str) -> str:
    notes = ""
    if not scenario_id or not user_message:
        return notes

    glossary_matches = match_glossary_terms(scenario_id, user_message)
    if glossary_matches:
        notes += "\n\n[术语匹配]\n"
        for match in glossary_matches:
            notes += f"- {match['term']} -> {match['standard_name']}"
            if match.get("description"):
                notes += f"（{match['description']}）"
            notes += "\n"

    matched_skills = await route_skills(scenario_id, user_message)
    if matched_skills:
        notes += "\n\n[技能匹配]\n"
        for skill in matched_skills:
            notes += f"- {skill['name']}: {skill['content'][:800]}\n"

    return notes


async def understand_intent(req: ChatRequest) -> dict:
    user_message = _last_user_message(req)
    if not user_message:
        return {"intent": "qa", "confidence": 0.0, "reason": "empty message"}

    prompt = [
        SystemMessage(content=(
            "你是一个意图分类器，只输出 JSON。"
            "将用户意图分类为 qa 或 data_analysis。"
            "data_analysis 表示需要查数、聚合、筛选、排序、下钻、指标计算、图表数据、Python分析或调用数据工具。"
            "qa 表示普通知识问答、概念解释、系统使用说明、闲聊或无需实时查询数据的问题。"
            "输出格式：{\"intent\":\"qa|data_analysis\",\"confidence\":0-1,\"reason\":\"...\"}。"
        )),
        HumanMessage(content=f"可用数据工具摘要：{_json_dumps(get_system_tools())[:3000]}\n\n用户问题：{user_message}"),
    ]

    try:
        response = await _new_llm(temperature=0.0).ainvoke(prompt)
        text = str(response.content).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        parsed = json.loads(text)
        intent = parsed.get("intent")
        if intent in {"qa", "data_analysis"}:
            return {
                "intent": intent,
                "confidence": float(parsed.get("confidence", 0.5)),
                "reason": parsed.get("reason", ""),
            }
    except Exception as exc:
        print(f"[DeepChatV2] intent classification failed: {exc}")

    data_keywords = [
        "查询", "统计", "多少", "排名", "趋势", "对比", "同比", "环比", "分布", "下钻",
        "明细", "数据", "表", "指标", "销售额", "数量", "平均", "总计", "top", "topn",
    ]
    is_data = any(keyword.lower() in user_message.lower() for keyword in data_keywords)
    return {
        "intent": "data_analysis" if is_data else "qa",
        "confidence": 0.45,
        "reason": "keyword fallback",
    }


def create_agent_tools(scenario_id: str, context: ConversationContext, query_history: list[dict]):
    oe = get_engine(scenario_id)
    dq = get_query_engine(scenario_id)

    @tool
    def get_ontology_schema(class_id: str = "") -> str:
        """获取本体 schema。class_id 为空时返回实体、关系、指标总览；不为空时返回指定实体详情。"""
        if class_id:
            info = oe.get_class_info(class_id)
            if not info:
                return _json_dumps({"error": f"未找到 class: {class_id}"})
            return _json_dumps({
                "class_id": class_id,
                "name_cn": info.get("name_cn", ""),
                "table_name": info.get("table_name", ""),
                "field_map": info.get("field_map", {}),
                "field_types": info.get("field_types", {}),
                "primary_key": info.get("primary_key", ""),
                "related_classes": oe.get_related_classes(class_id),
            })

        return _json_dumps({
            "classes": [
                {"id": cls.get("id", ""), "name_cn": cls.get("name_cn", ""), "description": cls.get("description", "")}
                for cls in oe.list_classes()
            ],
            "relationships": [
                {
                    "source": rel.get("source", ""),
                    "target": rel.get("target", ""),
                    "type": rel.get("type", ""),
                    "source_key": rel.get("source_key", ""),
                    "target_key": rel.get("target_key", ""),
                }
                for rel in oe.list_relationships()
            ],
            "metrics": oe.list_metrics(),
        })

    @tool
    def query_ontology_data(
        target_class: str,
        metrics: Optional[list] = None,
        dimensions: Optional[list] = None,
        filters: Optional[list] = None,
        join_classes: Optional[list] = None,
        order_by: str = "",
        limit: Optional[int] = None,
        having: Optional[list] = None,
    ) -> str:
        """执行本体数据查询。用于智能问数、聚合、筛选、排序、下钻和图表数据生成。"""
        args = {
            "target_class": target_class,
            "metrics": metrics or [],
            "dimensions": dimensions or [],
            "filters": filters or [],
            "join_classes": join_classes or [],
            "order_by": order_by,
            "limit": limit,
            "having": having or [],
        }
        cache_key = _json_dumps(args)
        if cache_key in context.query_cache:
            return context.query_cache[cache_key]

        result = dq.execute_query(**args)
        result_text = _json_dumps(result)
        context.query_cache[cache_key] = result_text
        if isinstance(result, dict):
            context.last_query_info = result

        if not (isinstance(result, dict) and result.get("error")):
            rows_data = result.get("rows", result) if isinstance(result, dict) else result
            query_history.append({"tool": "query_ontology_data", "args": args, "result": rows_data})
        return result_text

    @tool
    def get_field_types(class_id: str) -> str:
        """获取指定 class 的字段类型，用于构造类型安全的过滤条件。"""
        if not class_id:
            return _json_dumps({"error": "class_id 不能为空"})
        return _json_dumps({"class_id": class_id, "field_types": oe.get_field_types(class_id)})

    @tool
    def get_join_path(source: str, target: str) -> str:
        """获取两个 class 之间的 JOIN 路径。"""
        if not source or not target:
            return _json_dumps({"error": "source 和 target 不能为空"})
        path = oe.get_join_path(source, target)
        return _json_dumps({"source": source, "target": target, "join_path": path, "hops": len(path)})

    @tool
    def fuzzy_search_values(class_id: str, field_name: str, keyword: str, limit: Optional[int] = None) -> str:
        """模糊搜索字段值，用于实体名、客户名、产品名等过滤值消歧。"""
        if not class_id or not field_name or not keyword:
            return _json_dumps({"error": "class_id, field_name, keyword 不能为空"})
        return _json_dumps(dq.fuzzy_search_values(class_id, field_name, keyword, limit or 10))

    @tool
    def get_class_sample(class_id: str, limit: int = 5) -> str:
        """获取指定 class 的样本数据，用于确认字段含义和明细穿透。"""
        if not class_id:
            return _json_dumps({"error": "class_id 不能为空"})
        return _json_dumps(dq.get_class_sample(class_id, limit))

    @tool
    def lookup_metric(metric_name: str) -> str:
        """查询指标定义、口径、推荐维度和图表规则。"""
        return _json_dumps(do_lookup_metric(scenario_id, metric_name) or {"error": "未找到匹配指标"})

    @tool
    def python_analyze(code: str) -> str:
        """对前序查询结果执行 Python 深度分析、清洗、建模或预测。"""
        if not code:
            return _json_dumps({"error": "code 不能为空"})
        all_query_data = _json_dumps(query_history)
        last_result = query_history[-1]["result"] if query_history else []
        data_json = _json_dumps(last_result)
        return _json_dumps(run_python_analyze(code=code, data_json=data_json, all_query_data=all_query_data))

    return [
        get_ontology_schema,
        query_ontology_data,
        get_field_types,
        get_join_path,
        fuzzy_search_values,
        get_class_sample,
        lookup_metric,
        python_analyze,
    ]


def build_deep_agent(system_prompt: str, tools: list, model_temperature: float = 0.3):
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        raise RuntimeError("未安装 deepagents，请先在 backend 环境执行：pip install deepagents") from exc

    model = _new_llm(temperature=model_temperature, streaming=True)
    subagents = [
        {
            "name": "ontology-data-analyst",
            "description": "用于复杂多步骤问数、指标拆解、维度选择、结果校验和分析解释。",
            "system_prompt": (
                "你是本体驱动数据分析子智能体。优先使用 get_ontology_schema、lookup_metric、"
                "query_ontology_data 和 python_analyze 完成复杂分析；返回结论时说明口径、过滤条件和主要数据发现。"
            ),
            "tools": tools,
            "model": model,
        }
    ]
    return create_deep_agent(
        model=model,
        tools=get_system_tools(),
        system_prompt=system_prompt,
        subagents=subagents,
    )


async def _emit_action_events(scenario_id: str, final_text: str):
    try:
        matched_actions = await find_matching_actions(scenario_id, final_text)
        for action in matched_actions:
            yield f"data: {_json_dumps({'type': 'action_confirm', 'data': {'action_id': action['id'], 'action_name': action['name'], 'description': action.get('description', ''), 'action_type': action.get('action_type', 'notification'), 'requires_confirm': action.get('requires_confirm', True), 'message': action.get('message', '')}})}\n\n"
            break
    except Exception as exc:
        print(f"[DeepChatV2] action matching failed: {exc}")


def _extract_event_content(event: dict) -> str:
    data = event.get("data", {})
    chunk = data.get("chunk") or data.get("output")
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "text_delta"}:
                pieces.append(str(item.get("text", "")))
            elif isinstance(item, str):
                pieces.append(item)
        return "".join(pieces)
    return str(content) if content else ""


def _extract_tool_output(output: Any) -> Any:
    content = getattr(output, "content", output)
    return _try_json_loads(content)


async def _dispatch_card_events(parsed_result: Any):
    if isinstance(parsed_result, dict):
        if parsed_result.get("type") == "clarification_needed":
            yield f"data: {_json_dumps({'type': 'clarification', 'data': parsed_result})}\n\n"
        elif parsed_result.get("type") == "drill_down_result":
            yield f"data: {_json_dumps({'type': 'drilldown', 'data': parsed_result})}\n\n"
        elif parsed_result.get("type") == "query_result" and parsed_result.get("row_count", 0) > 0:
            yield f"data: {_json_dumps({'type': 'chart_data', 'data': parsed_result})}\n\n"


async def qa_stream(req: ChatRequest, intent: dict):
    scenario_id = req.scenario_id
    user_message = _last_user_message(req)
    init_prompt(scenario_id)

    system_prompt = (
        get_system_prompt(scenario_id)
        + await _build_context_notes(scenario_id, user_message)
        + "\n\n[回答模式]\n当前意图为普通问答。请基于系统上下文和历史对话直接回答；除非用户明确要求查数，否则不要调用数据分析工具。"
    )
    messages = [SystemMessage(content=system_prompt)] + _to_langchain_messages(_request_messages(req))

    yield f"data: {_json_dumps({'type': 'intent', 'intent': intent})}\n\n"

    final_text = ""
    async for chunk in _new_llm(temperature=0.5, streaming=True).astream(messages):
        content = chunk.content
        if content:
            final_text += str(content)
            yield f"data: {_json_dumps({'type': 'text', 'content': content})}\n\n"

    if final_text:
        async for event in _emit_action_events(scenario_id, final_text):
            yield event

    yield f"data: {_json_dumps({'type': 'done', 'tool_results': []})}\n\n"


async def deep_agent_stream(req: ChatRequest, intent: dict):
    scenario_id = req.scenario_id
    user_message = _last_user_message(req)
    init_prompt(scenario_id)

    context = get_or_create_context(req.conversation_id)
    query_history: list[dict] = []
    tools = create_agent_tools(scenario_id, context, query_history)
    system_prompt = (
        get_system_prompt(scenario_id)
        + await _build_context_notes(scenario_id, user_message)
        + "\n\n[Deep Agent 执行约束]\n"
        + "你正在处理智能问数问题。请使用 Deep Agents 的 write_todos 做任务规划，必要时用 task 委派复杂子任务。"
        + "优先通过本体 schema、指标定义、字段类型和 JOIN 路径理解数据语义，再调用 query_ontology_data。"
        + "构造过滤条件时必须包含 operator；不确定字段或类型时先查 schema/field_types；结果不足时主动下钻、消歧或改写查询。"
        + "最终回答必须包含口径、关键数据和可行动洞察。"
    )
    agent = build_deep_agent(system_prompt, tools)

    agent_input = {"messages": _request_messages(req)}
    config = {
        "configurable": {"thread_id": req.conversation_id or str(uuid.uuid4())},
        "recursion_limit": DEEP_AGENT_RECURSION_LIMIT,
    }

    accumulated_plan_steps: list[dict] = []
    all_tool_results: list[dict] = []
    running_step_by_run_id: dict[str, int] = {}
    final_text = ""

    yield f"data: {_json_dumps({'type': 'intent', 'intent': intent})}\n\n"

    event_stream = agent.astream_events(agent_input, config=config, version="v2")
    if not hasattr(event_stream, "__aiter__"):
        event_stream = await event_stream

    async for event in event_stream:
        kind = event.get("event")
        name = str(event.get("name", ""))
        run_id = str(event.get("run_id", ""))

        if kind == "on_tool_start":
            tool_input = event.get("data", {}).get("input", {})
            step = {
                "step_id": f"step_{len(accumulated_plan_steps)}",
                "tool": TOOL_NAME_MAP.get(name, name),
                "description": "Deep Agent 正在调度工具...",
                "tool_args": tool_input,
                "status": "running",
                "result": None,
            }
            accumulated_plan_steps.append(step)
            if run_id:
                running_step_by_run_id[run_id] = len(accumulated_plan_steps) - 1

            yield f"data: {_json_dumps({'type': 'tool', 'name': TOOL_NAME_MAP.get(name, name), 'arguments': tool_input})}\n\n"
            yield f"data: {_json_dumps({'type': 'plan', 'title': 'Deep Agents Plan-and-Execute', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

        elif kind == "on_tool_end":
            parsed_result = _extract_tool_output(event.get("data", {}).get("output"))
            step_idx = running_step_by_run_id.get(run_id)
            if step_idx is None:
                for idx in range(len(accumulated_plan_steps) - 1, -1, -1):
                    if accumulated_plan_steps[idx]["status"] == "running":
                        step_idx = idx
                        break

            if step_idx is not None:
                accumulated_plan_steps[step_idx]["status"] = "completed"
                accumulated_plan_steps[step_idx]["description"] = "工具结果已返回，进入综合判断"
                accumulated_plan_steps[step_idx]["result"] = parsed_result

            all_tool_results.append({"tool": name, "result": parsed_result})
            result_preview = _json_dumps(parsed_result)
            yield f"data: {_json_dumps({'type': 'tool_result', 'name': TOOL_NAME_MAP.get(name, name), 'result_preview': result_preview})}\n\n"
            yield f"data: {_json_dumps({'type': 'plan', 'title': 'Deep Agents Plan-and-Execute', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

            async for card_event in _dispatch_card_events(parsed_result):
                yield card_event

        elif kind == "on_chat_model_stream":
            content = _extract_event_content(event)
            if content:
                final_text += content
                yield f"data: {_json_dumps({'type': 'text', 'content': content})}\n\n"

    for step in accumulated_plan_steps:
        if step["status"] != "completed":
            step["status"] = "completed"

    yield f"data: {_json_dumps({'type': 'plan', 'title': 'Deep Agents Plan-and-Execute', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

    if final_text:
        async for event in _emit_action_events(scenario_id, final_text):
            yield event

    yield f"data: {_json_dumps({'type': 'done', 'tool_results': all_tool_results})}\n\n"


async def chat_stream_generator(req: ChatRequest):
    try:
        intent = await understand_intent(req)
        if intent["intent"] == "data_analysis":
            async for event in deep_agent_stream(req, intent):
                yield event
        else:
            async for event in qa_stream(req, intent):
                yield event
    except Exception as exc:
        import traceback
        traceback.print_exc()
        yield f"data: {_json_dumps({'type': 'error', 'content': str(exc)})}\n\n"


@router.post("/api/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        chat_stream_generator(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
