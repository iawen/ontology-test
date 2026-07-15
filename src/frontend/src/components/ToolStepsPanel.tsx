"use client";

import React, { useState } from "react";
import type { ToolStepsPanelProps } from "@/lib/types";

// 工具英文标识到人性化中文名称的映射
const TOOL_NAMES_MAP: Record<string, string> = {
  execution_mode_routing: "任务路由",
  ontology_recognition: "本体语义识别",
  metric_plan: "指标分析计划",
  metric_subquestion: "子问题",
  subquestion_scope: "子问题范围校验",
  subquestion_query_plan: "子问题查询规划",
  evidence_judgment: "证据充分性审核",
  metric_plan_complete: "指标计划完成",
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

interface StepTreeNode {
  step: ToolStepsPanelProps["steps"][number];
  children: StepTreeNode[];
}

function getSubquestionId(step: ToolStepsPanelProps["steps"][number]) {
  const args = step.args as Record<string, unknown> | undefined;
  const result = step.result as Record<string, unknown> | undefined;
  const metricPlan = args?._metric_plan as Record<string, unknown> | undefined;
  return String(metricPlan?.subquestion_id || result?.subquestion_id || "");
}

function buildStepTree(steps: ToolStepsPanelProps["steps"]): StepTreeNode[] {
  const roots: StepTreeNode[] = [];
  const subquestions = new Map<string, StepTreeNode>();

  for (const step of steps) {
    if (step.name === "metric_plan") {
      const result = step.result as Record<string, unknown> | undefined;
      const plannedSubquestions = Array.isArray(result?.subquestions) ? result.subquestions : [];
      const node: StepTreeNode = { step, children: [] };
      for (const item of plannedSubquestions) {
        if (!item || typeof item !== "object") continue;
        const subquestion = item as Record<string, unknown>;
        const id = String(subquestion.id || "");
        if (!id) continue;
        const subquestionNode: StepTreeNode = {
          step: {
            name: "metric_subquestion",
            description: String(subquestion.intent || "待执行子问题"),
            result: subquestion,
            status: "completed",
          },
          children: [],
        };
        node.children.push(subquestionNode);
        subquestions.set(id, subquestionNode);
      }
      roots.push(node);
      continue;
    }

    const subquestionId = getSubquestionId(step);
    const parent = subquestionId ? subquestions.get(subquestionId) : undefined;
    if (parent) {
      parent.children.push({ step, children: [] });
    } else {
      roots.push({ step, children: [] });
    }
  }
  return roots;
}


export default function ToolStepsPanel({ steps }: ToolStepsPanelProps) {
  const [isOpen, setIsOpen] = useState(true);
  const [expandedIndex, setExpandedIndex] = useState<string | null>(null);
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(new Set());

  if (!steps || steps.length === 0) return null;

  const hasRunning = steps.some((s) => s.status === "running");
  const stepTree = buildStepTree(steps);

  const renderStep = (node: StepTreeNode, key: string, depth = 0) => {
    const { step, children } = node;
    const isRunning = step.status === "running";
    const isCompleted = step.status === "completed";
    const isFailed = step.status === "failed";
    const isExpanded = expandedIndex === key;
    const canExpand = Boolean(step.args || step.result);
    const hasChildren = children.length > 0;
    const isCollapsed = collapsedNodes.has(key);

    const toggleChildren = () => {
      setCollapsedNodes((current) => {
        const next = new Set(current);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
    };

    return (
      <div key={key} className={`relative text-sm group transition-all ${depth ? "mt-2 border-l border-deloitte-line pl-4 dark:border-deloitte-green/50" : "pl-7"}`}>
        <div className={depth ? "absolute -left-1.5 top-1.5 z-10 h-2.5 w-2.5 rounded-full bg-deloitte-green ring-4 ring-white dark:ring-slate-900" : "absolute left-3 top-1 -translate-x-1/2 flex items-center justify-center z-10"}>
          {!depth && isRunning && <div className="w-3 h-3 rounded-full bg-amber-500 ring-4 ring-amber-100 dark:ring-amber-950 animate-pulse" />}
          {!depth && isCompleted && <div className="w-3 h-3 rounded-full bg-emerald-500 ring-4 ring-emerald-100 dark:ring-emerald-950" />}
          {!depth && isFailed && <div className="w-3 h-3 rounded-full bg-red-500 ring-4 ring-red-100 dark:ring-red-950" />}
        </div>

        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className={`font-medium transition-colors ${isRunning ? "text-amber-600 dark:text-amber-400 animate-pulse font-semibold" : "text-slate-700 dark:text-slate-300"}`}>
              {TOOL_NAMES_MAP[step.name] || step.name}
            </div>
            {step.description && <div className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{step.description}</div>}
            {!depth && <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-normal text-slate-400 dark:text-slate-500"><span>开始 {formatClock(step.startedAt)}</span><span>总耗时 {formatDuration(step.durationMs)}</span></div>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {hasChildren && <button onClick={toggleChildren} className="whitespace-nowrap text-xs text-slate-500 hover:text-deloitte-green-dark dark:text-slate-400 dark:hover:text-deloitte-green font-medium cursor-pointer">{isCollapsed ? `展开子任务 (${children.length})` : "收起子任务"}</button>}
            {canExpand && <button onClick={() => setExpandedIndex(isExpanded ? null : key)} className="whitespace-nowrap text-xs text-deloitte-green-dark hover:text-deloitte-green-dark dark:text-deloitte-green dark:hover:text-deloitte-green-light font-medium cursor-pointer">{isExpanded ? "隐藏" : "详情"}</button>}
          </div>
        </div>

        {isExpanded && <div className="mt-2 space-y-2 text-xs font-mono bg-slate-50 dark:bg-slate-950 p-3 rounded-lg border border-slate-200 dark:border-slate-800 max-h-60 overflow-y-auto">
          {step.args && Object.keys(step.args).length > 0 && <div><span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">输入参数</span><pre className="text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">{JSON.stringify(step.args, null, 2)}</pre></div>}
          {step.result && <div><span className="text-slate-400 dark:text-slate-500 font-bold block mb-1">返回结果</span><pre className="text-emerald-600 dark:text-emerald-400 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-100 dark:border-slate-800/60 overflow-x-auto whitespace-pre-wrap break-all">{formatDetail(step.result)}</pre></div>}
        </div>}

        {hasChildren && !isCollapsed && <div className="mt-2 space-y-2">{children.map((child, index) => renderStep(child, `${key}-${index + 1}`, depth + 1))}</div>}
      </div>
    );
  };

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
          {stepTree.map((node, idx) => renderStep(node, String(idx)))}
        </div>
      )}
    </div>
  );
}