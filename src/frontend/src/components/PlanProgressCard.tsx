"use client";

import type { PlanData } from "@/lib/types";

interface Props {
  data: PlanData;
}

export default function PlanProgressCard({ data }: Props) {
  const statusColors: Record<string, string> = {
    pending: "text-slate-400",
    running: "text-amber-600 dark:text-amber-400",
    completed: "text-deloitte-green-dark",
    failed: "text-red-500",
  };

  const completedCount = data.steps.filter((s) => s.status === "completed").length;
  const progress = data.steps.length > 0 ? (completedCount / data.steps.length) * 100 : 0;

  return (
    <div className="my-3 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 overflow-hidden shadow-sm">
      <div className="px-3.5 py-3 border-b border-slate-200 dark:border-slate-800">
        <div className="flex items-center gap-2 mb-2">
          <span className="font-semibold text-slate-800 dark:text-slate-200 text-sm">
            分析计划
          </span>
          <span className="ml-auto text-xs text-slate-500 dark:text-slate-400">
            {completedCount}/{data.steps.length} 步骤完成
          </span>
        </div>
        {data.plan_description && (
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">
            {data.plan_description}
          </p>
        )}
        <div className="h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-deloitte-green rounded-full transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
      <div className="px-3.5 py-2 space-y-1">
        {data.steps.map((step, i) => (
          <div
            key={step.step_id || i}
            className={`flex items-center gap-2 py-1.5 text-sm ${
              statusColors[step.status || "pending"]
            }`}
          >
            <span className="flex-shrink-0">
              {step.status === "running" ? (
                <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <span className="block h-2 w-2 rounded-full bg-current opacity-70" />
              )}
            </span>
            <span className="truncate">{step.description}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
