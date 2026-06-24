"use client";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, PieChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { QueryResultData, ChartConfigData } from "@/lib/types";

echarts.use([BarChart, PieChart, LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, CanvasRenderer]);

const PALETTE = ["#6366f1", "#22d3ee", "#f59e0b", "#34d399", "#f43f5e", "#a78bfa", "#fb923c", "#2dd4bf"];

interface Props {
  data: QueryResultData;
  chartConfig?: ChartConfigData;
  onDrilldown?: (dimension: string, value: string) => void;
}

/** 自动推断图表类型 */
function inferChartType(data: QueryResultData): "bar" | "pie" | "line" {
  const { rows, columns, aggregated, dimensions } = data;
  if (!aggregated || !dimensions || dimensions.length === 0 || rows.length === 0) return "bar";
  // 数据量 <= 8 适合饼图
  if (rows.length <= 8 && dimensions.length === 1) return "pie";
  // 默认柱状图
  return "bar";
}

/** 自动检测数值列和维度列 */
function detectColumns(data: QueryResultData) {
  const { rows, columns } = data;
  const numericCols = columns.filter((col) => {
    const vals = rows.slice(0, 20).map((r) => Number(r[col]));
    return vals.filter((v) => !isNaN(v) && v !== 0).length > 3;
  });
  const dimCols = columns.filter((col) => !numericCols.includes(col));
  return { numericCols, dimCols };
}

/** 生成 ECharts 配置 */
function buildEChartsOption(data: QueryResultData, chartType: string) {
  const { rows, columns, dimensions, class_name } = data;
  const { numericCols, dimCols } = detectColumns(data);
  const dimKey = dimensions?.[0] || dimCols[0];

  if (chartType === "pie" && dimKey && numericCols.length > 0) {
    const pieData = rows.map((r, i) => ({
      name: String(r[dimKey] ?? "").length > 14 ? String(r[dimKey]).slice(0, 14) + "..." : String(r[dimKey] ?? ""),
      value: Number(r[numericCols[0]]) || 0,
      itemStyle: { color: PALETTE[i % PALETTE.length] },
    }));
    return {
      title: { text: class_name, left: "center", textStyle: { fontSize: 14, fontWeight: 500 } },
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { orient: "vertical", right: 10, top: "center", type: "scroll" },
      series: [{
        type: "pie", radius: ["35%", "65%"], center: ["40%", "55%"],
        data: pieData, label: { formatter: "{b}\n{d}%", fontSize: 11 },
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.2)" } },
      }],
    };
  }

  // 柱状图 / 折线图
  if (dimKey && numericCols.length > 0) {
    const labels = rows.map((r) => {
      const raw = String(r[dimKey] ?? "");
      return raw.length > 14 ? raw.slice(0, 14) + "..." : raw;
    });
    const series = numericCols.slice(0, 4).map((col, si) => ({
      name: col,
      type: chartType === "line" ? "line" : "bar",
      data: rows.map((r) => Number(r[col]) || 0),
      itemStyle: { color: PALETTE[si % PALETTE.length] },
      smooth: chartType === "line",
    }));
    return {
      title: { text: class_name, left: "center", textStyle: { fontSize: 14, fontWeight: 500 } },
      tooltip: { trigger: "axis" },
      legend: { data: numericCols.slice(0, 4), top: 25 },
      grid: { left: "3%", right: "4%", bottom: "3%", top: 60, containLabel: true },
      xAxis: { type: "category", data: labels, axisLabel: { rotate: labels.length > 6 ? 30 : 0, fontSize: 10 } },
      yAxis: { type: "value" },
      series,
    };
  }

  return null;
}

export default function QueryResult({ data, chartConfig, onDrilldown }: Props) {
  const { rows, columns, class_name, total, aggregated, dimensions } = data;
  const displayCols = columns.slice(0, 10);

  // 自动推断图表类型
  const chartType = chartConfig?.chart_type || inferChartType(data);
  const option = buildEChartsOption(data, chartType);

  // 维度列（可下钻）
  const drillableDims = dimensions || [];
  const { dimCols } = detectColumns(data);

  return (
    <div className="my-3 space-y-3">
      {/* 图表区域 */}
      {option && aggregated && rows.length > 0 && (
        <div className="rounded-xl border border-slate-200/60 dark:border-slate-700/40 bg-white/80 dark:bg-slate-800/60 overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 dark:border-slate-700/30">
            <span className="text-lg">📊</span>
            <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
              {class_name}
            </span>
            <span className="text-xs ml-auto" style={{ color: "var(--text-muted)" }}>
              {total} 条
            </span>
          </div>
          <div className="p-2" style={{ minHeight: 280 }}>
            <ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} />
          </div>
        </div>
      )}

      {/* 下钻提示 */}
      {aggregated && drillableDims.length > 0 && onDrilldown && (
        <div className="flex flex-wrap gap-1.5 px-1">
          {drillableDims.map((dim) => (
            <span key={dim} className="text-xs text-slate-400">
              💡 点击 {dim} 值可下钻查看明细
            </span>
          ))}
        </div>
      )}

      {/* 数据表格 */}
      <div className="rounded-xl border border-slate-200/60 dark:border-slate-700/40 bg-white/80 dark:bg-slate-800/60 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 dark:border-slate-700/30">
          <span className="text-lg">📋</span>
          <span className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>
            {aggregated ? "数据明细" : class_name}
          </span>
          <span className="text-xs ml-auto" style={{ color: "var(--text-muted)" }}>
            {total} 条
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                {displayCols.map((col) => (
                  <th key={col} className="text-left px-3 py-2 font-medium text-xs whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 20).map((r, i) => (
                <tr
                  key={i}
                  className="border-b hover:bg-slate-50/50 dark:hover:bg-slate-700/20 cursor-pointer transition-colors"
                  style={{ borderColor: "var(--border)" }}
                  onClick={() => {
                    // 点击行触发下钻
                    if (onDrilldown && dimCols.length > 0) {
                      const dim = dimCols[0];
                      const val = String(r[dim] ?? "");
                      if (val) onDrilldown(dim, val);
                    }
                  }}
                >
                  {displayCols.map((col) => {
                    const v = r[col];
                    const isNum = typeof v === "number";
                    return (
                      <td
                        key={col}
                        className={"px-3 py-2 whitespace-nowrap text-xs " + (isNum ? "text-right font-medium" : "")}
                        style={{ color: isNum ? "var(--warning)" : "var(--text-secondary)" }}
                      >
                        {isNum ? (v as number) >= 100 ? (v as number).toLocaleString() : String(v) : String(v ?? "")}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
