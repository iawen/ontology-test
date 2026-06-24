"use client";

import type { PlanData } from "@/lib/types";

interface Props {
  data: PlanData;
}

export default function PlanProgressCard({ data }: Props) {
  const statusIcons: Record<string, string> = {
    pending: "⏳",
    running: "🔄",
    completed: "✅",
    failed: "❌",
  };

  const statusColors: Record<string, string> = {
    pending: "text-slate-400",
    running: "text-blue-500",
    completed: "text-emerald-500",
    failed: "text-red-500",
  };

  const completedCount = data.steps.filter((s) => s.status === "completed").length;
  const progress = data.steps.length > 0 ? (completedCount / data.steps.length) * 100 : 0;

  return (
    <div className="my-3 rounded-xl border border-blue-300/40 bg-blue-50/60 dark:bg-blue-900/20 dark:border-blue-700/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-blue-200/50 dark:border-blue-700/30">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-lg">📋</span>
          <span className="font-semibold text-blue-800 dark:text-blue-300 text-sm">
            分析计划
          </span>
          <span className="ml-auto text-xs text-blue-600/70 dark:text-blue-400/70">
            {completedCount}/{data.steps.length} 步骤完成
          </span>
        </div>
        <p className="text-xs text-blue-700/70 dark:text-blue-400/70 mb-2">
          {data.plan_description}
        </p>
        {/* Progress bar */}
        <div className="h-1.5 bg-blue-100 dark:bg-blue-800/50 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
      <div className="px-4 py-2 space-y-1">
        {data.steps.map((step, i) => (
          <div
            key={step.step_id || i}
            className={`flex items-center gap-2 py-1.5 text-sm ${
              statusColors[step.status || "pending"]
            }`}
          >
            <span className="text-sm flex-shrink-0">
              {step.status === "running" ? (
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                statusIcons[step.status || "pending"]
              )}
            </span>
            <span className="truncate">{step.description}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
