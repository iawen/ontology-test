"use client";

import { flushSync } from 'react-dom';
import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  VisualizationData, Scenario, QueryResultData
} from "@/lib/types";
import { ThemeProvider } from "@/contexts/ThemeContext";

// 组件导入
import QueryResult from "@/components/QueryResult";
import AlertsPanel from "@/components/AlertsPanel";
import ClarificationCard from "@/components/ClarificationCard";
import DrilldownCard from "@/components/DrilldownCard";
import ActionConfirmCard from "@/components/ActionConfirmCard";
import PlanProgressCard from "@/components/PlanProgressCard";
import LoginOverlay from "@/components/LoginOverlay";
import SidebarPanel from "@/components/SidebarPanel";
import ToolStepsPanel from "@/components/ToolStepsPanel";
import type { Message, Conversation, Suggestion, ToolStep } from "@/lib/types";

function normalizeQueryResult(value: any): QueryResultData | undefined {
  if (!value || value.type !== "query_result" || value.error) return undefined;

  const rows = Array.isArray(value.rows) ? value.rows : [];
  const columns = Array.isArray(value.columns)
    ? value.columns
    : rows.length > 0
      ? Object.keys(rows[0] || {})
      : [];

  if (rows.length === 0 || columns.length === 0) return undefined;

  return {
    ...value,
    class_id: value.class_id || value.target_class || "query_result",
    class_name: value.class_name || value.target_class || "查询结果",
    columns,
    rows,
    total: typeof value.total === "number" ? value.total : (value.row_count ?? rows.length),
  };
}

function pickLatestValidQueryResult(toolResults: any[]): QueryResultData | undefined {
  for (let index = toolResults.length - 1; index >= 0; index -= 1) {
    const normalized = normalizeQueryResult(toolResults[index]?.result);
    if (normalized) return normalized;
  }
  return undefined;
}

function parseSseEvent(raw: string): any {
  try {
    return JSON.parse(raw);
  } catch (initialError) {
    const normalized = raw.replace(/([:\[,]\s*)-?(?:NaN|Infinity)(?=\s*[,}\]])/g, "$1null");
    if (normalized !== raw) {
      console.warn("流式事件包含非标准 JSON 数值，已按 null 兼容处理");
      return JSON.parse(normalized);
    }
    throw initialError;
  }
}

function AppContent() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false); // 控制输入框禁用

  // 认证与环境上下文
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState("");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [currentScenario, setCurrentScenario] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping, scrollToBottom]);

  // 检测登录凭证
  useEffect(() => {
    const t = localStorage.getItem("chat_token");
    const savedUsername = localStorage.getItem("chat_username");
    if (savedUsername) {
      setUsername(savedUsername);
    }
    if (t) {
      setToken(t);
      setLoggedIn(true);
    }
  }, []);

  // 加载业务场景
  useEffect(() => {
    if (loggedIn) {
      fetch("/api/scenarios/list")
        .then((r) => r.json())
        .then((data) => {
          setScenarios(data || []);
          if (data && data.length > 0) setCurrentScenario(data[0].id || "");
        })
        .catch(() => {});
    }
  }, [loggedIn]);

  useEffect(() => {
    if (currentScenario && token) {
      loadSuggestions();
      loadConversations();
    }
  }, [currentScenario, token]);

  const api = async (url: string, opts?: RequestInit) => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...((opts?.headers as Record<string, string>) || {}),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(url, { ...opts, headers });
    if (res.status === 401) {
      handleLogout();
      return null;
    }
    return res.json();
  };

  const handleLogin = async () => {
    setLoginError("");
    try {
      const r = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const d = await r.json();
      if (d.token) {
        const loginUsername = d.username || username;
        setToken(d.token);
        setUsername(loginUsername);
        setLoggedIn(true);
        localStorage.setItem("chat_token", d.token);
        localStorage.setItem("chat_username", loginUsername);
      } else {
        setLoginError(d.detail || "凭证错误");
      }
    } catch {
      setLoginError("连接认证网关失败");
    }
  };

  const handleLogout = () => {
    setToken("");
    setUsername("");
    setPassword("");
    setLoggedIn(false);
    localStorage.removeItem("chat_token");
    localStorage.removeItem("chat_username");
    setMessages([]);
    setConversations([]);
  };

  const loadSuggestions = async () => {
    try {
      const d = await api(`/api/suggestions/${currentScenario}`);
      setSuggestions(d || []);
    } catch {}
  };

  const loadConversations = async () => {
    try {
      const d = await api(`/api/conversations/${currentScenario}`);
      setConversations(d || []);
    } catch {}
  };

  const newConversation = async () => {
    try {
      const d = await api(`/api/conversations/${currentScenario}`, {
        method: "POST",
        body: JSON.stringify({ title: "新维分析会话" }),
      });
      if (d?.id) {
        setActiveConvId(d.id);
        setMessages([]);
        loadConversations();
      }
    } catch {}
  };

  const selectConversation = async (id: string) => {
    setActiveConvId(id);
    try {
      const d = await api(`/api/conversations/${id}/messages`);
      setMessages(
        (d || []).map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.created_at).getTime(),
          visualization: m.visualization || undefined,
          steps: m.steps || [],
          actionConfirm: m.action_confirm || undefined,
          isLoading: false,
        }))
      );
    } catch {
      setMessages([]);
    }
  };

  const deleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    if (activeConvId === id) {
      setActiveConvId("");
      setMessages([]);
    }
    loadConversations();
  };

  // SSE 响应引擎
  const sendMessage = async (text: string) => {
    if (!text.trim() || isTyping) return;

    let convId = activeConvId;
    if (!convId) {
      try {
        const d = await api("/api/conversations", {
          method: "POST",
          body: JSON.stringify({ scenario_id: currentScenario, title: text.slice(0, 20) }),
        });
        if (!d?.id) return;
        convId = d.id;
        setActiveConvId(convId);
        loadConversations();
      } catch {
        return;
      }
    }

    const userMsg: Message = { id: `u-${Date.now()}`, role: "user", content: text, timestamp: Date.now() };
    const aiMsgId = `a-${Date.now()}`;
    // 初始状态：让 isLoading = true，激活动态骨架屏和思考动画
    const initialAiMsg: Message = { 
      id: aiMsgId, 
      role: "assistant", 
      content: "", 
      timestamp: Date.now(), 
      steps: [], 
      isLoading: true 
    };

    setMessages((prev) => [...prev, userMsg, initialAiMsg]);
    setInput("");
    setIsTyping(true);

    try {
      const chatHistory = messages.map((m) => ({ role: m.role, content: m.content }));
      chatHistory.push({ role: "user", content: text });

      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const response = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify({ scenario_id: currentScenario, messages: chatHistory, conversation_id: convId }),
      });

      if (!response.ok) throw new Error(`Chat API 请求失败: ${response.status}`);

      if (!response.body) throw new Error("ReadableStream 获取失败");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let accumulatedContent = "";
      // 🌟 核心：使用局部追踪器，确保在 React 渲染周期内高频并发的数据引用被彻底断开，强制刷新 UI
      let currentSteps: ToolStep[] = [];
      let latestVisualization: VisualizationData | undefined = undefined;
      let currentPlan: Message["plan"] | undefined = undefined;
      let actionConfirm: Message["actionConfirm"] | undefined = undefined;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const event = parseSseEvent(raw);

            switch (event.type) {
              case "plan":
                currentPlan = event;
                setMessages((prev) =>
                  prev.map((m) => (m.id === aiMsgId ? { ...m, plan: currentPlan, isLoading: false } : m))
                );
                break;

              case "text":
                accumulatedContent += event.content;
                flushSync(() => {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === aiMsgId
                        ? { ...m, content: accumulatedContent, isLoading: false }
                        : m
                    )
                  );
                });
                break;

              case "tool":
                // 1. 工具启动：创建全新的 Step 节点，状态设为 running
                const startedAt = event.started_at ?? Date.now();
                currentSteps = [
                  ...currentSteps,
                  { 
                    name: event.name, 
                    description: event.description,
                    args: event.arguments || {}, 
                    status: "running",
                    startedAt,
                    planningFinishedAt: event.planning_finished_at,
                    planningDurationMs: event.planning_duration_ms,
                  }
                ];
                // 强制更新数组引用，让 React 的 Diff 引擎立刻捕捉并逐步渲染
                flushSync(() => {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === aiMsgId
                        ? { ...m, steps: [...currentSteps], isLoading: false }
                        : m
                    )
                  );
                });
                break;

              case "tool_result":
                // 2. 工具执行完：精准定位正在运行的节点，将其升级为 completed
                let completedOne = false;
                const finishedAt = event.finished_at ?? Date.now();
                currentSteps = currentSteps.map((step) => {
                  if (!completedOne && step.name === event.name && step.status === "running") {
                    completedOne = true;
                    const hasError = Boolean(event.result?.error);
                    return { 
                      ...step, 
                      status: hasError ? "failed" as const : "completed" as const, 
                      description: event.description ?? step.description,
                      result: event.result_preview ?? event.result,
                      startedAt: event.started_at ?? step.startedAt,
                      planningFinishedAt: event.planning_finished_at ?? step.planningFinishedAt,
                      planningDurationMs: event.planning_duration_ms ?? step.planningDurationMs,
                      executionStartedAt: event.execution_started_at,
                      executionDurationMs: event.execution_duration_ms,
                      finishedAt,
                      durationMs: event.duration_ms ?? (step.startedAt ? finishedAt - step.startedAt : undefined),
                    };
                  }
                  return step;
                });

                if (!completedOne) {
                  currentSteps = [
                    ...currentSteps,
                    {
                      name: event.name,
                      description: event.description,
                      args: {},
                      status: event.result?.error ? "failed" : "completed",
                      result: event.result_preview ?? event.result,
                      startedAt: event.started_at ?? finishedAt,
                      planningFinishedAt: event.planning_finished_at,
                      planningDurationMs: event.planning_duration_ms,
                      executionStartedAt: event.execution_started_at,
                      executionDurationMs: event.execution_duration_ms,
                      finishedAt,
                      durationMs: event.duration_ms ?? 0,
                    },
                  ];
                }

                if (currentPlan?.steps) {
                  let matchedPlanStep = false;
                  currentPlan = {
                    ...currentPlan,
                    steps: currentPlan.steps.map((step) => {
                      if (!matchedPlanStep && step.tool === event.name && step.status === "running") {
                        matchedPlanStep = true;
                        return { ...step, status: event.result?.error ? "failed" : "completed", result: event.result };
                      }
                      return step;
                    }),
                  };
                }
                
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? { ...m, steps: [...currentSteps], plan: currentPlan }
                      : m
                  )
                );
                break;

              case "clarification":
                setMessages((prev) =>
                  prev.map((m) => (m.id === aiMsgId ? { ...m, clarification: event.data, isLoading: false } : m))
                );
                break;

              case "drilldown":
                setMessages((prev) =>
                  prev.map((m) => (m.id === aiMsgId ? { ...m, drilldown: event.data, isLoading: false } : m))
                );
                break;

              case "action_confirm":
                actionConfirm = event.data || event.action;
                setMessages((prev) =>
                  prev.map((m) => (m.id === aiMsgId ? { ...m, actionConfirm, isLoading: false } : m))
                );
                break;

              case "chart_data":
                latestVisualization = normalizeQueryResult(event.data);
                if (latestVisualization) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === aiMsgId
                        ? { ...m, visualization: latestVisualization, chartConfig: event.chart_config || undefined, isLoading: false }
                        : m
                    )
                  );
                }
                break;

              case "done":
                let finalViz: VisualizationData | undefined = latestVisualization;
                if (Array.isArray(event.tool_results)) {
                  finalViz = pickLatestValidQueryResult(event.tool_results) || finalViz;
                }

                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? {
                          ...m,
                          isLoading: false,
                          visualization: finalViz || m.visualization,
                          steps: currentSteps,
                          plan: currentPlan || m.plan,
                        }
                      : m
                  )
                );

                // 首问自动更替标题
                if (messages.length === 0) {
                  api(`/api/conversations/${convId}`, {
                    method: "PUT",
                    body: JSON.stringify({ title: text.slice(0, 20) }),
                  }).catch(() => {});
                  loadConversations();
                }
                break;

              case "error":
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId ? { ...m, content: m.content + `\n\n智能体系统异常: ${event.content}`, isLoading: false } : m
                  )
                );
                break;
            }
          } catch (e) {
            console.error("解析流式事件失败", e);
          }
        }
      }      
    } catch (err: any) {
      setMessages((prev) =>
        prev.map((m) => (m.id === aiMsgId ? { ...m, isLoading: false, content: `连接错误: ${err.message}` } : m))
      );
    } finally {
      setIsTyping(false);
    }
  };

  const handleTableDrilldown = (dimension: string, value: string) => {
    sendMessage(`下钻查看 ${dimension}="${value}" 的详细数据`);
  };

  // 消息渲染
  const renderMessage = (msg: Message) => {
    if (msg.role === "user") {
      return (
        <div key={msg.id} className="flex justify-end mb-4">
          <div
            className="max-w-[85%] px-4 py-2.5 rounded-lg text-sm leading-relaxed whitespace-pre-wrap shadow-sm border border-black/5"
            style={{
              background: "var(--accent)",
              color: "#fff",
            }}
          >
            {msg.content}
          </div>
        </div>
      );
    }

    return (
      <div key={msg.id} className="flex justify-start mb-6">
        <div className="w-full max-w-[95%] space-y-3">
          {msg.plan && msg.plan.steps && msg.plan.steps.length > 0 && (
            <PlanProgressCard data={msg.plan} />
          )}

          {msg.steps && msg.steps.length > 0 && (
            <ToolStepsPanel steps={msg.steps} />
          )}

          {msg.isLoading && !msg.content && (!msg.steps || msg.steps.length === 0) && (
            <div 
              className="flex items-center gap-3 px-3 py-2.5 rounded-lg border max-w-sm text-xs shadow-sm"
              style={{
                borderColor: "var(--border)",
                background: "var(--bg-card)",
                color: "var(--text-muted)"
              }}
            >
              <div className="flex gap-1">
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
              </div>
              <span>正在分析问题并准备上下文...</span>
            </div>
          )}

          {msg.clarification && (
            <ClarificationCard data={msg.clarification} onSelect={(optId, val) => sendMessage(val)} />
          )}

          {msg.drilldown && (
            <DrilldownCard
              data={msg.drilldown}
              onDrill={(opt) => sendMessage(opt.dimension ? `按 ${opt.dimension} 维度分析: ${opt.label}` : opt.label)}
            />
          )}

          {msg.content && (
            <div className="md-content rounded-lg border border-indigo-100 bg-white px-4 py-3 text-sm text-slate-800 shadow-sm dark:border-slate-700/60 dark:bg-slate-950/30 dark:text-slate-100">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          )}

          {msg.actionConfirm && (
            <ActionConfirmCard
              data={msg.actionConfirm}
              onConfirm={(actId) => sendMessage(`确认执行动作: ${actId}`)}
              onCancel={() => {}}
            />
          )}

          {msg.visualization && msg.visualization.type === "query_result" && (
            <div className="max-w-full overflow-hidden rounded-lg border border-slate-200/60 dark:border-slate-800 shadow-sm">
              <QueryResult
                data={msg.visualization as QueryResultData}
                chartConfig={msg.chartConfig}
                onDrilldown={handleTableDrilldown}
              />
            </div>
          )}

          {msg.visualization && msg.visualization.type === "alerts" && (
            <AlertsPanel data={msg.visualization as any} />
          )}
        </div>
      </div>
    );
  };

  if (!loggedIn) {
    return (
      <LoginOverlay
        username={username}
        setUsername={setUsername}
        password={password}
        setPassword={setPassword}
        error={loginError}
        onLogin={handleLogin}
      />
    );
  }

  // 主布局
  return (
    <div className="flex h-screen bg-slate-100 dark:bg-slate-950 overflow-hidden text-slate-900 dark:text-slate-100 font-sans antialiased">
      <SidebarPanel
        username={username}
        scenarios={scenarios}
        currentScenario={currentScenario}
        onSwitchScenario={(id) => {
          setCurrentScenario(id);
          setMessages([]);
          setActiveConvId("");
        }}
        conversations={conversations}
        activeConvId={activeConvId}
        onSelectConversation={selectConversation}
        onDeleteConversation={deleteConversation}
        onNewConversation={newConversation}
        onLogout={handleLogout}
      />

      {/* 主视图视窗 */}
      <main className="flex-1 flex flex-col h-full bg-slate-50 dark:bg-slate-900 relative">
        
        {/* 对话滚动区域 */}
        <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
          <div className="max-w-4xl mx-auto w-full space-y-6">
            {messages.length === 0 ? (
              <div className="h-[70vh] flex flex-col items-center justify-center text-center p-8">
                <div className="mb-4 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                  Ontology AI
                </div>
                <h2 className="text-2xl font-bold text-slate-800 dark:text-slate-200 mb-2 tracking-tight">
                  开始一次数据分析会话
                </h2>
                <p className="text-xs text-slate-400 dark:text-slate-500 max-w-md mb-8">
                  选择一个推荐问题，或直接输入你想查看的指标、维度和筛选条件。
                </p>
                
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full">
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => sendMessage(s.question)}
                      className="p-3 text-left bg-white dark:bg-slate-900 hover:bg-slate-50 dark:hover:bg-slate-800 border border-slate-200/80 dark:border-slate-800 rounded-lg text-xs transition-all duration-200 hover:border-indigo-300 dark:hover:border-indigo-800 cursor-pointer group shadow-sm"
                    >
                      <span className="font-medium text-slate-700 dark:text-slate-300 group-hover:text-indigo-600 dark:group-hover:text-indigo-400">
                        {s.question}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => renderMessage(m))
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* 输入组件控制面板 */}
        <div className="p-4 border-t border-slate-200 dark:border-slate-800 bg-white/95 dark:bg-slate-900/95 backdrop-blur-md">
          <div className="max-w-4xl mx-auto flex gap-3 w-full">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={isTyping ? "正在处理当前问题..." : "输入指标、维度或业务问题..."}
              disabled={isTyping}
              onKeyDown={(e) => e.key === "Enter" && sendMessage(input)}
              className="flex-1 px-4 py-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 disabled:opacity-60 transition-all shadow-inner"
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={isTyping || !input.trim()}
              className="px-5 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-sm rounded-lg transition-all disabled:opacity-40 cursor-pointer shadow-sm active:scale-95 shrink-0"
            >
              发送
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function Home() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}