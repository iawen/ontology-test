"use client";

import type { TransactionDetailData } from "@/lib/types";

interface Props {
  data: TransactionDetailData;
}

export default function TransactionTable({ data }: Props) {
  const totalAmount = data.transactions.reduce((sum, t) => sum + t.amount, 0);

  return (
    <div className="space-y-4">
      {/* 汇总卡片 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">交易笔数</div>
          <div className="text-lg font-bold text-indigo-400">{data.transactions.length}<span className="text-xs text-slate-400 ml-0.5">笔</span></div>
        </div>
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">交易总金额</div>
          <div className="text-lg font-bold text-amber-400">¥{totalAmount.toLocaleString()}</div>
        </div>
        <div className="glass-card p-4 text-center">
          <div className="text-xs text-slate-500 mb-1">子科目</div>
          <div className="text-lg font-bold text-cyan-400">{data.sub_category_name}</div>
        </div>
      </div>

      {/* 交易明细表 */}
      <div className="glass-card overflow-hidden">
        <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-2">
          <svg className="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          <h4 className="text-sm font-semibold text-slate-300">原始交易明细</h4>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.04]">
                <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">日期</th>
                <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">供应商</th>
                <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">描述</th>
                <th className="text-right px-4 py-2.5 font-medium text-slate-500 text-xs">金额</th>
                <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">单号</th>
                <th className="text-left px-4 py-2.5 font-medium text-slate-500 text-xs">审批人</th>
                <th className="text-center px-4 py-2.5 font-medium text-slate-500 text-xs">状态</th>
              </tr>
            </thead>
            <tbody>
              {data.transactions.map((t, i) => (
                <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-3 text-slate-400 whitespace-nowrap text-xs">{t.date}</td>
                  <td className="px-4 py-3 font-medium text-slate-200 whitespace-nowrap">{t.vendor}</td>
                  <td className="px-4 py-3 text-slate-400 max-w-[180px] truncate text-xs">{t.description}</td>
                  <td className="px-4 py-3 text-right font-semibold text-amber-400 whitespace-nowrap">¥{t.amount.toLocaleString()}</td>
                  <td className="px-4 py-3 text-slate-500 font-mono text-[11px] whitespace-nowrap">{t.invoice_no}</td>
                  <td className="px-4 py-3 text-slate-400 whitespace-nowrap text-xs">{t.approver}</td>
                  <td className="px-4 py-3 text-center whitespace-nowrap">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                      t.status === "approved" ? "bg-emerald-500/10 text-emerald-400" :
                      t.status === "pending" ? "bg-amber-500/10 text-amber-400" :
                      "bg-rose-500/10 text-rose-400"
                    }`}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{
                        background: t.status === "approved" ? "#34d399" : t.status === "pending" ? "#fbbf24" : "#fb7185"
                      }} />
                      {t.status === "approved" ? "已审批" : t.status === "pending" ? "待审批" : "已驳回"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
