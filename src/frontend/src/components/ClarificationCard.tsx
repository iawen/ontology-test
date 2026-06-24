"use client";

import type { ClarificationData } from "@/lib/types";

interface Props {
  data: ClarificationData;
  onSelect: (optionId: string, value: string) => void;
}

export default function ClarificationCard({ data, onSelect }: Props) {
  return (
    <div className="my-3 rounded-xl border border-amber-300/40 bg-amber-50/60 dark:bg-amber-900/20 dark:border-amber-700/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-amber-200/50 dark:border-amber-700/30 flex items-center gap-2">
        <span className="text-lg">🤔</span>
        <span className="font-semibold text-amber-800 dark:text-amber-300 text-sm">
          需要确认
        </span>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm text-amber-900 dark:text-amber-200 mb-3">{data.question}</p>
        <div className="flex flex-wrap gap-2">
          {data.options.map((opt) => (
            <button
              key={opt.id}
              onClick={() => onSelect(opt.id, opt.value || opt.label)}
              className="px-3 py-1.5 rounded-lg text-sm font-medium transition-all
                bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-700/50
                text-amber-800 dark:text-amber-300
                hover:bg-amber-100 dark:hover:bg-amber-800/40
                hover:border-amber-400 dark:hover:border-amber-500
                hover:shadow-sm active:scale-95"
            >
              {opt.label}
              {opt.description && (
                <span className="ml-1 text-xs opacity-60">{opt.description}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
