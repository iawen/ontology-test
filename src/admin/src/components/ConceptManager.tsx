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
import type { Concept } from "@/lib/types";

const TYPE_COLORS: Record<string, string> = { entity: "bg-indigo-50 text-indigo-600", dimension: "bg-amber-50 text-amber-600", measure: "bg-emerald-50 text-emerald-600", attribute: "bg-purple-50 text-purple-600" };
const TYPE_LABELS: Record<string, string> = { entity: "实体", dimension: "维度", measure: "度量", attribute: "属性" };

interface TreeNode extends Concept {
  children: TreeNode[];
}

export default function ConceptManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterType, setFilterType] = useState("");
  const [editConcept, setEditConcept] = useState<Partial<Concept> | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"tree" | "table">("tree");

  const cacheKey = `concepts:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<Concept[]>(cacheKey); if (cached) { setConcepts(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api(`/api/admin/scenarios/${activeScenario}/concepts`); const data = d || []; setConcepts(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载概念失败"); } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const save = async () => {
    if (!editConcept?.name) { addToast("warning", "名称必填"); return; }
    const isEdit = !!editConcept.id;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/concepts${isEdit ? `/${editConcept.id}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editConcept) });
      addToast("success", isEdit ? "概念已更新" : "概念已创建"); setIsModalOpen(false); setEditConcept(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: string) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/concepts/${id}`, { method: "DELETE" }); addToast("success", "概念已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const buildTree = (items: Concept[], parentId: string = ""): TreeNode[] => {
    return items.filter(c => (c.parent_id || "") === parentId).sort((a, b) => a.sort_order - b.sort_order).map(c => ({ ...c, children: buildTree(items, c.id) }));
  };

  const renderTree = (nodes: any[], depth = 0) => (
    <div className={depth > 0 ? "ml-6 border-l-2 border-slate-100 pl-3" : ""}>
      {nodes.map(node => (
        <div key={node.id} className="py-1">
          <div className="flex items-center gap-2 py-2 px-3 rounded-lg hover:bg-slate-50 group">
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${TYPE_COLORS[node.concept_type] || "bg-slate-100 text-slate-500"}`}>{TYPE_LABELS[node.concept_type] || node.concept_type}</span>
            <span className="text-sm font-medium text-slate-700">{node.name}</span>
            <span className="text-xs text-slate-400 truncate max-w-xs">{node.description}</span>
            <div className="ml-auto opacity-0 group-hover:opacity-100 flex items-center gap-1">
              <button onClick={() => { setEditConcept(node); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button>
              <button onClick={() => setDeleteTarget(node.id)} className="btn-ghost text-xs text-red-500">删除</button>
            </div>
          </div>
          {node.children?.length > 0 && renderTree(node.children, depth + 1)}
        </div>
      ))}
    </div>
  );

  const filtered = concepts.filter(c => {
    const matchSearch = !search || c.name.toLowerCase().includes(search.toLowerCase());
    const matchType = !filterType || c.concept_type === filterType;
    return matchSearch && matchType;
  });

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">概念管理</h2><ScenarioSelector /></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">概念管理</h2>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-slate-200 overflow-hidden">
            <button onClick={() => setViewMode("tree")} className={`px-3 py-1.5 text-xs ${viewMode === "tree" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}>树形</button>
            <button onClick={() => setViewMode("table")} className={`px-3 py-1.5 text-xs ${viewMode === "table" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}>表格</button>
          </div>
          <button onClick={() => { setEditConcept({ scenario_id: activeScenario, parent_id: "", level: 0, concept_type: "entity", related_class: "", sort_order: 0 }); setIsModalOpen(true); }} className="btn-primary">+ 新增概念</button>
        </div>
      </div>
      <ScenarioSelector />
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1"><SearchInput value={search} onChange={setSearch} placeholder="搜索概念..." /></div>
        <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="text-sm w-32"><option value="">全部类型</option>{Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="🌳" title="暂无概念" description="使用AI提取或手动创建" />
      ) : viewMode === "tree" ? (
        <div className="card p-4">{renderTree(buildTree(filtered))}</div>
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table"><thead><tr><th>名称</th><th>类型</th><th>描述</th><th>关联类</th><th>层级</th><th className="text-right">操作</th></tr></thead>
          <tbody>{filtered.sort((a, b) => a.level - b.level || a.sort_order - b.sort_order).map(c => (
            <tr key={c.id}><td className="font-medium text-slate-700" style={{ paddingLeft: `${(c.level || 0) * 20 + 20}px` }}>{c.name}</td><td><span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${TYPE_COLORS[c.concept_type] || ""}`}>{TYPE_LABELS[c.concept_type] || c.concept_type}</span></td><td className="text-slate-500 max-w-xs truncate">{c.description}</td><td className="text-xs">{c.related_class}</td><td className="text-xs text-slate-400">{c.level}</td><td className="text-right"><button onClick={() => { setEditConcept(c); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button><button onClick={() => setDeleteTarget(c.id)} className="btn-ghost text-xs text-red-500">删除</button></td></tr>
          ))}</tbody></table>
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={() => { setIsModalOpen(false); setEditConcept(null); }} title={editConcept?.id ? "编辑概念" : "新增概念"} footer={<><button onClick={() => { setIsModalOpen(false); setEditConcept(null); }} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">名称</label><input value={editConcept?.name || ""} onChange={(e) => setEditConcept({ ...editConcept!, name: e.target.value })} className="w-full" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={editConcept?.description || ""} onChange={(e) => setEditConcept({ ...editConcept!, description: e.target.value })} className="w-full" rows={2} /></div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">类型</label><select value={editConcept?.concept_type || "entity"} onChange={(e) => setEditConcept({ ...editConcept!, concept_type: e.target.value })} className="w-full">{Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">父概念</label><select value={editConcept?.parent_id || ""} onChange={(e) => setEditConcept({ ...editConcept!, parent_id: e.target.value })} className="w-full"><option value="">无 (顶级)</option>{concepts.filter(c => c.id !== editConcept?.id).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">关联类</label><input value={editConcept?.related_class || ""} onChange={(e) => setEditConcept({ ...editConcept!, related_class: e.target.value })} className="w-full" placeholder="Sale" /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">排序</label><input type="number" value={editConcept?.sort_order || 0} onChange={(e) => setEditConcept({ ...editConcept!, sort_order: Number(e.target.value) })} className="w-full" /></div>
          </div>
        </div>
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除概念" message="确定要删除此概念吗？子概念将变为顶级概念。" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
