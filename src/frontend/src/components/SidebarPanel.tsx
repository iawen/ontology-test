"use client";

import React from "react";
import type { Scenario } from "@/lib/types";

interface Conversation {
  id: string;
  title: string;
  scenario_id: string;
}

interface SidebarPanelProps {
  username: string;
  scenarios: Scenario[];
  currentScenario: string;
  onSwitchScenario: (id: string) => void;
  conversations: Conversation[];
  activeConvId: string;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string, e: React.MouseEvent) => void;
  onNewConversation: () => void;
  onLogout: () => void;
}

export default function SidebarPanel({
  username,
  scenarios,
  currentScenario,
  onSwitchScenario,
  conversations,
  activeConvId,
  onSelectConversation,
  onDeleteConversation,
  onNewConversation,
  onLogout,
}: SidebarPanelProps) {
  return (
    <aside className="w-64 border-r border-slate-200 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-950 flex flex-col h-full flex-shrink-0">
      {/* 顶部场景切换 */}
      <div className="p-4 border-b border-slate-200 dark:border-slate-800">
        <label className="block text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5">当前分析业务场景</label>
        <select
          value={currentScenario}
          onChange={(e) => onSwitchScenario(e.target.value)}
          className="w-full px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 text-xs font-medium focus:outline-none"
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>{s.name || s.id}</option>
          ))}
        </select>
      </div>

      {/* 会话列表操作区 */}
      <div className="p-3 flex-1 overflow-y-auto space-y-1">
        <button
          onClick={onNewConversation}
          className="w-full py-2 px-3 mb-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/50 flex items-center justify-center gap-1.5 transition-colors cursor-pointer shadow-sm"
        >
          ➕ 开启全新多维对话
        </button>

        <div className="text-[11px] font-bold text-slate-400 px-2 pb-1 uppercase tracking-wider">历史认知会话</div>
        {conversations.length === 0 ? (
          <div className="text-xs text-slate-400 text-center py-8">暂无历史会话</div>
        ) : (
          conversations.map((c) => {
            const isActive = c.id === activeConvId;
            return (
              <div
                key={c.id}
                onClick={() => onSelectConversation(c.id)}
                className={`group flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium transition-colors cursor-pointer ${
                  isActive
                    ? "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/40 dark:text-indigo-400 border border-indigo-100/50 dark:border-indigo-900/30"
                    : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-900/50"
                }`}
              >
                <span className="truncate pr-2">💬 {c.title || "未命名对话"}</span>
                <button
                  onClick={(e) => onDeleteConversation(c.id, e)}
                  className="opacity-0 group-hover:opacity-100 hover:text-red-500 p-0.5 text-[10px] rounded transition-opacity"
                  title="删除会话"
                >
                  ✕
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* 底部退出安全区 */}
      <div className="p-3 border-t border-slate-200 dark:border-slate-800 bg-slate-100/40 dark:bg-slate-900/10">
        <button
          onClick={onLogout}
          className="w-full py-1.5 rounded-lg text-center text-xs font-medium text-slate-500 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/20 transition-all cursor-pointer"
        >
          退出账户 {username} 🚪
        </button>
      </div>
    </aside>
  );
}