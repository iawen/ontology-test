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
    <div className="my-3 rounded-xl border border-deloitte-line border-l-4 border-l-deloitte-green bg-deloitte-green-light/50 dark:bg-deloitte-green/10 dark:border-deloitte-green/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-deloitte-line dark:border-deloitte-green/30 flex items-center gap-2">
        <span className="text-lg">🔎</span>
        <span className="font-semibold text-deloitte-green-dark dark:text-deloitte-green-light text-sm">
          深入分析
        </span>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm text-slate-800 dark:text-slate-200 mb-3">{data.summary}</p>
        <div className="space-y-2">
          {data.options.map((opt, i) => (
            <button
              key={i}
              onClick={() => onDrill(opt)}
              className="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all
                bg-white dark:bg-slate-800 border border-deloitte-line dark:border-deloitte-green/50
                hover:bg-deloitte-green-light dark:hover:bg-deloitte-green/20
                hover:border-deloitte-green dark:hover:border-deloitte-green
                hover:shadow-sm active:scale-[0.99]
                flex items-center gap-2"
            >
              <span className="text-base flex-shrink-0">
                {icons[opt.action || "drill"] || "🔍"}
              </span>
              <div className="min-w-0">
                <div className="font-medium text-deloitte-green-dark dark:text-deloitte-green-light truncate">
                  {opt.label}
                </div>
                {opt.description && (
                  <div className="text-xs text-deloitte-green-dark/70 dark:text-deloitte-green/70 truncate">
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
