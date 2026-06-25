"use client";

import { useApp } from "@/contexts/AppContext";
import type { PageKey } from "./Sidebar";

const PAGE_TITLES: Record<PageKey, string> = {
  dashboard: "仪表盘",
  scenarios: "场景列表",
  data: "数据管理",
  "extraction-logs": "提取日志",
  schema: "Schema 管理",
  "schema-optimization": "Schema 优化",
  concepts: "概念管理",
  metrics: "指标管理",
  "chart-rules": "图表规则",
  glossary: "专用名称",
  skills: "技能包",
  actions: "Action 管理",
  workflows: "工作流实例",
  "alert-rules": "告警规则",
  users: "用户管理",
  settings: "系统设置",
  "audit-logs": "操作日志",
};

const PAGE_GROUPS: Record<PageKey, string> = {
  dashboard: "概览",
  scenarios: "场景管理",
  data: "场景管理",
  "extraction-logs": "场景管理",
  schema: "知识建模",
  "schema-optimization": "知识建模",
  concepts: "知识建模",
  metrics: "知识建模",
  "chart-rules": "知识建模",
  glossary: "知识增强",
  skills: "知识增强",
  actions: "行动闭环",
  workflows: "行动闭环",
  "alert-rules": "行动闭环",
  users: "系统管理",
  settings: "系统管理",
  "audit-logs": "系统管理",
};

interface Props {
  activePage: PageKey;
}

export default function Header({ activePage }: Props) {
  const { sidebarCollapsed, setSidebarCollapsed, logout } = useApp();

  return (
    <header className="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-6 flex-shrink-0">
      <div className="flex items-center gap-3">
        <button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M3 12h18M3 6h18M3 18h18" />
          </svg>
        </button>
        <nav className="flex items-center text-sm">
          <span className="text-slate-400">{PAGE_GROUPS[activePage]}</span>
          <svg
            className="w-4 h-4 text-slate-300 mx-1"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M9 18l6-6-6-6" />
          </svg>
          <span className="text-slate-700 font-medium">
            {PAGE_TITLES[activePage]}
          </span>
        </nav>
      </div>
      <div className="flex items-center gap-2 pl-3 border-l border-slate-200">
        <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 text-xs font-bold">
          A
        </div>
        <button
          onClick={logout}
          className="text-xs text-slate-400 hover:text-red-500 transition-colors"
        >
          退出
        </button>
      </div>
    </header>
  );
}
