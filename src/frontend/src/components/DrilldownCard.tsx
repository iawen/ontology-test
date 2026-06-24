"use client";

import type { DrilldownData } from "@/lib/types";

interface Props {
  data: DrilldownData;
  onDrill: (option: DrilldownData["options"][0]) => void;
}

export default function DrilldownCard({ data, onDrill }: Props) {
  const icons: Record<string, string> = {
    drill: "🔍",
    raw_data: "📋",
    compare: "📊",
  };

  return (
    <div className="my-3 rounded-xl border border-indigo-300/40 bg-indigo-50/60 dark:bg-indigo-900/20 dark:border-indigo-700/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-indigo-200/50 dark:border-indigo-700/30 flex items-center gap-2">
        <span className="text-lg">🔎</span>
        <span className="font-semibold text-indigo-800 dark:text-indigo-300 text-sm">
          深入分析
        </span>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm text-indigo-900 dark:text-indigo-200 mb-3">{data.summary}</p>
        <div className="space-y-2">
          {data.options.map((opt, i) => (
            <button
              key={i}
              onClick={() => onDrill(opt)}
              className="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all
                bg-white dark:bg-slate-800 border border-indigo-200 dark:border-indigo-700/50
                hover:bg-indigo-100 dark:hover:bg-indigo-800/30
                hover:border-indigo-400 dark:hover:border-indigo-500
                hover:shadow-sm active:scale-[0.99]
                flex items-center gap-2"
            >
              <span className="text-base flex-shrink-0">
                {icons[opt.action || "drill"] || "🔍"}
              </span>
              <div className="min-w-0">
                <div className="font-medium text-indigo-800 dark:text-indigo-300 truncate">
                  {opt.label}
                </div>
                {opt.description && (
                  <div className="text-xs text-indigo-600/70 dark:text-indigo-400/70 truncate">
                    {opt.description}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
