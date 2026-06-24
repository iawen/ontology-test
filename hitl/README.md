# LangChain Deep Agents Human-in-the-Loop (HITL) 示例

## 📋 概述

本项目演示了 LangChain Deep Agents 中 Human-in-the-Loop 的三种实现模式，包含完整的后端 API（FastAPI + LangGraph）和前端 Chat Bot UI。

## 🏗️ 架构

```
┌─────────────────┐     HTTP/SSE      ┌──────────────────┐
│  Chat Bot UI    │ ←──────────────→  │  FastAPI 后端     │
│  (index.html)   │                   │  (app.py)        │
└─────────────────┘                   └────────┬─────────┘
                                               │
                                      ┌────────▼─────────┐
                                      │  LangGraph Agent │
                                      │  + interrupt()   │
                                      │  + checkpointer  │
                                      └────────┬─────────┘
                                               │
                                      ┌────────▼─────────┐
                                      │  SQLite 持久化    │
                                      │  (checkpoints.db)│
                                      └──────────────────┘
```

## 🔧 三种 HITL 模式

### 模式一：工具审批（HumanInTheLoopMiddleware 风格）

**场景**：Agent 要执行敏感操作（发邮件、执行SQL），需要人工审批。

**实现**：在工具函数内部调用 `interrupt()`，暂停执行等待用户决策。

```python
@tool
def send_email(to, subject, body):
    # interrupt 暂停，等待用户审批
    decision = interrupt({
        "type": "tool_approval",
        "tool_name": "send_email",
        "action": {"name": "send_email", "args": {...}},
        "allowed_decisions": ["approve", "edit", "reject"],
    })
    
    if decision["type"] == "approve":
        return "邮件已发送"
    elif decision["type"] == "edit":
        edited = decision["edited_action"]["args"]
        return f"邮件已发送（编辑后）给 {edited['to']}"
    elif decision["type"] == "reject":
        return f"邮件发送被拒绝：{decision.get('message')}"
```

**用户决策类型**：
| 决策 | 说明 | decision 格式 |
|------|------|--------------|
| approve | 批准执行 | `{"type": "approve"}` |
| edit | 编辑参数后执行 | `{"type": "edit", "edited_action": {"name": "...", "args": {...}}}` |
| reject | 拒绝执行 | `{"type": "reject", "message": "拒绝原因"}` |

### 模式二：澄清提问（ask_user 风格）

**场景**：Agent 需要向用户提问以获取缺失信息。

**实现**：通过 `interrupt()` 传递问题，前端渲染提问卡片。

```python
def ask_user_question(question, options=None, allow_multiple=False):
    answer = interrupt({
        "type": "ask_user",
        "question": question,
        "options": options,           # None=自由文本, list=选择题
        "allow_multiple": allow_multiple,
    })
    return answer  # 用户回答作为返回值
```

### 模式三：复杂表单（自定义 interrupt + 结构化 schema）

**场景**：需要用户填写复杂表单（如时间范围选择器、多选门店等）。

**实现**：通过 `interrupt()` 传递表单 schema，前端根据 schema 动态渲染 UI。

```python
def ask_time_range(prompt="请选择时间范围"):
    result = interrupt({
        "type": "form",
        "form_kind": "time_range",
        "prompt": prompt,
        "schema": {
            "start_date": {
                "type": "date",
                "label": "开始日期",
                "required": True,
            },
            "end_date": {
                "type": "date", 
                "label": "结束日期",
                "required": True,
            },
            "granularity": {
                "type": "select",
                "label": "时间粒度",
                "options": ["日", "周", "月", "季", "年"],
                "default": "日",
            },
        },
    })
    return result  # {"start_date": "2024-01-01", "end_date": "2024-06-30", "granularity": "月"}
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd hitl_demo
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# .env 文件
OPENAI_API_KEY=sk-your-api-key
MODEL_NAME=gpt-4o-mini
# 如果使用兼容 API（如 DashScope）：
# OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# MODEL_NAME=qwen-plus
```

### 3. 启动服务

```bash
uvicorn app:app --reload --port 8000
```

### 4. 打开 Chat Bot UI

浏览器访问：http://localhost:8000

## 📡 API 接口

### POST /api/chat - 发送消息

```json
// 请求
{
    "message": "帮我给 zhangsan@example.com 发邮件",
    "thread_id": null  // 首次为 null，后续传入返回的 thread_id
}

// 响应（正常完成）
{
    "thread_id": "uuid-xxx",
    "status": "completed",
    "messages": [{"role": "assistant", "content": "邮件已发送"}]
}

// 响应（被中断，等待人工输入）
{
    "thread_id": "uuid-xxx",
    "status": "interrupted",
    "messages": [...],
    "interrupt": {
        "type": "tool_approval",
        "tool_name": "send_email",
        "action": {"name": "send_email", "args": {...}},
        "allowed_decisions": ["approve", "edit", "reject"]
    }
}
```

### POST /api/chat/resume - 恢复执行

```json
// 请求（工具审批-批准）
{
    "thread_id": "uuid-xxx",
    "decision": {"type": "approve"}
}

// 请求（工具审批-编辑）
{
    "thread_id": "uuid-xxx",
    "decision": {
        "type": "edit",
        "edited_action": {"name": "send_email", "args": {"to": "new@example.com", ...}}
    }
}

// 请求（工具审批-拒绝）
{
    "thread_id": "uuid-xxx",
    "decision": {"type": "reject", "message": "收件人不对"}
}

// 请求（澄清提问-回答）
{
    "thread_id": "uuid-xxx",
    "decision": {"type": "respond", "message": "用户的回答"}
}

// 请求（复杂表单-提交）
{
    "thread_id": "uuid-xxx",
    "decision": {
        "type": "form_result",
        "values": {"start_date": "2024-01-01", "end_date": "2024-06-30", "granularity": "月"}
    }
}
```

### GET /api/state/{thread_id} - 查看会话状态

### DELETE /api/chat/{thread_id} - 清除会话

## 🔄 HITL 完整流程

```
1. 用户发送消息
   POST /api/chat {"message": "发邮件给 xxx"}
        ↓
2. Agent 执行，遇到工具调用 → interrupt() 暂停
   返回 {"status": "interrupted", "interrupt": {...}}
        ↓
3. 前端渲染 HITL 卡片（审批/提问/表单）
        ↓
4. 用户做出决策
   POST /api/chat/resume {"decision": {"type": "approve"}}
        ↓
5. Command(resume=decision) 恢复执行
   interrupt() 返回用户的 decision
        ↓
6. 工具根据决策执行，Agent 继续运行
        ↓
7. 可能再次 interrupt（多轮 HITL）或正常完成
```

## 🎯 复杂 HITL 场景处理

### 场景1：确定问题的时间范围

使用**模式三（复杂表单）**，前端渲染日期选择器：

```python
# 后端
result = ask_time_range("请选择分析的时间范围")
# result = {"start_date": "2024-01-01", "end_date": "2024-06-30", "granularity": "月"}
```

前端根据 `schema` 中的 `type: "date"` 渲染 `<input type="date">`，并提供"最近7天/30天/90天"快捷选项。

### 场景2：多轮澄清

Agent 可以连续调用多次 `interrupt()`，每次只问一个问题：

```python
# 第一轮：问时间范围
time_range = ask_time_range()

# 第二轮：问门店选择
stores = ask_store_selector(["门店A", "门店B", "门店C"])

# 第三轮：问指标
metric = ask_user_question("要分析哪个指标？", options=["销售额", "客流量", "转化率"])

# 所有信息收集完毕，执行分析
```

### 场景3：条件性 HITL

根据工具参数动态决定是否需要审批：

```python
@tool
def query_database(sql, is_readonly=True):
    if is_readonly:
        # 只读查询，直接执行
        return execute_sql(sql)
    else:
        # 写操作，需要审批
        decision = interrupt({...})
        if decision["type"] == "approve":
            return execute_sql(sql)
```

## 📁 文件结构

```
hitl_demo/
├── app.py              # 后端 API（FastAPI + LangGraph）
├── index.html          # 前端 Chat Bot UI
├── requirements.txt    # Python 依赖
├── README.md           # 本文档
└── checkpoints.db      # SQLite 持久化（运行后自动生成）
```

## 🔑 核心机制总结

| 机制 | 说明 |
|------|------|
| `interrupt(value)` | 暂停图执行，value 传递给前端 |
| `Command(resume=decision)` | 用户决策后恢复执行，decision 成为 interrupt() 的返回值 |
| `checkpointer` | 持久化图状态（SQLite），支持中断恢复 |
| `thread_id` | 会话标识，用于定位 checkpoint |
| `state.next` | 检查是否还有未执行节点（判断是否被中断） |
| `state.tasks[].interrupts` | 获取 interrupt 的值 |

## 📚 参考文档

- [LangChain Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [HumanInTheLoopMiddleware](https://reference.langchain.com/python/langchain/agents/middleware/human_in_the_loop/HumanInTheLoopMiddleware)
- [AskUserMiddleware](https://reference.langchain.com/python/deepagents-code/ask_user/AskUserMiddleware)
