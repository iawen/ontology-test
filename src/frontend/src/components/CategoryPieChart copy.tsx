"use client";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { PieChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { QueryResultData } from "@/lib/types";
echarts.use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer]);
const PALETTE = ["#6366f1","#22d3ee","#f59e0b","#34d399","#f43f5e","#a78bfa"];
interface Props { data: QueryResultData; }
export default function CategoryPieChart({ data }: Props) {
  const { rows, columns, dimensions } = data;
  const numericCols = columns.filter(c => rows.slice(0,10).some(r => !isNaN(Number(r[c])) && Number(r[c]) !== 0));
  const dimKey = (dimensions && dimensions[0]) || columns[0];
  const metricKey = numericCols[0] || columns[1];
  const pieData = rows.slice(0, 10).map(r => ({ name: String(r[dimKey] || "").slice(0, 14), value: Number(r[metricKey]) || 0 }));
  const option = {
    tooltip: { trigger: "item" },
    legend: { orient: "vertical", right: 10, top: "center", textStyle: { fontSize: 11 } },
    series: [{ type: "pie", radius: ["40%", "70%"], data: pieData, label: { fontSize: 10 }, itemStyle: { borderRadius: 6 } }],
    color: PALETTE,
  };
  return <div className="glass-card p-4"><ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} notMerge /></div>;
}
