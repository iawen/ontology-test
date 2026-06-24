"use client";

/**
 * 场景选择器 — 嵌入到每个与场景相关的模块中
 *
 * 核心设计：
 * - 场景激活是前端用户偏好，独立于后端 is_active 字段
 * - 选择结果持久化到 localStorage
 * - 切换场景时自动清除该场景相关页面的缓存，触发数据刷新
 */

import { useApp } from "@/contexts/AppContext";
import { invalidateCacheByPrefix } from "@/lib/cache";

export default function ScenarioSelector() {
  const { scenarios, activeScenario, setActiveScenario } = useApp();

  const handleChange = (id: string) => {
    /* 切换场景时，清除旧场景的缓存，让各页面重新加载 */
    invalidateCacheByPrefix("data:");
    invalidateCacheByPrefix("schema:");
    invalidateCacheByPrefix("concepts:");
    invalidateCacheByPrefix("metrics:");
    invalidateCacheByPrefix("chart_rules:");
    invalidateCacheByPrefix("glossary:");
    invalidateCacheByPrefix("skills:");
    invalidateCacheByPrefix("extraction_logs:");
    invalidateCacheByPrefix("dashboard:");
    setActiveScenario(id);
  };

  if (scenarios.length === 0) {
    return (
      <div className="flex items-center gap-2 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 mb-4">
        <span>⚠️</span>
        <span>请先在「场景列表」中创建一个场景，然后在此选择</span>
      </div>
    );
  }

  const current = scenarios.find((s) => s.id === activeScenario);

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-lg mb-4">
      <span className="text-xs text-slate-500 font-medium whitespace-nowrap">当前场景</span>
      <select
        value={activeScenario}
        onChange={(e) => handleChange(e.target.value)}
        className="text-sm border-slate-200 bg-white min-w-[160px]"
      >
        <option value="" disabled>
          选择场景...
        </option>
        {scenarios.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      {current && current.description && (
        <span className="text-xs text-slate-400 truncate max-w-xs">{current.description}</span>
      )}
      {activeScenario && (
        <span className="ml-auto flex items-center gap-1 text-xs text-emerald-600 whitespace-nowrap">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          已选择
        </span>
      )}
    </div>
  );
}
