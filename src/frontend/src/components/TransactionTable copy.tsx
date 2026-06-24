"use client";
import type { QueryResultData } from "@/lib/types";
interface Props { data: QueryResultData; }
export default function TransactionTable({ data }: Props) {
  const { rows, columns, total } = data;
  const displayCols = columns.slice(0, 8);
  return (
    <div className="glass-card overflow-hidden">
      <div className="px-4 py-2 border-b text-xs font-semibold" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>记录明细 ({total})</div>
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b" style={{ borderColor: "var(--border)" }}>{displayCols.map(c => <th key={c} className="text-left px-3 py-2 text-xs whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{c}</th>)}</tr></thead>
      <tbody>{rows.slice(0,20).map((r,i) => <tr key={i} className="border-b" style={{ borderColor: "var(--border)" }}>{displayCols.map(c => { const v = r[c]; const isNum = typeof v === "number"; return <td key={c} className={"px-3 py-2 whitespace-nowrap text-xs " + (isNum ? "text-right font-medium" : "")} style={{ color: isNum ? "var(--warning)" : "var(--text-secondary)" }}>{isNum ? (v as number) >= 100 ? (v as number).toLocaleString() : String(v) : String(v ?? "")}</td>; })}</tr>)}</tbody></table></div>
    </div>
  );
}
