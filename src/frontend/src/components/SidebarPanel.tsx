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
    <aside className="w-64 border-r border-black bg-deloitte-charcoal flex flex-col h-full flex-shrink-0 text-white">
      {/* 顶部场景切换 */}
      <div className="p-4 border-b border-white/10">
        <label className="block text-xs font-semibold text-white/50 uppercase tracking-wider mb-1.5">当前分析业务场景</label>
        <select
          value={currentScenario}
          onChange={(e) => onSwitchScenario(e.target.value)}
          className="w-full px-3 py-1.5 rounded-lg border border-white/15 bg-white/10 text-xs font-medium text-white focus:outline-none focus:border-deloitte-green"
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
          className="w-full py-2 px-3 mb-3 bg-deloitte-green border border-deloitte-green rounded-xl text-xs font-semibold text-deloitte-ink hover:bg-deloitte-green-dark hover:text-white flex items-center justify-center gap-1.5 transition-colors cursor-pointer shadow-sm"
        >
          ➕ 开启全新多维对话
        </button>

        <div className="text-[11px] font-bold text-white/45 px-2 pb-1 uppercase tracking-wider">历史认知会话</div>
        {conversations.length === 0 ? (
          <div className="text-xs text-white/45 text-center py-8">暂无历史会话</div>
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
                    ? "bg-deloitte-green text-deloitte-ink border border-deloitte-green"
                    : "text-white/70 hover:bg-white/10 hover:text-white"
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
                    className="min-w-0 flex-1 rounded border border-deloitte-green bg-white px-1.5 py-0.5 text-xs text-slate-700 outline-none"
                    aria-label="会话标题"
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate pr-2">💬 {c.title || "未命名对话"}</span>
                )}
                {!isEditing && <div className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    onClick={(event) => startRenaming(c, event)}
                    className="rounded p-0.5 text-[10px] hover:text-deloitte-green"
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
      <div className="p-3 border-t border-white/10 bg-black/10">
        <button
          onClick={onLogout}
          className="w-full py-1.5 rounded-lg text-center text-xs font-medium text-white/55 hover:text-white hover:bg-white/10 transition-all cursor-pointer"
        >
          退出账户 {username} 🚪
        </button>
      </div>
    </aside>
  );
}