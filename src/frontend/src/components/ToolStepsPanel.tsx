"use client";

import React, { useState } from "react";
import type { ToolStepsPanelProps } from "@/lib/types";

// 工具英文标识到人性化中文名称的映射
const TOOL_NAMES_MAP: Record<string, string> = {
  explore_schema: "🔍 探索数据模型 (Schema Overview)",
  query_data: "📊 多维指标模型高阶查询",
  query_raw_data: "📋 原始明细穿透数据检索",
  lookup_metric: "📐 指标标准口径与定义匹配",
  drill_down: "🪵 核心维度多级下钻穿透",
  python_analyst: "🐍 Python 安全沙箱高级数据分析",
  execute_action: "⚡ 触发自动化闭环执行动作",
  list_available_actions: "🔔 场景可用动作包智能甄选",
};


export default function ToolStepsPanel({ steps }: ToolStepsPanelProps) {
  const [isOpen, setIsOpen] = useState(true);
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (!steps || steps.length === 0) return null;

  const hasRunning = steps.some((s) => s.status === "running");

  return (
    <div className="my-3 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden bg-slate-50/50 dark:bg-slate-900/20 transition-all shadow-sm">
      {/* 头部标题控制栏 */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-4 py-2.5 flex items-center justify-between text-xs font-medium text-slate-500 dark:text-slate-400 bg-slate-100/80 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-800 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
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
          <span>本体驱动认知循环路径 ({steps.length} 步)</span>
        </div>
        <span className="text-[10px] opacity-70">{isOpen ? "收起 ▲" : "展开 ▼"}</span>
      </button>

      {/* 步骤时间轴列表 */}
      {isOpen && (
        <div className="p-4 space-y-4 relative before:absolute before:top-6 before:bottom-6 before:left-6 before:w-0.5 before:bg-slate-200 dark:before:bg-slate-800">
          {steps.map((step, idx) => {
            const isRunning = step.status === "running";
            const isCompleted = step.status === "completed";
            const isFailed = step.status === "failed";
            const isExpanded = expandedIndex === idx;

            return (
              <div key={idx} className="relative pl-8 text-sm group transition-all">
                {/* 时间轴左侧 ICON 节点 */}
                <div className="absolute left-3.5 top-0.5 -translate-x-1/2 flex items-center justify-center z-10">
                  {isRunning && (
                    <div className="w-5 h-5 rounded-full bg-amber-50 dark:bg-amber-950 flex items-center justify-center text-amber-500 border border-amber-300 dark:border-amber-700 text-[11px] animate-spin">
                      ⚙️
                    </div>
                  )}
                  {isCompleted && (
                    <div className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-950 flex items-center justify-center text-emerald-600 dark:text-emerald-400 border border-emerald-300 dark:border-emerald-800 text-[10px] font-bold">
                      ✓
                    </div>
                  )}
                  {isFailed && (
                    <div className="w-5 h-5 rounded-full bg-red-100 dark:bg-red-950 flex items-center justify-center text-red-600 dark:text-red-400 border border-red-300 dark:border-red-800 text-[10px] font-bold">
                      ✕
                    </div>
                  )}
                </div>

                {/* 节点主要内容 */}
                <div className="flex items-center justify-between gap-4">
                  <span
                    className={`font-medium transition-colors ${
                      isRunning
                        ? "text-amber-600 dark:text-amber-400 animate-pulse font-semibold"
                        : "text-slate-700 dark:text-slate-300"
                    }`}
                  >
                    {TOOL_NAMES_MAP[step.name] || step.name}
                  </span>
                  {(step.args || step.result) && (
                    <button
                      onClick={() => setExpandedIndex(isExpanded ? null : idx)}
                      className="text-xs text-indigo-500 hover:text-indigo-600 dark:text-indigo-400 dark:hover:text-indigo-300 font-medium underline cursor-pointer"
                    >
                      {isExpanded ? "隐藏细节" : "查看细节"}
                    </button>
                  )}
                </div>

                {/* JSON 伸缩抽屉详情面板 */}
                {isExpanded && (
                  <div className="mt-2 space-y-2 text-xs font-mono bg-white dark:bg-slate-950 p-3 rounded-lg border border-slate-200 dark:border-slate-800 shadow-inner max-h-60 overflow-y-auto">
                    {step.args && Object.keys(step.args).length > 0 && (
                      <div>
                        <span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">📥 输入参数 (Arguments):</span>
                        <pre className="text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                          {JSON.stringify(step.args, null, 2)}
                        </pre>
                      </div>
                    )}
                    {step.result && (
                      <div>
                        <span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">📤 执行返回结果 (Result):</span>
                        <pre className="text-emerald-600 dark:text-emerald-400 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                          {typeof step.result === "string" && (step.result.startsWith("{") || step.result.startsWith("["))
                            ? JSON.stringify(JSON.parse(step.result), null, 2)
                            : String(step.result)}
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