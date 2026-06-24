"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { ChartRule } from "@/lib/types";

const CHART_LABELS: Record<string, string> = { bar: "柱状图", line: "折线图", pie: "饼图", scatter: "散点图", table: "表格", heatmap: "热力图", funnel: "漏斗图", radar: "雷达图", gauge: "仪表盘", treemap: "矩形树图", sankey: "桑基图" };
const CHART_COLORS: Record<string, string> = { bar: "bg-indigo-50 text-indigo-600", line: "bg-emerald-50 text-emerald-600", pie: "bg-amber-50 text-amber-600", scatter: "bg-purple-50 text-purple-600", table: "bg-slate-100 text-slate-600", heatmap: "bg-red-50 text-red-600", funnel: "bg-cyan-50 text-cyan-600", radar: "bg-pink-50 text-pink-600" };

export default function ChartRulesManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [rules, setRules] = useState<ChartRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editRule, setEditRule] = useState<Partial<ChartRule> | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const cacheKey = `chart_rules:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<ChartRule[]>(cacheKey); if (cached) { setRules(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api(`/api/admin/scenarios/${activeScenario}/chart_rules`); const data = d || []; setRules(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载图表规则失败"); } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const save = async () => {
    if (!editRule?.data_pattern || !editRule?.chart_type) { addToast("warning", "数据模式和图表类型必填"); return; }
    const isEdit = !!editRule.id;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/chart_rules${isEdit ? `/${editRule.id}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editRule) });
      addToast("success", isEdit ? "规则已更新" : "规则已创建"); setIsModalOpen(false); setEditRule(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: number) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/chart_rules/${id}`, { method: "DELETE" }); addToast("success", "规则已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const filtered = rules.filter(r => !search || r.data_pattern.toLowerCase().includes(search.toLowerCase()) || r.description?.toLowerCase().includes(search.toLowerCase()));

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">图表规则</h2><ScenarioSelector /></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">图表规则</h2>
        <button onClick={() => { setEditRule({ scenario_id: activeScenario, data_pattern: "", chart_type: "", description: "", priority: 0 }); setIsModalOpen(true); }} className="btn-primary">+ 新增规则</button>
      </div>
      <ScenarioSelector />
      <div className="mb-4"><SearchInput value={search} onChange={setSearch} placeholder="搜索规则..." /></div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📈" title="暂无图表规则" description="创建规则来指导图表类型的选择" />
      ) : (
        <div className="grid gap-3">
          {filtered.sort((a, b) => b.priority - a.priority).map(r => (
            <div key={r.id} className="card p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className={`text-xs px-2 py-1 rounded font-medium ${CHART_COLORS[r.chart_type] || "bg-slate-100 text-slate-500"}`}>{CHART_LABELS[r.chart_type] || r.chart_type}</span>
                <div><p className="text-sm font-medium text-slate-700">{r.data_pattern}</p><p className="text-xs text-slate-400">{r.description}</p></div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-400">优先级 {r.priority}</span>
                <button onClick={() => { setEditRule(r); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button>
                <button onClick={() => setDeleteTarget(r.id)} className="btn-ghost text-xs text-red-500">删除</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={() => { setIsModalOpen(false); setEditRule(null); }} title={editRule?.id ? "编辑规则" : "新增规则"} footer={<><button onClick={() => { setIsModalOpen(false); setEditRule(null); }} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">数据模式</label><input value={editRule?.data_pattern || ""} onChange={(e) => setEditRule({ ...editRule!, data_pattern: e.target.value })} className="w-full" placeholder="time_series, categorical, distribution" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">图表类型</label><select value={editRule?.chart_type || ""} onChange={(e) => setEditRule({ ...editRule!, chart_type: e.target.value })} className="w-full"><option value="">选择图表类型</option>{Object.entries(CHART_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={editRule?.description || ""} onChange={(e) => setEditRule({ ...editRule!, description: e.target.value })} className="w-full" rows={3} /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">优先级</label><input type="number" value={editRule?.priority || 0} onChange={(e) => setEditRule({ ...editRule!, priority: Number(e.target.value) })} className="w-32" /><p className="text-xs text-slate-400 mt-1">数值越大优先级越高</p></div>
        </div>
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除图表规则" message="确定要删除此图表规则吗？" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
