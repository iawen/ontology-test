"use client";

import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { GaugeChart, BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { DashboardData } from "@/lib/types";

echarts.use([GaugeChart, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

interface Props {
  data: DashboardData;
}

export default function DashboardChart({ data }: Props) {
  const rate = Math.round(data.overall_execution_rate * 100);

  const gaugeOption = {
    series: [{
      type: "gauge", startAngle: 200, endAngle: -20, min: 0, max: 100,
      itemStyle: { color: rate >= 80 ? "#ef4444" : rate >= 60 ? "#f59e0b" : "#22c55e" },
      progress: { show: true, width: 20 },
      pointer: { show: false },
      axisLine: { lineStyle: { width: 20, color: [[1, "#e5e7eb"]] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      title: { show: true, offsetCenter: [0, "70%"], fontSize: 14, color: "#6b7280" },
      detail: { valueAnimation: true, fontSize: 32, fontWeight: "bold", offsetCenter: [0, "40%"], formatter: "{value}%", color: rate >= 80 ? "#ef4444" : rate >= 60 ? "#f59e0b" : "#22c55e" },
      data: [{ value: rate, name: "总执行率" }],
    }],
  };

  const barOption = {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { data: ["预算", "已执行"], top: 0 },
    grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
    xAxis: { type: "category", data: data.categories.map((c) => c.name) },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => `¥${(v / 10000).toFixed(0)}万` } },
    series: [
      { name: "预算", type: "bar", stack: "total", itemStyle: { color: "#93c5fd", borderRadius: [0, 0, 4, 4] }, data: data.categories.map((c) => c.budget) },
      { name: "已执行", type: "bar", stack: "total", itemStyle: { color: "#6366f1", borderRadius: [4, 4, 0, 0] }, data: data.categories.map((c) => c.spent) },
    ],
  };

  const lineOption = {
    tooltip: { trigger: "axis" },
    legend: { data: ["执行率"], top: 0 },
    grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
    xAxis: { type: "category", data: data.monthly_trend.map((m) => m.month) },
    yAxis: { type: "value", axisLabel: { formatter: "{value}%" }, min: 0, max: 100 },
    series: [{
      name: "执行率", type: "line", smooth: true,
      lineStyle: { width: 3, color: "#6366f1" },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(99,102,241,0.3)" }, { offset: 1, color: "rgba(99,102,241,0.02)" }]) },
      data: data.monthly_trend.map((m) => Math.round(m.execution_rate * 100)),
    }],
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm flex items-center justify-center">
          <ReactEChartsCore echarts={echarts} option={gaugeOption} style={{ height: 200 }} notMerge />
        </div>
        <div className="lg:col-span-2 grid grid-cols-3 gap-3">
          {data.categories.map((c) => (
            <div key={c.name} className={`rounded-xl p-4 border ${c.status === "warning" ? "bg-amber-50 border-amber-200" : "bg-green-50 border-green-200"}`}>
              <div className="text-xs text-gray-500 font-medium">{c.name}</div>
              <div className={`text-2xl font-bold mt-1 ${c.status === "warning" ? "text-amber-600" : "text-green-600"}`}>{Math.round(c.execution_rate * 100)}%</div>
              <div className="text-xs text-gray-400 mt-1">¥{(c.spent / 10000).toFixed(0)}万 / ¥{(c.budget / 10000).toFixed(0)}万</div>
            </div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
          <h4 className="text-sm font-semibold text-gray-600 mb-2">各科目预算 vs 实际</h4>
          <ReactEChartsCore echarts={echarts} option={barOption} style={{ height: 280 }} notMerge />
        </div>
        <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
          <h4 className="text-sm font-semibold text-gray-600 mb-2">月度执行趋势</h4>
          <ReactEChartsCore echarts={echarts} option={lineOption} style={{ height: 280 }} notMerge />
        </div>
      </div>
      {data.categories.some((c) => c.status === "warning") && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3">
          <span className="text-amber-500 text-lg mt-0.5">⚠️</span>
          <div>
            <div className="text-sm font-semibold text-amber-700">超支风险预警</div>
            <div className="text-sm text-amber-600 mt-1">
              「{data.categories.find((c) => c.status === "warning")?.name}」执行率达 {Math.round((data.categories.find((c) => c.status === "warning")?.execution_rate || 0) * 100)}%，高于时间进度（Q2 已过约 55%），建议关注投放节奏。
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
