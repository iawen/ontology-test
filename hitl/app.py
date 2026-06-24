"""
LangChain Deep Agents Human-in-the-Loop 示例 - 后端 API
========================================================
演示三种 HITL 模式：
  1. 工具审批（HumanInTheLoopMiddleware 风格）：approve / edit / reject
  2. 澄清提问（ask_user 风格）：自由文本 / 多选题
  3. 复杂表单（自定义 interrupt）：时间范围选择器等

核心机制：
  - interrupt(value)  → 暂停执行，保存 checkpoint
  - Command(resume=decision) → 用户决策后恢复执行
  - checkpointer + thread_id → 持久化会话状态

运行：
  pip install -r requirements.txt
  export OPENAI_API_KEY=sk-xxx
  uvicorn app:app --reload --port 8000
"""

import json
import uuid
import os
from datetime import datetime
from typing import Any, Optional, Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────
# LangGraph 核心导入
# ──────────────────────────────────────────────────────────
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

# ──────────────────────────────────────────────────────────
# SQLite Checkpointer（持久化会话状态，支持中断恢复）
# ──────────────────────────────────────────────────────────
import sqlite3

DB_PATH = str(Path(__file__).parent / "checkpoints.db")
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(_conn)

# ──────────────────────────────────────────────────────────
# LLM 配置
# ──────────────────────────────────────────────────────────
llm = ChatOpenAI(
    model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=os.getenv("OPENAI_BASE_URL", None),
    temperature=0.7,
)

# ============================================================
# 模式一：工具审批型 HITL（HumanInTheLoopMiddleware 风格）
# ============================================================
# 模拟一个"发送邮件"工具，执行前需要人工审批

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件给指定收件人。此工具需要人工审批后才会执行。"""
    # ⚠️ 关键：在执行实际操作前，调用 interrupt() 暂停等待人工审批
    # interrupt 的参数会传递给前端，前端据此渲染审批卡片
    approval_request = {
        "type": "tool_approval",
        "tool_name": "send_email",
        "description": f"即将发送邮件给 {to}",
        "action": {
            "name": "send_email",
            "args": {"to": to, "subject": subject, "body": body},
        },
        "allowed_decisions": ["approve", "edit", "reject"],
    }

    # interrupt() 会暂停图执行，返回值是用户 resume 时提供的 decision
    decision = interrupt(approval_request)

    # 根据 human 的决策执行
    if decision["type"] == "approve":
        # 审批通过，执行实际操作
        return f"✅ 邮件已发送给 {to}，主题：{subject}"
    elif decision["type"] == "edit":
        # 用户编辑了参数，使用编辑后的参数执行
        edited = decision["edited_action"]["args"]
        return f"✅ 邮件已发送（编辑后）给 {edited['to']}，主题：{edited['subject']}"
    elif decision["type"] == "reject":
        # 用户拒绝，返回拒绝原因给 LLM
        return f"❌ 邮件发送被拒绝：{decision.get('message', '用户未提供原因')}"
    else:
        return f"⚠️ 未知决策类型：{decision['type']}"


@tool
def query_database(sql: str) -> str:
    """查询数据库（模拟）。此工具需要人工审批。"""
    approval_request = {
        "type": "tool_approval",
        "tool_name": "query_database",
        "description": f"即将执行 SQL 查询",
        "action": {"name": "query_database", "args": {"sql": sql}},
        "allowed_decisions": ["approve", "edit", "reject"],
    }
    decision = interrupt(approval_request)

    if decision["type"] == "approve":
        return f"✅ 查询已执行：{sql}\n结果：模拟返回 42 行数据"
    elif decision["type"] == "edit":
        return f"✅ 查询已执行（编辑后）：{decision['edited_action']['args']['sql']}"
    else:
        return f"❌ 查询被拒绝：{decision.get('message', '')}"


# ============================================================
# 模式二：澄清提问型 HITL（ask_user 风格）
# ============================================================
# Agent 主动向用户提问，支持自由文本和多选题

def ask_user_question(
    question: str,
    options: Optional[list[str]] = None,
    allow_multiple: bool = False,
) -> str:
    """
    向用户提问并等待回答。
    - options 为 None：自由文本输入
    - options 不为 None：多选题（单选/多选）
    """
    ask_request = {
        "type": "ask_user",
        "question": question,
        "options": options,
        "allow_multiple": allow_multiple,
    }
    # interrupt 暂停，用户回答后 resume 值作为返回值
    answer = interrupt(ask_request)
    return answer


# ============================================================
# 模式三：复杂表单型 HITL（自定义 interrupt + 结构化 schema）
# ============================================================
# 例如：时间范围选择器、多字段表单等

def ask_time_range(
    prompt: str = "请选择时间范围",
    default_start: str = "",
    default_end: str = "",
) -> dict:
    """
    向用户展示时间范围选择器。
    前端根据 schema 渲染日期选择器 UI。
    """
    form_request = {
        "type": "form",
        "form_kind": "time_range",
        "prompt": prompt,
        "schema": {
            "start_date": {"type": "date", "label": "开始日期", "default": default_start, "required": True},
            "end_date": {"type": "date", "label": "结束日期", "default": default_end, "required": True},
            "granularity": {
                "type": "select",
                "label": "时间粒度",
                "options": ["日", "周", "月", "季", "年"],
                "default": "日",
                "required": True,
            },
        },
    }
    result = interrupt(form_request)
    return result


def ask_store_selector(stores: list[str]) -> list[str]:
    """复杂表单：门店多选器"""
    form_request = {
        "type": "form",
        "form_kind": "multi_select",
        "prompt": "请选择要分析的门店（可多选）",
        "schema": {
            "selected": {
                "type": "multi_select",
                "label": "门店列表",
                "options": stores,
                "required": True,
            }
        },
    }
    result = interrupt(form_request)
    return result.get("selected", [])


# ============================================================
# Agent State 定义
# ============================================================

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    # 记录 HITL 交互历史
    hitl_history: list[dict]


# ============================================================
# Agent 节点：LLM 决策节点
# ============================================================

SYSTEM_PROMPT = """你是一个智能数据分析助手。你可以使用以下工具：

1. send_email(to, subject, body) - 发送邮件（需要人工审批）
2. query_database(sql) - 查询数据库（需要人工审批）

此外，当你需要向用户澄清问题时，你可以直接在回复中说明需要澄清，
系统会自动触发提问流程。

当用户的请求中缺少关键信息（如时间范围、门店选择等）时，
请明确告诉用户需要提供什么信息。"""

tools = [send_email, query_database]
llm_with_tools = llm.bind_tools(tools)


def agent_node(state: AgentState) -> AgentState:
    """LLM 决策节点：分析用户输入，决定调用哪个工具"""
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)

    return {"messages": [response]}


# ============================================================
# Agent 节点：工具执行节点（含 HITL interrupt）
# ============================================================

def tool_node(state: AgentState) -> AgentState:
    """工具执行节点：执行 LLM 请求的工具调用（含 HITL 审批）"""
    last_message = state["messages"][-1]
    results = []

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        if tool_name == "send_email":
            # send_email 内部会调用 interrupt() 暂停等待审批
            result = send_email.invoke(tool_args)
        elif tool_name == "query_database":
            result = query_database.invoke(tool_args)
        else:
            result = f"未知工具：{tool_name}"

        results.append(
            ToolMessage(content=str(result), tool_call_id=tool_call["id"])
        )

    return {"messages": results}


# ============================================================
# 路由函数：判断是否需要调用工具
# ============================================================

def should_use_tools(state: AgentState) -> str:
    """判断下一步：调用工具 or 结束"""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


# ============================================================
# 构建 Agent Graph
# ============================================================

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_use_tools, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")  # 工具执行完回到 agent 继续判断

# 编译图，注入 checkpointer（HITL 必需）
app_graph = workflow.compile(checkpointer=checkpointer)


# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(title="Deep Agent HITL Demo", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（前端 UI）
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent)), name="static")


# ──────────────────────────────────────────────────────────
# 请求/响应模型
# ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class ResumeRequest(BaseModel):
    """用户恢复执行的请求"""
    thread_id: str
    decision: dict = Field(..., description="用户的决策（approve/edit/reject/respond/form_result）")


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "interrupted"] 
    messages: list[dict] = []
    interrupt: Optional[dict] = None


# ──────────────────────────────────────────────────────────
# 辅助函数：提取 interrupt 信息
# ──────────────────────────────────────────────────────────

def extract_interrupt_info(interrupt_value: Any) -> dict:
    """从 interrupt 值中提取前端需要的信息"""
    if isinstance(interrupt_value, dict):
        return interrupt_value
    return {"type": "unknown", "value": str(interrupt_value)}


def messages_to_dict(messages: list) -> list[dict]:
    """将 LangChain messages 转为前端可用的 dict"""
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            content = msg.content
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_names = [tc["name"] for tc in msg.tool_calls]
                content += f"\n[调用工具: {', '.join(tool_names)}]"
            result.append({"role": "assistant", "content": content})
        elif isinstance(msg, ToolMessage):
            result.append({"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id})
        else:
            result.append({"role": "unknown", "content": str(msg)})
    return result


# ──────────────────────────────────────────────────────────
# API 接口
# ──────────────────────────────────────────────────────────

@app.get("/")
async def index():
    """返回前端 Chat Bot UI"""
    return FileResponse(str(Path(__file__).parent / "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    发送消息给 Agent。
    
    返回值：
      - status="completed"：Agent 已完成，messages 包含最终回复
      - status="interrupted"：Agent 被中断等待人工输入，interrupt 包含审批/提问信息
    """
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # 添加用户消息
    user_msg = HumanMessage(content=req.message)

    try:
        # 运行 Agent 图
        result = app_graph.invoke(
            {"messages": [user_msg], "hitl_history": []},
            config=config,
        )

        # 检查是否被 interrupt 中断
        # LangGraph 在 interrupt 时会抛出特定状态，通过 get_state 检查
        state = app_graph.get_state(config)
        
        if state.next:  # 还有未执行的节点 → 被 interrupt 了
            # 提取 interrupt 信息
            interrupt_info = None
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        interrupt_info = extract_interrupt_info(task.interrupts[0].value)
                        break

            return ChatResponse(
                thread_id=thread_id,
                status="interrupted",
                messages=messages_to_dict(result.get("messages", [])),
                interrupt=interrupt_info,
            )
        else:
            # 正常完成
            return ChatResponse(
                thread_id=thread_id,
                status="completed",
                messages=messages_to_dict(result.get("messages", [])),
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 执行失败: {str(e)}")


@app.post("/api/chat/resume", response_model=ChatResponse)
async def resume_chat(req: ResumeRequest):
    """
    用户做出决策后，恢复 Agent 执行。
    
    decision 格式：
      - 工具审批：{"type": "approve"} 或 {"type": "edit", "edited_action": {...}} 或 {"type": "reject", "message": "..."}
      - 澄清提问：{"type": "respond", "message": "用户的回答"}
      - 复杂表单：{"type": "form_result", "values": {...}}
    """
    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        # 使用 Command(resume=...) 恢复执行
        # resume 的值会成为 interrupt() 的返回值
        resume_value = req.decision.get("values", req.decision)

        result = app_graph.invoke(
            Command(resume=resume_value),
            config=config,
        )

        state = app_graph.get_state(config)

        if state.next:
            # 可能又触发了新的 interrupt（多轮 HITL）
            interrupt_info = None
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        interrupt_info = extract_interrupt_info(task.interrupts[0].value)
                        break

            return ChatResponse(
                thread_id=req.thread_id,
                status="interrupted",
                messages=messages_to_dict(result.get("messages", [])) if isinstance(result, dict) else [],
                interrupt=interrupt_info,
            )
        else:
            return ChatResponse(
                thread_id=req.thread_id,
                status="completed",
                messages=messages_to_dict(result.get("messages", [])) if isinstance(result, dict) else [],
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复执行失败: {str(e)}")


@app.get("/api/state/{thread_id}")
async def get_state(thread_id: str):
    """获取指定会话的当前状态（调试用）"""
    config = {"configurable": {"thread_id": thread_id}}
    state = app_graph.get_state(config)
    return {
        "thread_id": thread_id,
        "next": state.next,
        "values": {
            "messages": messages_to_dict(state.values.get("messages", [])),
        },
    }


@app.delete("/api/chat/{thread_id}")
async def clear_chat(thread_id: str):
    """清除指定会话"""
    # SQLite checkpointer 的清除方式
    _conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
    _conn.commit()
    return {"status": "ok", "message": f"会话 {thread_id} 已清除"}


# ============================================================
# 独立的复杂 HITL 演示端点（不经过 LLM，直接演示表单交互）
# ============================================================

class TimeRangeRequest(BaseModel):
    """时间范围请求"""
    thread_id: Optional[str] = None
    prompt: str = "请选择要分析的时间范围"


@app.post("/api/hitl/time-range")
async def start_time_range(req: TimeRangeRequest):
    """
    演示复杂 HITL：启动一个时间范围选择流程。
    Agent 需要 user 指定时间范围才能继续分析。
    """
    thread_id = req.thread_id or str(uuid.uuid4())

    # 构建一个简单的图：只包含一个 interrupt 节点
    def ask_time_node(state):
        result = ask_time_range(req.prompt)
        return {"time_range": result, "messages": [AIMessage(content=f"已选择时间范围：{result['start_date']} 至 {result['end_date']}，粒度：{result['granularity']}")]}

    simple_state = TypedDict("SimpleState", {"time_range": dict, "messages": list})
    simple_graph = StateGraph(simple_state)
    simple_graph.add_node("ask", ask_time_node)
    simple_graph.set_entry_point("ask")
    simple_graph.add_edge("ask", END)
    simple_compiled = simple_graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": thread_id}}
    result = simple_compiled.invoke({}, config=config)

    state = simple_compiled.get_state(config)
    if state.next:
        interrupt_info = None
        if state.tasks:
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupt_info = extract_interrupt_info(task.interrupts[0].value)
                    break
        return {
            "thread_id": thread_id,
            "status": "interrupted",
            "interrupt": interrupt_info,
        }
    else:
        return {
            "thread_id": thread_id,
            "status": "completed",
            "result": result.get("time_range"),
        }


@app.post("/api/hitl/time-range/resume")
async def resume_time_range(req: ResumeRequest):
    """恢复时间范围选择流程"""
    config = {"configurable": {"thread_id": req.thread_id}}

    # 重新构建相同的图来恢复
    def ask_time_node(state):
        result = ask_time_range("请选择要分析的时间范围")
        return {"time_range": result}

    simple_state = TypedDict("SimpleState", {"time_range": dict, "messages": list})
    simple_graph = StateGraph(simple_state)
    simple_graph.add_node("ask", ask_time_node)
    simple_graph.set_entry_point("ask")
    simple_graph.add_edge("ask", END)
    simple_compiled = simple_graph.compile(checkpointer=checkpointer)

    result = simple_compiled.invoke(Command(resume=req.decision.get("values", req.decision)), config=config)

    return {
        "thread_id": req.thread_id,
        "status": "completed",
        "result": result.get("time_range"),
    }


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  Deep Agent HITL Demo")
    print("  访问 http://localhost:8000 打开 Chat Bot UI")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
