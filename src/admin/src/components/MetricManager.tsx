"use client";
import { useState, useEffect } from "react";
import { Pencil, Plus, Trash2, X } from "lucide-react";
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
const REVIEW_BADGE = {
  approved: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100",
  pending: "bg-amber-50 text-amber-700 ring-1 ring-amber-100",
};

const compactList = (items: string[] = [], limit = 3) => {
  if (!items.length) return "-";
  const head = items.slice(0, limit).join(", ");
  return items.length > limit ? `${head} +${items.length - limit}` : head;
};

export default function MetricManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [editMetric, setEditMetric] = useState<Partial<Metric> | null>(null);
  const [editingMetricId, setEditingMetricId] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isBulkDeleteOpen, setIsBulkDeleteOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const cacheKey = `metrics:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<Metric[]>(cacheKey); if (cached) { setMetrics(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api(`/api/admin/scenarios/${activeScenario}/metrics`); const data = d || []; setMetrics(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载指标失败"); } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) { setSelectedIds([]); load(); } }, [activeScenario]);

  const save = async () => {
    if (!editingMetricId && !editMetric?.id) { addToast("warning", "指标 ID 必填"); return; }
    if (!editMetric?.name) { addToast("warning", "名称必填"); return; }
    const isEdit = !!editingMetricId;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/metrics${isEdit ? `/${editingMetricId}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editMetric) });
      addToast("success", isEdit ? "指标已更新" : "指标已创建"); setIsModalOpen(false); setEditMetric(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: string) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/metrics/${id}`, { method: "DELETE" }); addToast("success", "指标已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const removeSelected = async () => {
    if (!activeScenario || selectedIds.length === 0) return;
    const ids = [...selectedIds];
    try {
      await api(`/api/admin/scenarios/${activeScenario}/metrics/batch-delete`, { method: "POST", body: JSON.stringify({ ids }) });
      addToast("success", `已删除 ${ids.length} 个指标`);
      setSelectedIds([]); setIsBulkDeleteOpen(false); invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "批量删除失败"); }
  };

  const categories = [...new Set(metrics.map(m => m.category).filter(Boolean))];
  const filtered = metrics.filter(m => {
    const keyword = search.toLowerCase();
    const ms = !search || [m.id, m.name, m.description, m.target_class, m.formula].some(v => (v || "").toLowerCase().includes(keyword));
    const mc = !filterCategory || m.category === filterCategory;
    return ms && mc;
  });
  const visibleIds = filtered.map(m => m.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selectedIds.includes(id));
  const toggleMetric = (id: string) => setSelectedIds(prev => prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]);
  const toggleVisible = () => setSelectedIds(prev => allVisibleSelected ? prev.filter(id => !visibleIds.includes(id)) : Array.from(new Set([...prev, ...visibleIds])));
  const openCreate = () => { setEditingMetricId(null); setEditMetric({ id: "", scenario_id: activeScenario, category: "", target_class: "", calculation: "", formula: "", dimensions: [], required_dimensions: [], filters_hint: "", chart_type: "bar", sort_order: 0, is_reviewed: false }); setIsModalOpen(true); };
  const openEdit = (metric: Metric) => { setEditingMetricId(metric.id); setEditMetric(metric); setIsModalOpen(true); };
  const closeModal = () => { setIsModalOpen(false); setEditMetric(null); setEditingMetricId(null); };

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">指标管理</h2><ScenarioSelector /></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">指标管理</h2>
          <p className="mt-1 text-xs text-slate-500">共 {metrics.length} 个指标，当前显示 {filtered.length} 个</p>
        </div>
        <button onClick={openCreate} className="btn-primary inline-flex items-center gap-2"><Plus className="h-4 w-4" />新增指标</button>
      </div>
      <ScenarioSelector />
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1"><SearchInput value={search} onChange={setSearch} placeholder="搜索名称、ID、目标类或公式..." /></div>
        <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className="text-sm lg:w-40"><option value="">全部分类</option>{categories.map(c => <option key={c} value={c}>{c}</option>)}</select>
      </div>
      {selectedIds.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2">
          <span className="text-sm font-medium text-red-700">已选 {selectedIds.length} 个指标</span>
          <button onClick={() => setIsBulkDeleteOpen(true)} className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700"><Trash2 className="h-3.5 w-3.5" />批量删除</button>
          <button onClick={() => setSelectedIds([])} className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-100"><X className="h-3.5 w-3.5" />取消选择</button>
        </div>
      )}

      <style jsx>{`
        .metric-clamp {
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
      `}</style>

      <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
        <span>使用勾选框选择指标后可批量删除。</span>
        <span>{selectedIds.length > 0 ? `已选 ${selectedIds.length}` : "未选择"}</span>
      </div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📐" title="暂无指标" description="使用AI提取或手动创建" />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
          <table className="data-table table-fixed min-w-[1120px]"><colgroup><col className="w-12" /><col className="w-[240px]" /><col className="w-28" /><col className="w-[180px]" /><col className="w-[260px]" /><col className="w-[170px]" /><col className="w-24" /><col className="w-24" /><col className="w-28" /></colgroup><thead><tr><th><input type="checkbox" checked={allVisibleSelected} onChange={toggleVisible} className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500" aria-label="选择当前指标列表" /></th><th>指标</th><th>分类</th><th>目标类</th><th>公式</th><th>维度</th><th>图表</th><th>审核</th><th className="text-right">操作</th></tr></thead>
          <tbody>{filtered.map(m => (
            <tr key={m.id} className={selectedIds.includes(m.id) ? "bg-indigo-50/40" : ""}><td><input type="checkbox" checked={selectedIds.includes(m.id)} onChange={() => toggleMetric(m.id)} className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500" aria-label={`选择 ${m.name}`} /></td><td><div className="min-w-0"><div className="metric-clamp font-medium text-slate-800" title={m.name}>{m.name || "未命名指标"}</div><div className="mt-1 truncate font-mono text-[11px] text-slate-400" title={m.id}>{m.id}</div>{m.description && <div className="metric-clamp mt-1 text-xs text-slate-500" title={m.description}>{m.description}</div>}</div></td><td><span className="inline-flex max-w-full rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600"><span className="truncate">{m.category || "-"}</span></span></td><td><div className="truncate font-mono text-xs text-slate-600" title={m.target_class}>{m.target_class || "-"}</div></td><td><code className="metric-clamp rounded-md bg-slate-50 px-2 py-1 font-mono text-xs text-slate-600 ring-1 ring-slate-100" title={m.formula}>{m.formula || "-"}</code></td><td><div className="metric-clamp text-xs text-slate-500" title={(m.dimensions || []).join(", ")}>{compactList(m.dimensions)}</div>{m.required_dimensions?.length > 0 && <div className="mt-1 truncate text-[11px] text-slate-400" title={m.required_dimensions.join(", ")}>必需：{compactList(m.required_dimensions, 2)}</div>}</td><td><span className="inline-flex rounded bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-600 ring-1 ring-indigo-100">{CHART_LABELS[m.chart_type] || m.chart_type}</span></td><td><span className={`inline-flex rounded px-1.5 py-0.5 text-xs ${m.is_reviewed ? REVIEW_BADGE.approved : REVIEW_BADGE.pending}`}>{m.is_reviewed ? "通过" : "待审"}</span></td><td className="text-right"><div className="flex justify-end gap-1"><button onClick={() => openEdit(m)} className="btn-ghost p-1.5" title="编辑"><Pencil className="h-4 w-4" /></button><button onClick={() => setDeleteTarget(m.id)} className="btn-ghost p-1.5 text-red-500" title="删除"><Trash2 className="h-4 w-4" /></button></div></td></tr>
          ))}</tbody></table>
          </div>
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={closeModal} title={editingMetricId ? "编辑指标" : "新增指标"} footer={<><button onClick={closeModal} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">指标 ID</label><input value={editMetric?.id || ""} disabled={!!editingMetricId} onChange={(e) => setEditMetric({ ...editMetric!, id: e.target.value })} className="w-full font-mono disabled:bg-slate-50 disabled:text-slate-400" placeholder="total_sales_amount" /></div>
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

      <ConfirmDialog isOpen={!!deleteTarget} title="删除指标" message="确定要删除此指标吗？" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setSelectedIds(prev => prev.filter(id => id !== deleteTarget)); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
      <ConfirmDialog isOpen={isBulkDeleteOpen} title="批量删除指标" message={`确定要删除选中的 ${selectedIds.length} 个指标吗？此操作不可撤销。`} confirmText="批量删除" onConfirm={removeSelected} onCancel={() => setIsBulkDeleteOpen(false)} />
    </div>
  );
}
