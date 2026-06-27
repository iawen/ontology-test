"use client";

import React, { useState } from "react";
import type { ToolStepsPanelProps } from "@/lib/types";

// 工具英文标识到人性化中文名称的映射
const TOOL_NAMES_MAP: Record<string, string> = {
  get_ontology_schema: "探索数据模型",
  query_ontology_data: "多维指标查询",
  fuzzy_search_values: "模糊匹配字段值",
  get_class_sample: "查看样本数据",
  python_analyze: "Python 数据分析",
  explore_schema: "探索数据模型",
  query_data: "多维指标查询",
  query_raw_data: "原始明细检索",
  lookup_metric: "指标口径匹配",
  drill_down: "维度下钻",
  python_analyst: "Python 数据分析",
  execute_action: "执行动作",
  list_available_actions: "可用动作甄选",
};

function formatDetail(value: unknown) {
  if (typeof value !== "string") {
    return JSON.stringify(value, null, 2);
  }

  const trimmed = value.trim();
  if (!trimmed) return "";

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return value;
    }
  }

  return value;
}

function formatClock(timestamp?: number) {
  if (!timestamp) return "--:--:--";
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(durationMs?: number) {
  if (durationMs === undefined) return "执行中";
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}


export default function ToolStepsPanel({ steps }: ToolStepsPanelProps) {
  const [isOpen, setIsOpen] = useState(true);
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (!steps || steps.length === 0) return null;

  const hasRunning = steps.some((s) => s.status === "running");

  return (
    <div className="my-3 border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden bg-white dark:bg-slate-900 transition-all shadow-sm">
      {/* 头部标题控制栏 */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-3.5 py-2.5 flex items-center justify-between text-xs font-medium text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="flex h-2 w-2 relative">
            {hasRunning && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
            )}
            <span
              className={`relative inline-flex rounded-full h-2 w-2 ${
                hasRunning ? "bg-amber-500" : "bg-emerald-500"
              }`}
            ></span>
          </span>
          <span>工具执行路径 ({steps.length} 步)</span>
        </div>
        <span className="text-[10px] opacity-70">{isOpen ? "收起" : "展开"}</span>
      </button>

      {/* 步骤时间轴列表 */}
      {isOpen && (
        <div className="p-3.5 space-y-3 relative before:absolute before:top-5 before:bottom-5 before:left-5 before:w-px before:bg-slate-200 dark:before:bg-slate-800">
          {steps.map((step, idx) => {
            const isRunning = step.status === "running";
            const isCompleted = step.status === "completed";
            const isFailed = step.status === "failed";
            const isExpanded = expandedIndex === idx;

            return (
              <div key={idx} className="relative pl-7 text-sm group transition-all">
                {/* 时间轴左侧 ICON 节点 */}
                <div className="absolute left-3 top-1 -translate-x-1/2 flex items-center justify-center z-10">
                  {isRunning && (
                    <div className="w-3 h-3 rounded-full bg-amber-500 ring-4 ring-amber-100 dark:ring-amber-950 animate-pulse" />
                  )}
                  {isCompleted && (
                    <div className="w-3 h-3 rounded-full bg-emerald-500 ring-4 ring-emerald-100 dark:ring-emerald-950" />
                  )}
                  {isFailed && (
                    <div className="w-3 h-3 rounded-full bg-red-500 ring-4 ring-red-100 dark:ring-red-950" />
                  )}
                </div>

                {/* 节点主要内容 */}
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div
                      className={`font-medium transition-colors ${
                        isRunning
                          ? "text-amber-600 dark:text-amber-400 animate-pulse font-semibold"
                          : "text-slate-700 dark:text-slate-300"
                      }`}
                    >
                      {TOOL_NAMES_MAP[step.name] || step.name}
                    </div>
                    {step.description && (
                      <div className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                        {step.description}
                      </div>
                    )}
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-normal text-slate-400 dark:text-slate-500">
                      <span>开始 {formatClock(step.startedAt)}</span>
                      <span>总耗时 {formatDuration(step.durationMs)}</span>
                    </div>
                  </div>
                  {(step.args || step.result) && (
                    <button
                      onClick={() => setExpandedIndex(isExpanded ? null : idx)}
                      className="text-xs text-indigo-500 hover:text-indigo-600 dark:text-indigo-400 dark:hover:text-indigo-300 font-medium cursor-pointer"
                    >
                      {isExpanded ? "隐藏" : "详情"}
                    </button>
                  )}
                </div>

                {/* JSON 伸缩抽屉详情面板 */}
                {isExpanded && (
                  <div className="mt-2 space-y-2 text-xs font-mono bg-slate-50 dark:bg-slate-950 p-3 rounded-lg border border-slate-200 dark:border-slate-800 max-h-60 overflow-y-auto">
                    {step.args && Object.keys(step.args).length > 0 && (
                      <div>
                        <span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">输入参数</span>
                        <pre className="text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                          {JSON.stringify(step.args, null, 2)}
                        </pre>
                      </div>
                    )}
                    {step.result && (
                      <div>
                        <span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">返回结果</span>
                        <pre className="text-emerald-600 dark:text-emerald-400 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                          {formatDetail(step.result)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}