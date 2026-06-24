"""
Deep Chat API - Intent Router + LangChain DeepAgent
===================================================
独立于 modules/chat.py 的新版 /api/chat 实现：
  - 先做意图理解：普通问答 vs 智能问数
  - 普通问答：RAG 上下文 + LLM 流式回答
  - 智能问数：LangGraph DeepAgent（Plan / Tool / Reflection / Answer）
"""

import json
import operator
from typing import Annotated, Any, Literal, Optional
from typing_extensions import TypedDict

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from configs.global_config import Cfg, client
from modules.actions import find_matching_actions
from modules.glossary import match_glossary_terms
from modules.metrics import lookup_metric as do_lookup_metric
from modules.models import ChatRequest
from modules.skills import route_skills
from prompts.prompt import get_engine, get_query_engine, get_system_prompt, get_system_tools, init_prompt
from tools.python_analyize import python_analyze as run_python_analyze


router = APIRouter()
MAX_AGENT_ROUNDS = 20
GRAPH_RECURSION_LIMIT = MAX_AGENT_ROUNDS * 4 + 10

TOOL_NAME_MAP = {
    "get_ontology_schema": "模型结构分析",
    "query_ontology_data": "多维模型高阶查询",
    "get_field_types": "字段类型校验",
    "get_join_path": "关系拓扑推导",
    "fuzzy_search_values": "实体语义消歧",
    "get_class_sample": "明细数据穿透",
    "lookup_metric": "指标定义检索",
    "python_analyze": "深度策略分析(Python)",
}


class DeepAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    scenario_id: str
    current_round: int
    reflection_logs: Annotated[list[str], operator.add]


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


def _last_user_message(req: ChatRequest) -> str:
    for message in reversed(req.messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _to_langchain_messages(messages: list[dict]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
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


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _try_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


async def _build_context_notes(scenario_id: str, user_message: str) -> str:
    notes = ""
    if not user_message or not scenario_id:
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
            notes += f"- {skill['name']}: {skill['content'][:500]}\n"

    return notes


async def understand_intent(req: ChatRequest) -> dict:
    """区分普通问答与智能问数。失败时使用保守关键词兜底。"""
    user_message = _last_user_message(req)
    if not user_message:
        return {"intent": "qa", "confidence": 0.0, "reason": "empty message"}

    llm = _new_llm(temperature=0.0)
    tools_summary = _json_dumps(get_system_tools())[:3000]
    prompt = [
        SystemMessage(content=(
            "你是一个意图分类器，只输出 JSON。"
            "将用户意图分类为 qa 或 data_analysis。"
            "data_analysis 表示需要查数、聚合、筛选、排序、下钻、指标计算、图表数据、Python分析或调用数据工具。"
            "qa 表示普通知识问答、概念解释、系统使用说明、闲聊或无需实时查询数据的问题。"
            "输出格式：{\"intent\":\"qa|data_analysis\",\"confidence\":0-1,\"reason\":\"...\"}。"
        )),
        HumanMessage(content=f"可用数据工具摘要：{tools_summary}\n\n用户问题：{user_message}"),
    ]

    try:
        response = await llm.ainvoke(prompt)
        text = str(response.content).strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.removeprefix("json").strip()
        parsed = json.loads(text)
        intent = parsed.get("intent")
        if intent in {"qa", "data_analysis"}:
            return {
                "intent": intent,
                "confidence": float(parsed.get("confidence", 0.5)),
                "reason": parsed.get("reason", ""),
            }
    except Exception as exc:
        print(f"[IntentRouter] LLM classification failed: {exc}")

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


def create_agent_tools(scenario_id: str, query_history: list[dict]):
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
        result = dq.execute_query(
            target_class=target_class,
            metrics=metrics or [],
            dimensions=dimensions or [],
            filters=filters or [],
            join_classes=join_classes or [],
            order_by=order_by,
            limit=limit,
            having=having or [],
        )
        rows_data = result.get("rows", result) if isinstance(result, dict) else result
        if not (isinstance(result, dict) and result.get("error")):
            query_history.append({
                "tool": "query_ontology_data",
                "args": {
                    "target_class": target_class,
                    "metrics": metrics or [],
                    "dimensions": dimensions or [],
                    "filters": filters or [],
                    "join_classes": join_classes or [],
                    "order_by": order_by,
                    "limit": limit,
                    "having": having or [],
                },
                "result": rows_data,
            })
        return _json_dumps(result)

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
        return _json_dumps(dq.fuzzy_search_values(class_id, field_name, keyword, limit))

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


def build_deep_agent_graph(scenario_id: str, query_history: list[dict]):
    llm = _new_llm(temperature=0.3, streaming=True)
    reflection_llm = _new_llm(temperature=0.1, streaming=False)
    tools = create_agent_tools(scenario_id, query_history)

    async def call_model(state: DeepAgentState):
        messages = state["messages"]
        if len(messages) > 28:
            messages = [messages[0]] + messages[-22:]
        response = await llm.bind_tools(tools).ainvoke(messages)
        return {"messages": [response], "current_round": state["current_round"] + 1}

    async def reflect_on_results(state: DeepAgentState):
        prompt = SystemMessage(content=(
            "你是 DeepAgent 的反思节点。请审视最近工具结果，只输出一到两句内部修正建议。"
            "重点判断：数据是否为空或报错、是否足以回答用户、下一步是否需要补查/下钻/改写查询/Python分析。"
            "不要直接回答用户。"
        ))
        response = await reflection_llm.ainvoke([prompt] + state["messages"][-6:])
        reflection_text = str(response.content)
        return {
            "messages": [SystemMessage(content=f"[DeepAgent反思]: {reflection_text}")],
            "reflection_logs": [reflection_text],
        }

    def should_continue(state: DeepAgentState) -> Literal["tools", "finalize"]:
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None) and state["current_round"] < MAX_AGENT_ROUNDS:
            return "tools"
        return "finalize"

    workflow = StateGraph(DeepAgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("reflection", reflect_on_results)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "finalize": END})
    workflow.add_edge("tools", "reflection")
    workflow.add_edge("reflection", "agent")
    return workflow.compile()


async def _emit_action_events(scenario_id: str, final_text: str):
    try:
        matched_actions = await find_matching_actions(scenario_id, final_text)
        for action in matched_actions:
            yield f"data: {_json_dumps({'type': 'action_confirm', 'data': {'action_id': action['id'], 'action_name': action['name'], 'description': action.get('description', ''), 'action_type': action.get('action_type', 'notification'), 'requires_confirm': action.get('requires_confirm', True), 'message': action.get('message', '')}})}\n\n"
            break
    except Exception as exc:
        print(f"[DeepChat] action matching failed: {exc}")


async def rag_llm_stream(req: ChatRequest, intent: dict):
    scenario_id = req.scenario_id
    user_message = _last_user_message(req)
    init_prompt(scenario_id)

    context_notes = await _build_context_notes(scenario_id, user_message)
    rag_prompt = (
        get_system_prompt(scenario_id)
        + context_notes
        + "\n\n[回答模式]\n当前意图为普通问答。请基于上述 RAG 上下文和历史对话直接回答；"
        + "除非用户明确要求查数，否则不要调用数据分析流程。"
    )
    messages = [SystemMessage(content=rag_prompt)] + _to_langchain_messages(req.messages)

    yield f"data: {_json_dumps({'type': 'intent', 'intent': intent})}\n\n"

    llm = _new_llm(temperature=0.5, streaming=True)
    final_text = ""
    async for chunk in llm.astream(messages):
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

    context_notes = await _build_context_notes(scenario_id, user_message)
    deep_agent_prompt = (
        get_system_prompt(scenario_id)
        + context_notes
        + "\n\n[DeepAgent执行约束]\n"
        + "你正在处理智能问数问题。请按 Plan-and-Execute 思路调用工具获取数据，"
        + "必要时下钻、消歧、校验字段类型，并在工具结果充分后给出清晰结论。"
        + "构造过滤条件时必须包含 operator；不确定字段或类型时先查 schema/field_types。"
    )
    initial_messages = [SystemMessage(content=deep_agent_prompt)] + _to_langchain_messages(req.messages)
    query_history: list[dict] = []
    graph = build_deep_agent_graph(scenario_id, query_history)

    current_state: DeepAgentState = {
        "messages": initial_messages,
        "scenario_id": scenario_id,
        "current_round": 0,
        "reflection_logs": [],
    }
    accumulated_plan_steps: list[dict] = []
    all_tool_results: list[dict] = []
    final_text = ""
    running_step_by_call_id: dict[str, int] = {}

    yield f"data: {_json_dumps({'type': 'intent', 'intent': intent})}\n\n"
    print(f"data: {_json_dumps({'type': 'intent', 'intent': intent})}")

    async for event in graph.astream_events(
        current_state,
        config={"recursion_limit": GRAPH_RECURSION_LIMIT},
        version="v2",
    ):
        kind = event.get("event")
        name = event.get("name")
        node_name = event.get("metadata", {}).get("langgraph_node")

        if kind == "on_chat_model_end" and node_name == "agent":
            output_msg = event.get("data", {}).get("output")
            tool_calls = getattr(output_msg, "tool_calls", None) or []
            for tool_call in tool_calls:
                fn_name = tool_call.get("name", "")
                fn_args = tool_call.get("args", {})
                call_id = tool_call.get("id", "")
                step = {
                    "step_id": f"step_{len(accumulated_plan_steps)}",
                    "tool": TOOL_NAME_MAP.get(fn_name, fn_name),
                    "description": "DeepAgent 正在调度本体工具...",
                    "tool_args": fn_args,
                    "status": "running",
                    "result": None,
                }
                accumulated_plan_steps.append(step)
                if call_id:
                    running_step_by_call_id[call_id] = len(accumulated_plan_steps) - 1
                yield f"data: {_json_dumps({'type': 'tool', 'name': TOOL_NAME_MAP.get(fn_name, fn_name), 'arguments': fn_args})}\n\n"
                yield f"data: {_json_dumps({'type': 'plan', 'title': 'LangChain DeepAgent 认知循环', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

        elif kind == "on_tool_end":
            output = event.get("data", {}).get("output")
            call_id = getattr(output, "tool_call_id", "") or event.get("run_id", "")
            content = getattr(output, "content", output)
            parsed_result = _try_json_loads(content)
            step_idx = running_step_by_call_id.get(call_id)

            if step_idx is None:
                for idx in range(len(accumulated_plan_steps) - 1, -1, -1):
                    if accumulated_plan_steps[idx]["status"] == "running":
                        step_idx = idx
                        break

            if step_idx is not None:
                accumulated_plan_steps[step_idx]["status"] = "completed"
                accumulated_plan_steps[step_idx]["description"] = "工具结果已返回，进入反思校验"
                accumulated_plan_steps[step_idx]["result"] = parsed_result

            all_tool_results.append({"tool": name, "result": parsed_result})
            result_preview = _json_dumps(parsed_result)
            yield f"data: {_json_dumps({'type': 'tool_result', 'name': TOOL_NAME_MAP.get(str(name), str(name)), 'result_preview': result_preview})}\n\n"
            yield f"data: {_json_dumps({'type': 'plan', 'title': 'LangChain DeepAgent 认知循环', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

            if isinstance(parsed_result, dict):
                if parsed_result.get("type") == "clarification_needed":
                    yield f"data: {_json_dumps({'type': 'clarification', 'data': parsed_result})}\n\n"
                elif parsed_result.get("type") == "drill_down_result":
                    yield f"data: {_json_dumps({'type': 'drilldown', 'data': parsed_result})}\n\n"
                elif parsed_result.get("type") == "query_result" and parsed_result.get("row_count", 0) > 0:
                    yield f"data: {_json_dumps({'type': 'chart_data', 'data': parsed_result})}\n\n"

        elif kind == "on_chat_model_end" and node_name == "reflection":
            reflection = event.get("data", {}).get("output")
            reflection_content = getattr(reflection, "content", "")
            if reflection_content:
                yield f"data: {_json_dumps({'type': 'reflection', 'content': reflection_content})}\n\n"

        elif kind == "on_chat_model_stream" and node_name == "agent":
            chunk = event.get("data", {}).get("chunk")
            content = getattr(chunk, "content", "")
            if content:
                final_text += str(content)
                yield f"data: {_json_dumps({'type': 'text', 'content': content})}\n\n"

    for step in accumulated_plan_steps:
        if step["status"] != "completed":
            step["status"] = "completed"

    yield f"data: {_json_dumps({'type': 'plan', 'title': 'LangChain DeepAgent 认知循环', 'steps': accumulated_plan_steps, 'current_step': len(accumulated_plan_steps), 'total_steps': len(accumulated_plan_steps)})}\n\n"

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
            async for event in rag_llm_stream(req, intent):
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
