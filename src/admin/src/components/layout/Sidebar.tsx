"use client";

import { useApp } from "@/contexts/AppContext";

export type PageKey =
  | "dashboard"
  | "scenarios"
  | "data"
  | "extraction-logs"
  | "schema"
  | "schema-optimization"
  | "concepts"
  | "dimension-groups"
  | "metrics"
  | "chart-rules"
  | "glossary"
  | "skills"
  | "actions"
  | "workflows"
  | "alert-rules"
  | "users"
  | "settings"
  | "audit-logs";

interface NavGroup {
  label: string;
  items: { key: PageKey; label: string; icon: string }[];
}

const NAV_GROUPS: NavGroup[] = [
  { label: "概览", items: [{ key: "dashboard", label: "仪表盘", icon: "📊" }] },
  { label: "场景管理", items: [
    { key: "scenarios", label: "场景列表", icon: "📁" },
    { key: "data", label: "数据管理", icon: "📄" },
    { key: "extraction-logs", label: "提取日志", icon: "📋" },
  ]},
  { label: "知识建模", items: [
    { key: "schema", label: "Schema", icon: "🔗" },
    { key: "schema-optimization", label: "Schema 优化", icon: "🧭" },
    { key: "dimension-groups", label: "分析维度组", icon: "🧩" },
    { key: "metrics", label: "指标管理", icon: "📐" },
    { key: "concepts", label: "概念管理", icon: "🌳" },
    { key: "chart-rules", label: "图表规则", icon: "📈" },
  ]},
  { label: "知识增强", items: [
    { key: "glossary", label: "专用名称", icon: "📖" },
    { key: "skills", label: "技能包", icon: "⚡" },
  ]},
  { label: "行动闭环", items: [
    { key: "actions", label: "Action 管理", icon: "🚀" },
    { key: "workflows", label: "工作流实例", icon: "⚙️" },
    { key: "alert-rules", label: "告警规则", icon: "🔔" },
  ]},
  { label: "系统管理", items: [
    { key: "users", label: "用户管理", icon: "👥" },
    { key: "settings", label: "系统设置", icon: "⚙️" },
    { key: "audit-logs", label: "操作日志", icon: "📝" },
  ]},
];

interface Props {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
}

export default function Sidebar({ activePage, onNavigate }: Props) {
  const { sidebarCollapsed, setSidebarCollapsed } = useApp();

  return (
    <aside className={`flex-shrink-0 bg-deloitte-charcoal border-r border-black flex flex-col transition-all duration-300 ${sidebarCollapsed ? "w-16" : "w-56"}`}>
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-white/10 flex-shrink-0">
        <div className="w-8 h-8 rounded-lg bg-deloitte-green flex items-center justify-center text-deloitte-ink text-sm font-bold flex-shrink-0">O</div>
        {!sidebarCollapsed && <span className="ml-3 text-sm font-semibold text-white truncate">本体助手管理</span>}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4">
            {!sidebarCollapsed && <p className="px-3 mb-1 text-[10px] font-semibold text-white/40 uppercase tracking-wider">{group.label}</p>}
            {group.items.map((item) => {
              const isActive = activePage === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => onNavigate(item.key)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors mb-0.5 ${isActive ? "bg-deloitte-green text-deloitte-ink font-semibold" : "text-white/70 hover:bg-white/10 hover:text-white"}`}
                  title={sidebarCollapsed ? item.label : undefined}
                >
                  <span className="text-base flex-shrink-0 w-5 text-center">{item.icon}</span>
                  {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Collapse toggle */}
      <div className="border-t border-white/10 p-2 flex-shrink-0">
        <button onClick={() => setSidebarCollapsed(!sidebarCollapsed)} className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs text-white/50 hover:bg-white/10 hover:text-white transition-colors">
          <svg className={`w-4 h-4 transition-transform ${sidebarCollapsed ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
          {!sidebarCollapsed && <span>收起侧栏</span>}
        </button>
      </div>
    </aside>
  );
}
