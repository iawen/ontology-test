"use client";

import { flushSync } from 'react-dom';
import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  Scenario, QueryResultData
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
import type { AnswerDataset, ClarificationAnswer, Message, Conversation, ToolStep } from "@/lib/types";

function normalizeQueryResult(value: any): QueryResultData | undefined {
  if (!value || value.error) return undefined;

  const rows = Array.isArray(value.rows) ? value.rows : [];
  const columns = Array.isArray(value.columns)
    ? value.columns
    : rows.length > 0
      ? Object.keys(rows[0] || {})
      : [];

  if (rows.length === 0 || columns.length === 0) return undefined;

  return {
    ...value,
    type: "query_result",
    class_id: value.class_id || value.target_class || "query_result",
    class_name: value.class_name || value.target_class || "查询结果",
    columns,
    rows,
    total: typeof value.total === "number" ? value.total : (value.row_count ?? rows.length),
  };
}

function normalizeAnswerDatasets(value: any): AnswerDataset[] {
  if (!Array.isArray(value)) return [];
  const datasets = value
    .map((item, index) => {
      const data = normalizeQueryResult(item?.data || item?.result || item);
      if (!data) return undefined;
      return {
        id: String(item?.id || item?.dataset_index || `query_${index + 1}`),
        name: String(
          item?.name || item?.target_class || data.class_name || `Query ${index + 1}`,
        ),
        arguments: item?.arguments || undefined,
        chart_type: item?.chart_type || item?.chartConfig?.chart_type || item?.chart_config?.chart_type,
        chart_config: item?.chart_config || item?.chartConfig || undefined,
        data,
      } satisfies AnswerDataset;
    })
    .filter(Boolean) as AnswerDataset[];

  // SSE/persisted payloads can contain the same completed query more than once.
  // Preserve distinct Plan-Execute evidence, but collapse byte-for-byte duplicate
  // datasets so the answer does not repeat identical charts and tables.
  const seen = new Set<string>();
  return datasets.filter((dataset) => {
    const signature = JSON.stringify({
      targetClass: dataset.data.class_id,
      columns: dataset.data.columns,
      rows: dataset.data.rows,
      sql: dataset.data.sql || "",
    });
    if (seen.has(signature)) return false;
    seen.add(signature);
    return true;
  });
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

function parseEventTimestamp(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return Date.now();
}

function parseToolPayload(value: unknown) {
  if (typeof value !== "string") return value;
  try {
    return parseSseEvent(value);
  } catch {
    return value;
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

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const manuallyRenamedConversationIds = useRef(new Set<string>());

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
          answerDatasets: normalizeAnswerDatasets(m.answer_datasets),
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

  const renameConversation = async (id: string, title: string) => {
    const normalizedTitle = title.trim();
    if (!normalizedTitle) return;
    manuallyRenamedConversationIds.current.add(id);
    try {
      const result = await api(`/api/conversations/${id}`, {
        method: "PUT",
        body: JSON.stringify({ title: normalizedTitle }),
      });
      if (result?.status !== "ok") throw new Error("会话标题更新失败");
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === id ? { ...conversation, title: normalizedTitle } : conversation,
        ),
      );
    } catch {
      manuallyRenamedConversationIds.current.delete(id);
    }
  };

  // SSE 响应引擎
  const sendMessage = async (
    text: string,
    clarificationAnswers?: ClarificationAnswer[],
    clarificationCheckpointId?: string,
  ) => {
    if (!text.trim() || isTyping) return;

    let convId = activeConvId;
    if (!convId) {
      try {
        const d = await api(`/api/conversations/${currentScenario}`, {
          method: "POST",
          body: JSON.stringify({ title: text.slice(0, 20) }),
        });
        if (!d?.id) return;
        convId = d.id;
        setActiveConvId(convId);
        loadConversations();
      } catch {
        return;
      }
    }

    const isClarificationContinuation = Boolean(clarificationCheckpointId && clarificationAnswers?.length);
    const continuationText = "已确认维度条件，继续查询。";
    const userMsg: Message = {
      id: `u-${Date.now()}`,
      role: "user",
      content: isClarificationContinuation ? continuationText : text,
      timestamp: Date.now(),
    };
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
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const response = await fetch("/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify({
          session_id: convId,
          agent_id: currentScenario,
          message: text,
          language: navigator.language || "zh-CN",
          options: {
            source: "frontend",
            conversation_id: convId,
            clarification_answers: clarificationAnswers,
            clarification_checkpoint_id: clarificationCheckpointId,
            clarification_continuation: isClarificationContinuation,
            clarification_display: isClarificationContinuation ? continuationText : undefined,
          },
        }),
      });

      if (response.status === 401) {
        handleLogout();
        throw new Error("登录状态已失效，请重新登录");
      }
      if (!response.ok) throw new Error(`Chat API 请求失败: ${response.status}`);

      if (!response.body) throw new Error("ReadableStream 获取失败");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let accumulatedContent = "";
      // 🌟 核心：使用局部追踪器，确保在 React 渲染周期内高频并发的数据引用被彻底断开，强制刷新 UI
      let currentSteps: ToolStep[] = [];
      let answerDatasets: AnswerDataset[] = [];
      let currentPlan: Message["plan"] | undefined = undefined;
      let actionConfirm: Message["actionConfirm"] | undefined = undefined;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          const raw = block
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n")
            .trim();
          if (!raw) continue;
          try {
            const event = parseSseEvent(raw);

            switch (event.type) {
              case "query_started":
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId ? { ...m, isLoading: true } : m,
                  ),
                );
                break;

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

              case "tools": {
                if (event.step && typeof event.step === "object") {
                  currentSteps = [...currentSteps, event.step as ToolStep];
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === aiMsgId
                        ? { ...m, steps: [...currentSteps], isLoading: false }
                        : m,
                    ),
                  );
                  break;
                }
                const result = parseToolPayload(event.payload);
                const startedAt = parseEventTimestamp(event.begin_time);
                const durationMs = Math.round(Number(event.duration || 0) * 1000);
                const finishedAt = startedAt + durationMs;
                const failed = Boolean(
                  result && typeof result === "object" && "error" in result && result.error,
                );
                currentSteps = [
                  ...currentSteps,
                  {
                    name: event.tool_name || "工具调用",
                    description: event.description,
                    args:
                      result && typeof result === "object"
                        ? (result.arguments || {})
                        : {},
                    status: failed ? "failed" : "completed",
                    result,
                    startedAt,
                    finishedAt,
                    durationMs,
                  },
                ];
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? { ...m, steps: [...currentSteps], isLoading: false }
                      : m,
                  ),
                );
                break;
              }

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

              case "answer_datasets":
                answerDatasets = normalizeAnswerDatasets(event.data);
                if (answerDatasets.length > 0) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === aiMsgId
                        ? { ...m, answerDatasets, isLoading: false }
                        : m
                    )
                  );
                }
                break;

              case "done":
                if (Array.isArray(event.steps)) {
                  currentSteps = event.steps as ToolStep[];
                }
                const finalAnswer = event.final_answer || {};
                const finalAnswerContent =
                  typeof finalAnswer === "string"
                    ? finalAnswer
                    : finalAnswer.final_answer || finalAnswer.plain_text_summary || "";
                const finalAnswerDatasets = normalizeAnswerDatasets(
                  event.answer_datasets || finalAnswer.supporting_datasets,
                );
                if (finalAnswerDatasets.length > 0) {
                  answerDatasets = finalAnswerDatasets;
                }
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? {
                          ...m,
                          content: finalAnswerContent || m.content,
                          isLoading: false,
                          answerDatasets: answerDatasets.length > 0 ? answerDatasets : m.answerDatasets,
                          steps: currentSteps,
                          plan: currentPlan || m.plan,
                        }
                      : m
                  )
                );

                // 首问自动更替标题
                if (messages.length === 0 && !manuallyRenamedConversationIds.current.has(convId)) {
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
    const messageIndex = messages.findIndex((item) => item.id === msg.id);
    const originalQuestion = messages
      .slice(0, messageIndex)
      .reverse()
      .find((item) => item.role === "user")?.content;
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

    const answerDatasetItems = msg.answerDatasets || [];

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
              className="flex w-full max-w-md items-center gap-3 rounded-xl border border-deloitte-line border-l-4 border-l-deloitte-green bg-gradient-to-br from-deloitte-green-light via-white to-deloitte-mist px-4 py-3 shadow-sm dark:border-deloitte-green/35 dark:from-deloitte-green/20 dark:via-slate-900 dark:to-slate-950"
            >
              <div className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-deloitte-green-light text-deloitte-green-dark dark:bg-deloitte-green/25 dark:text-deloitte-green-light">
                <span className="absolute inset-0 animate-ping rounded-full bg-deloitte-green/30" />
                <svg className="relative h-4 w-4 animate-pulse" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M12 3v3m0 12v3M4.22 4.22l2.12 2.12m11.32 11.32 2.12 2.12M3 12h3m12 0h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                  <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.8" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-slate-800 dark:text-white">正在理解你的问题</div>
                <div className="mt-0.5 text-xs font-medium text-slate-600 dark:text-deloitte-green-light">正在匹配业务术语并准备分析上下文</div>
              </div>
              <div className="ml-auto flex shrink-0 gap-1.5 pt-4" aria-label="处理中">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-deloitte-green [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-deloitte-green [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-deloitte-green" />
              </div>
            </div>
          )}

          {msg.clarification && (
            <ClarificationCard
              data={msg.clarification}
              onSelect={(optId, val) => sendMessage(val)}
              onSubmitAnswers={(answers) => sendMessage(
                originalQuestion || "请按已确认的维度口径继续原查询。",
                answers,
                msg.clarification?.checkpoint_id,
              )}
            />
          )}

          {msg.drilldown && (
            <DrilldownCard
              data={msg.drilldown}
              onDrill={(opt) => sendMessage(opt.dimension ? `按 ${opt.dimension} 维度分析: ${opt.label}` : opt.label)}
            />
          )}

          {msg.content && (
            <div className="md-content rounded-lg border border-deloitte-line bg-white px-4 py-3 text-sm text-slate-800 shadow-sm dark:border-slate-700/60 dark:bg-slate-950/30 dark:text-slate-100">
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

          {answerDatasetItems.length > 0 && (
            <div className="space-y-3">
              {answerDatasetItems.map((dataset, index) => (
                <div key={dataset.id || index} className="max-w-full overflow-hidden rounded-lg border border-slate-200/60 dark:border-slate-800 shadow-sm">
                  <div className="border-b border-slate-200/60 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-300">
                    {dataset.name || `Query ${index + 1}`}
                  </div>
                  <QueryResult
                    data={dataset.data}
                    chartConfig={dataset.chart_config || (dataset.chart_type ? { chart_type: dataset.chart_type, title: dataset.name, data: dataset.data.rows } : undefined)}
                    onDrilldown={handleTableDrilldown}
                  />
                </div>
              ))}
            </div>
          )}

          {answerDatasetItems.length === 0 && msg.visualization && msg.visualization.type === "query_result" && (
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
    <div className="flex h-screen bg-deloitte-mist dark:bg-deloitte-ink overflow-hidden text-slate-900 dark:text-slate-100 font-sans antialiased">
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
        onRenameConversation={renameConversation}
        onDeleteConversation={deleteConversation}
        onNewConversation={newConversation}
        onLogout={handleLogout}
      />

      {/* 主视图视窗 */}
      <main className="flex-1 flex flex-col h-full bg-deloitte-mist dark:bg-deloitte-charcoal relative">
        
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
                  输入你想查看的指标、维度和筛选条件。
                </p>
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
              className="flex-1 px-4 py-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950 text-sm focus:outline-none focus:ring-2 focus:ring-deloitte-green/25 focus:border-deloitte-green disabled:opacity-60 transition-all shadow-inner"
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={isTyping || !input.trim()}
              className="px-5 py-3 bg-deloitte-green hover:bg-deloitte-green-dark text-deloitte-ink hover:text-white font-semibold text-sm rounded-lg transition-all disabled:opacity-40 cursor-pointer shadow-sm active:scale-95 shrink-0"
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