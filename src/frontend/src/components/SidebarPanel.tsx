"use client";

import React, { useRef, useState } from "react";
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
  onRenameConversation: (id: string, title: string) => Promise<void>;
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
  onRenameConversation,
  onDeleteConversation,
  onNewConversation,
  onLogout,
}: SidebarPanelProps) {
  const [editingConversationId, setEditingConversationId] = useState("");
  const [editingTitle, setEditingTitle] = useState("");
  const cancelledRenameId = useRef("");
  const savingRenameId = useRef("");

  const startRenaming = (conversation: Conversation, event: React.MouseEvent) => {
    event.stopPropagation();
    cancelledRenameId.current = "";
    setEditingConversationId(conversation.id);
    setEditingTitle(conversation.title || "");
  };

  const finishRenaming = async () => {
    const title = editingTitle.trim();
    const conversationId = editingConversationId;
    setEditingConversationId("");
    if (!conversationId || !title || cancelledRenameId.current === conversationId || savingRenameId.current === conversationId) return;
    savingRenameId.current = conversationId;
    try {
      await onRenameConversation(conversationId, title);
    } finally {
      savingRenameId.current = "";
    }
  };

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
            const isEditing = c.id === editingConversationId;
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
                {isEditing ? (
                  <input
                    autoFocus
                    value={editingTitle}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => setEditingTitle(event.target.value)}
                    onBlur={() => void finishRenaming()}
                    onKeyDown={(event) => {
                      event.stopPropagation();
                      if (event.key === "Enter") event.currentTarget.blur();
                      if (event.key === "Escape") {
                        cancelledRenameId.current = c.id;
                        setEditingConversationId("");
                      }
                    }}
                    className="min-w-0 flex-1 rounded border border-indigo-300 bg-white px-1.5 py-0.5 text-xs text-slate-700 outline-none dark:border-indigo-600 dark:bg-slate-900 dark:text-slate-200"
                    aria-label="会话标题"
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate pr-2">💬 {c.title || "未命名对话"}</span>
                )}
                {!isEditing && <div className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    onClick={(event) => startRenaming(c, event)}
                    className="rounded p-0.5 text-[10px] hover:text-indigo-500"
                    title="修改标题"
                    aria-label="修改会话标题"
                  >
                    ✎
                  </button>
                  <button
                    onClick={(event) => onDeleteConversation(c.id, event)}
                    className="rounded p-0.5 text-[10px] hover:text-red-500"
                    title="删除会话"
                  >
                    ✕
                  </button>
                </div>}
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