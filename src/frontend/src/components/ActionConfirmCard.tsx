"use client";

import { useState } from "react";
import type { ActionConfirmData } from "@/lib/types";

interface Props {
  data: ActionConfirmData;
  onConfirm: (actionId: string) => void;
  onCancel: () => void;
}

export default function ActionConfirmCard({ data, onConfirm, onCancel }: Props) {
  const [loading, setLoading] = useState(false);

  const typeIcons: Record<string, string> = {
    notification: "🔔",
    webhook: "🔗",
    email: "📧",
    data_update: "📝",
    workflow: "⚙️",
  };

  const typeLabels: Record<string, string> = {
    notification: "通知",
    webhook: "Webhook",
    email: "邮件",
    data_update: "数据更新",
    workflow: "工作流",
  };

  const handleConfirm = async () => {
    setLoading(true);
    try {
      onConfirm(data.action_id);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-emerald-300/40 bg-emerald-50/60 dark:bg-emerald-900/20 dark:border-emerald-700/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-emerald-200/50 dark:border-emerald-700/30 flex items-center gap-2">
        <span className="text-lg">{typeIcons[data.action_type] || "🚀"}</span>
        <span className="font-semibold text-emerald-800 dark:text-emerald-300 text-sm">
          行动确认
        </span>
        <span className="ml-auto px-2 py-0.5 rounded-full text-xs bg-emerald-100 dark:bg-emerald-800/50 text-emerald-700 dark:text-emerald-300">
          {typeLabels[data.action_type] || data.action_type}
        </span>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm text-emerald-900 dark:text-emerald-200 mb-1 font-medium">
          {data.action_name}
        </p>
        {data.description && (
          <p className="text-xs text-emerald-700/70 dark:text-emerald-400/70 mb-2">
            {data.description}
          </p>
        )}
        <p className="text-sm text-emerald-800 dark:text-emerald-200 mb-3">
          {data.message}
        </p>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-all
              bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600
              text-slate-600 dark:text-slate-300
              hover:bg-slate-50 dark:hover:bg-slate-700
              disabled:opacity-50"
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-all
              bg-emerald-600 text-white
              hover:bg-emerald-700
              disabled:opacity-50 disabled:cursor-not-allowed
              flex items-center gap-1.5"
          >
            {loading && (
              <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            确认执行
          </button>
        </div>
      </div>
    </div>
  );
}
