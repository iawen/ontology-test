"use client";

import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { PieChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { CategoryDrilldownData } from "@/lib/types";

echarts.use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer]);

const COLORS = {
  indigo: "#818cf8",
  purple: "#a78bfa",
  cyan: "#22d3ee",
  amber: "#fbbf24",
  emerald: "#34d399",
  rose: "#fb7185",
  bg: "rgba(15, 23, 42, 0.9)",
  border: "rgba(255,255,255,0.06)",
  text: "#94a3b8",
  textLight: "#cbd5e1",
};

const PIE_COLORS = [COLORS.indigo, COLORS.cyan, COLORS.amber, COLORS.emerald, COLORS.rose, COLORS.purple];

interface Props {
  data: CategoryDrilldownData;
}

export default function CategoryPieChart({ data }: Props) {
  const rate = Math.round(data.category_execution_rate * 100);

  const option = {
    tooltip: {
      trigger: "item",
      backgroundColor: COLORS.bg,
      borderColor: COLORS.border,
      textStyle: { color: COLORS.textLight, fontSize: 12 },
      formatter: (params: { name: string; value: number; percent: number }) =>
        `<strong>${params.name}</strong><br/>已执行: ¥${params.value.toLocaleString()}<br/>占比: ${params.percent}%`,
    },
    legend: {
      orient: "vertical", right: "5%", top: "center",
      textStyle: { color: COLORS.text, fontSize: 12 },
    },
    series: [{
      name: "已执行金额", type: "pie",
      radius: ["42%", "72%"], center: ["35%", "50%"],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: "rgba(10,14,26,0.8)", borderWidth: 3 },
      label: { show: true, formatter: "{b}", fontSize: 11, color: COLORS.text },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: "bold", color: "#fff" },
        itemStyle: { shadowBlur: 20, shadowColor: "rgba(0,0,0,0.4)" },
      },
      data: data.sub_categories.map((s, i) => ({
        name: s.name,
        value: s.spent,
        itemStyle: { color: PIE_COLORS[i % PIE_COLORS.length] },
      })),
    }],
  };

  return (
    <div className="space-y-4">
      {/* 科目概览 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">科目预算</div>
          <div className="text-lg font-bold text-white">¥{(data.category_budget / 10000).toFixed(0)}<span className="text-xs text-slate-400 ml-0.5">万</span></div>
        </div>
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">已执行</div>
          <div className="text-lg font-bold text-indigo-400">¥{(data.category_spent / 10000).toFixed(0)}<span className="text-xs text-slate-400 ml-0.5">万</span></div>
        </div>
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">执行率</div>
          <div className={`text-lg font-bold ${rate >= 80 ? "text-rose-400" : rate >= 60 ? "text-amber-400" : "text-emerald-400"}`}>{rate}%</div>
        </div>
      </div>

      {/* 饼图 */}
      <div className="glass-card p-4">
        <ReactEChartsCore echarts={echarts} option={option} style={{ height: 260 }} notMerge />
      </div>

      {/* 子科目明细表 */}
      <div className="glass-card overflow-hidden">
        <div className="px-4 py-3 border-b border-white/[0.06]">
          <h4 className="text-sm font-semibold text-slate-300">子科目明细</h4>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.04]">
              <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">子科目</th>
              <th className="text-right px-4 py-2.5 font-medium text-slate-500 text-xs">预算</th>
              <th className="text-right px-4 py-2.5 font-medium text-slate-500 text-xs">已执行</th>
              <th className="text-right px-4 py-2.5 font-medium text-slate-500 text-xs">执行率</th>
            </tr>
          </thead>
          <tbody>
            {data.sub_categories.map((s, i) => (
              <tr key={s.name} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                <td className="px-4 py-3 font-medium text-slate-200 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                  {s.name}
                </td>
                <td className="px-4 py-3 text-right text-slate-400">¥{s.budget.toLocaleString()}</td>
                <td className="px-4 py-3 text-right text-slate-300">¥{s.spent.toLocaleString()}</td>
                <td className="px-4 py-3 text-right">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                    s.execution_rate >= 0.8 ? "bg-rose-500/10 text-rose-400" :
                    s.execution_rate >= 0.6 ? "bg-amber-500/10 text-amber-400" :
                    "bg-emerald-500/10 text-emerald-400"
                  }`}>
                    {Math.round(s.execution_rate * 100)}%
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
