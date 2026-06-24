"use client";

import { flushSync } from 'react-dom';
import { useState, useRef, useEffect, useCallback, useMemo } from "react";
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
import type { Message, Conversation, Suggestion, ToolStep } from "@/lib/types";

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

  // ================= ⚡️ 核心改良：高性能高敏感度 SSE 响应引擎 =================
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

      if (!response.body) throw new Error("ReadableStream 获取失败");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let accumulatedContent = "";
      // 🌟 核心：使用局部追踪器，确保在 React 渲染周期内高频并发的数据引用被彻底断开，强制刷新 UI
      let currentSteps: ToolStep[] = [];
      let actionConfirm = {};

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
            const event = JSON.parse(raw);

            switch (event.type) {
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
                currentSteps = [
                  ...currentSteps,
                  { 
                    name: event.name, 
                    args: event.arguments || {}, 
                    status: "running" 
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
                currentSteps = currentSteps.map((step) => {
                  if (step.name === event.name && step.status === "running") {
                    return { 
                      ...step, 
                      status: "completed" as const, 
                      result: event.result_preview 
                    };
                  }
                  return step;
                });
                
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? { ...m, steps: [...currentSteps] }
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
                setMessages((prev) =>
                  prev.map((m) => (m.id === aiMsgId ? { ...m, actionConfirm: event.data, isLoading: false } : m))
                );
                actionConfirm = event.data || {};
                break;

              case "chart_data":
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? { ...m, visualization: event.data, chartConfig: event.chart_config || undefined, isLoading: false }
                      : m
                  )
                );
                break;

              case "done":
                let finalViz: VisualizationData | undefined = undefined;
                if (event.tool_results) {
                  const queryResultTool = event.tool_results.find((t: any) => t.result?.type === "query_result");
                  if (queryResultTool) {
                    finalViz = queryResultTool.result;
                  }
                }

                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMsgId
                      ? {
                          ...m,
                          isLoading: false,
                          visualization: finalViz || m.visualization,
                        }
                      : m
                  )
                );

                // 异步持久化落库
                const payloadAiMsg = {
                  id: aiMsgId,
                  role: "assistant" as const,
                  content: accumulatedContent,
                  timestamp: Date.now(),
                  visualization: finalViz,
                  steps: currentSteps,
                  action_confirm: actionConfirm,
                };
                api(`/api/conversations/${convId}/messages`, {
                  method: "POST",
                  body: JSON.stringify({ messages: [userMsg, payloadAiMsg] }),
                }).catch(() => {});

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
                    m.id === aiMsgId ? { ...m, content: m.content + `\n\n❌ 智能体系统异常: ${event.content}`, isLoading: false } : m
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
        prev.map((m) => (m.id === aiMsgId ? { ...m, isLoading: false, content: `❌ 连接错误: ${err.message}` } : m))
      );
    } finally {
      setIsTyping(false);
    }
  };

  const handleTableDrilldown = (dimension: string, value: string) => {
    sendMessage(`下钻查看 ${dimension}="${value}" 的详细数据`);
  };

  // ================= ⚡️ 视觉与逻辑改良后的渲染引擎 =================
  const renderMessage = (msg: Message) => {
    if (msg.role === "user") {
      return (
        <div key={msg.id} className="flex justify-end mb-4">
          <div
            className="max-w-[85%] px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap shadow-sm border border-black/5"
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
        <div className="w-full max-w-[95%] space-y-4">
          
          {/* 🌟 修正 1：完美对接 PlanProgressCard 的 data 属性 */}
          {msg.plan && msg.plan.steps && msg.plan.steps.length > 0 && (
            <div className="transition-all duration-300">
              <PlanProgressCard data={msg.plan} />
            </div>
          )}

          {/* 🌟 修正 2：独立折叠的底层工具调用时序流 */}
          {msg.steps && msg.steps.length > 0 && (
            <div
              className="border rounded-xl p-3 space-y-2 max-h-80 overflow-y-auto shadow-sm"
              style={{
                borderColor: "var(--border)",
                background: "var(--bg-primary)",
              }}
            >
              {msg.steps.map((step, idx) => {
                return <StepItem key={idx} step={step} />;
              })}
            </div>
          )}

          {/* 🌟 3. 无缝 Loading 保持不变 */}
          {msg.isLoading && !msg.content && (!msg.steps || msg.steps.length === 0) && (
            <div 
              className="flex items-center gap-3 p-3 rounded-xl border max-w-sm animate-pulse text-xs shadow-sm"
              style={{
                borderColor: "var(--border)",
                background: "var(--sidebar-bg)",
                color: "var(--text-muted)"
              }}
            >
              <div className="flex h-2 w-2 relative">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-600"></span>
              </div>
              <span>正在唤醒数智大脑，规划多维求解路径...</span>
            </div>
          )}

          {/* 其余交互组件卡片及富文本渲染保持原样... */}
          {msg.clarification && (
            <ClarificationCard data={msg.clarification} onSelect={(optId, val) => sendMessage(val)} />
          )}

          {msg.drilldown && (
            <DrilldownCard
              data={msg.drilldown}
              onDrill={(opt) => sendMessage(opt.dimension ? `按 ${opt.dimension} 维度分析：${opt.label}` : opt.label)}
            />
          )}

          {msg.content && (
            <div className="md-content">
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
            <div className="max-w-full overflow-hidden rounded-xl border border-slate-200/60 dark:border-slate-800 shadow-sm">
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

  // ================= ⚡️ 布局改良：严格限制 max-w-4xl 且全局居中对齐 =================
  return (
    <div className="flex h-screen bg-slate-50 dark:bg-slate-950 overflow-hidden text-slate-900 dark:text-slate-100 font-sans antialiased">
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
      <main className="flex-1 flex flex-col h-full bg-white dark:bg-slate-900 relative">
        
        {/* 对话滚动区域：使用 max-w-4xl mx-auto 强行让聊天流和底部输入框完美等宽、水平对齐 */}
        <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
          <div className="max-w-4xl mx-auto w-full space-y-6">
            {messages.length === 0 ? (
              <div className="h-[70vh] flex flex-col items-center justify-center text-center p-8">
                <div className="p-4 bg-indigo-50 dark:bg-indigo-950/40 rounded-3xl text-indigo-600 dark:text-indigo-400 mb-4 shadow-inner text-3xl animate-bounce">
                  🤖
                </div>
                <h2 className="text-2xl font-bold text-slate-800 dark:text-slate-200 mb-2 tracking-tight">
                  欢迎来到 Ontology AI 管理看板
                </h2>
                <p className="text-xs text-slate-400 dark:text-slate-500 max-w-md mb-8">
                  本体策略模型加载就绪，您可以选择下方推荐进行深度剖析：
                </p>
                
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full">
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => sendMessage(s.question)}
                      className="p-3.5 text-left bg-slate-50 dark:bg-slate-800/50 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 border border-slate-200/80 dark:border-slate-800 rounded-2xl text-xs transition-all duration-200 hover:border-indigo-300 dark:hover:border-indigo-800 cursor-pointer group shadow-sm"
                    >
                      <span className="mr-2 group-hover:scale-125 inline-block transition-transform">{s.icon || "💡"}</span> 
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

        {/* 输入组件控制面板：保持 max-w-4xl mx-auto */}
        <div className="p-4 border-t border-slate-100 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md">
          <div className="max-w-4xl mx-auto flex gap-3 w-full">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={isTyping ? "AI 智能体正调用多维本体模型进行多步规划..." : "请向智能体提问..."}
              disabled={isTyping}
              onKeyDown={(e) => e.key === "Enter" && sendMessage(input)}
              className="flex-1 px-4 py-3 rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 disabled:opacity-60 transition-all shadow-inner"
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={isTyping || !input.trim()}
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-sm rounded-2xl transition-all disabled:opacity-40 cursor-pointer shadow-md shadow-indigo-600/10 hover:shadow-indigo-600/20 active:scale-95 shrink-0"
            >
              发送
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

// ================= ⚡️ 新增：支持独立折叠/展开的算子组件 =================
function StepItem({ step }: { step: ToolStep }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div
      className="p-3 rounded-lg border transition-all duration-200"
      style={{
        borderColor: "var(--border)",
        background: "var(--sidebar-bg)",
      }}
    >
      {/* 头部点击区域 */}
      <div 
        className="flex flex-wrap items-center justify-between gap-2 font-semibold text-xs cursor-pointer select-none"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span style={{ color: "var(--accent-light)" }} className="whitespace-normal flex items-center gap-1.5">
          <span>{isOpen ? "🔽" : "▶️"}</span>
          🛠️ 算子调度: { step.name }
        </span>
        <span
          className={`px-2 py-0.5 rounded-md text-[10px] flex items-center gap-1 shrink-0 ${
            step.status === "running" 
              ? "bg-amber-500/10 text-amber-500 animate-pulse" 
              : "bg-emerald-500/10 text-emerald-500"
          }`}
        >
          {step.status === "running" ? (
            <>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping"></span>
              执行中...
            </>
          ) : (
            "✓ 已完成"
          )}
        </span>
      </div>

      {/* 折叠展开的内容区域 */}
      {isOpen && step.args && Object.keys(step.args).length > 0 && (
        <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400 border-t border-slate-200/40 dark:border-slate-800/40 pt-2 animate-fadeIn">
          <span className="font-bold block mb-1 text-slate-600 dark:text-slate-400">约束条件与入参:</span>
          <pre className="p-2 bg-slate-100/80 dark:bg-slate-950/50 rounded overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] border border-slate-200/50 dark:border-slate-800/50 leading-relaxed">
            {JSON.stringify(step.args, null, 2)}
          </pre>
          {step.result && (
            <div className="mt-2">
              <span className="font-bold block mb-1 text-slate-600 dark:text-slate-400">算子输出预览:</span>
              <div className="p-2 bg-emerald-50/40 dark:bg-emerald-950/10 rounded border border-emerald-500/10 text-emerald-800 dark:text-emerald-400 max-h-32 overflow-y-auto">
                {typeof step.result === "string" ? step.result : JSON.stringify(step.result)}
              </div>
            </div>
          )}
        </div>
      )}
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