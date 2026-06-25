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
import type { Metric } from "@/lib/types";

const CHART_LABELS: Record<string, string> = { bar: "柱状图", line: "折线图", pie: "饼图", scatter: "散点图", table: "表格", heatmap: "热力图", funnel: "漏斗图", radar: "雷达图" };

export default function MetricManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [editMetric, setEditMetric] = useState<Partial<Metric> | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const cacheKey = `metrics:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<Metric[]>(cacheKey); if (cached) { setMetrics(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api(`/api/admin/scenarios/${activeScenario}/metrics`); const data = d || []; setMetrics(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载指标失败"); } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const save = async () => {
    if (!editMetric?.name) { addToast("warning", "名称必填"); return; }
    const isEdit = !!editMetric.id;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/metrics${isEdit ? `/${editMetric.id}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editMetric) });
      addToast("success", isEdit ? "指标已更新" : "指标已创建"); setIsModalOpen(false); setEditMetric(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: string) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/metrics/${id}`, { method: "DELETE" }); addToast("success", "指标已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const categories = [...new Set(metrics.map(m => m.category).filter(Boolean))];
  const filtered = metrics.filter(m => {
    const ms = !search || m.name.toLowerCase().includes(search.toLowerCase()) || m.description?.toLowerCase().includes(search.toLowerCase());
    const mc = !filterCategory || m.category === filterCategory;
    return ms && mc;
  });

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">指标管理</h2><ScenarioSelector /></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">指标管理</h2>
        <button onClick={() => { setEditMetric({ scenario_id: activeScenario, category: "", target_class: "", calculation: "", formula: "", dimensions: [], required_dimensions: [], filters_hint: "", chart_type: "bar", sort_order: 0, is_reviewed: false }); setIsModalOpen(true); }} className="btn-primary">+ 新增指标</button>
      </div>
      <ScenarioSelector />
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1"><SearchInput value={search} onChange={setSearch} placeholder="搜索指标..." /></div>
        <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className="text-sm w-32"><option value="">全部分类</option>{categories.map(c => <option key={c} value={c}>{c}</option>)}</select>
      </div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📐" title="暂无指标" description="使用AI提取或手动创建" />
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table"><thead><tr><th>名称</th><th>分类</th><th>目标类</th><th>图表</th><th>审核</th><th>描述</th><th className="text-right">操作</th></tr></thead>
          <tbody>{filtered.map(m => (
            <tr key={m.id}><td className="font-medium text-slate-700">{m.name}</td><td><span className="text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{m.category || "-"}</span></td><td className="text-xs">{m.target_class}</td><td><span className="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">{CHART_LABELS[m.chart_type] || m.chart_type}</span></td><td><span className={`text-xs px-1.5 py-0.5 rounded ${m.is_reviewed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{m.is_reviewed ? "通过" : "待审"}</span></td><td className="text-slate-500 max-w-xs truncate">{m.description}</td><td className="text-right"><button onClick={() => { setEditMetric(m); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button><button onClick={() => setDeleteTarget(m.id)} className="btn-ghost text-xs text-red-500">删除</button></td></tr>
          ))}</tbody></table>
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={() => { setIsModalOpen(false); setEditMetric(null); }} title={editMetric?.id ? "编辑指标" : "新增指标"} footer={<><button onClick={() => { setIsModalOpen(false); setEditMetric(null); }} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">名称</label><input value={editMetric?.name || ""} onChange={(e) => setEditMetric({ ...editMetric!, name: e.target.value })} className="w-full" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={editMetric?.description || ""} onChange={(e) => setEditMetric({ ...editMetric!, description: e.target.value })} className="w-full" rows={2} /></div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">分类</label><input value={editMetric?.category || ""} onChange={(e) => setEditMetric({ ...editMetric!, category: e.target.value })} className="w-full" /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">目标类</label><input value={editMetric?.target_class || ""} onChange={(e) => setEditMetric({ ...editMetric!, target_class: e.target.value })} className="w-full" /></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">计算方式</label><input value={editMetric?.calculation || ""} onChange={(e) => setEditMetric({ ...editMetric!, calculation: e.target.value })} className="w-full" /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">公式</label><input value={editMetric?.formula || ""} onChange={(e) => setEditMetric({ ...editMetric!, formula: e.target.value })} className="w-full" /></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">图表类型</label><select value={editMetric?.chart_type || "bar"} onChange={(e) => setEditMetric({ ...editMetric!, chart_type: e.target.value })} className="w-full">{Object.entries(CHART_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">排序</label><input type="number" value={editMetric?.sort_order || 0} onChange={(e) => setEditMetric({ ...editMetric!, sort_order: Number(e.target.value) })} className="w-32" /></div>
          </div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">维度 (逗号分隔)</label><input value={(editMetric?.dimensions || []).join(", ")} onChange={(e) => setEditMetric({ ...editMetric!, dimensions: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full" placeholder="region, category" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">必要维度 (逗号分隔)</label><input value={(editMetric?.required_dimensions || []).join(", ")} onChange={(e) => setEditMetric({ ...editMetric!, required_dimensions: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">筛选提示</label><input value={editMetric?.filters_hint || ""} onChange={(e) => setEditMetric({ ...editMetric!, filters_hint: e.target.value })} className="w-full" /></div>
          <label className="inline-flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={!!editMetric?.is_reviewed} onChange={(e) => setEditMetric({ ...editMetric!, is_reviewed: e.target.checked })} />人工审核通过</label>
        </div>
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除指标" message="确定要删除此指标吗？" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
