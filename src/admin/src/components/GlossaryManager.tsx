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
import type { GlossaryTerm } from "@/lib/types";

export default function GlossaryManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editTerm, setEditTerm] = useState<Partial<GlossaryTerm> | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const cacheKey = `glossary:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<GlossaryTerm[]>(cacheKey); if (cached) { setTerms(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api(`/api/admin/scenarios/${activeScenario}/glossary`); const data = d || []; setTerms(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载专用名称失败"); } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const save = async () => {
    if (!editTerm?.term) { addToast("warning", "术语必填"); return; }
    const isEdit = !!editTerm.id;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/glossary${isEdit ? `/${editTerm.id}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editTerm) });
      addToast("success", isEdit ? "术语已更新" : "术语已创建"); setIsModalOpen(false); setEditTerm(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: string) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/glossary/${id}`, { method: "DELETE" }); addToast("success", "术语已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const filtered = terms.filter(t => !search || t.term.toLowerCase().includes(search.toLowerCase()) || t.aliases?.some(a => a.toLowerCase().includes(search.toLowerCase())));

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">专用名称</h2><ScenarioSelector /></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">专用名称</h2>
        <button onClick={() => { setEditTerm({ scenario_id: activeScenario, term: "", aliases: [], description: "" }); setIsModalOpen(true); }} className="btn-primary">+ 新增术语</button>
      </div>
      <ScenarioSelector />
      <div className="mb-4"><SearchInput value={search} onChange={setSearch} placeholder="搜索术语或别名..." /></div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📖" title="暂无专用名称" description="添加业务术语和别名，提升AI理解能力" />
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table"><thead><tr><th>术语</th><th>别名</th><th>描述</th><th className="text-right">操作</th></tr></thead>
          <tbody>{filtered.map(t => (
            <tr key={t.id}><td className="font-medium text-slate-700">{t.term}</td><td className="text-xs text-slate-500">{(t.aliases || []).join(", ")}</td><td className="text-slate-500 max-w-xs truncate">{t.description}</td><td className="text-right"><button onClick={() => { setEditTerm(t); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button><button onClick={() => setDeleteTarget(t.id)} className="btn-ghost text-xs text-red-500">删除</button></td></tr>
          ))}</tbody></table>
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={() => { setIsModalOpen(false); setEditTerm(null); }} title={editTerm?.id ? "编辑术语" : "新增术语"} footer={<><button onClick={() => { setIsModalOpen(false); setEditTerm(null); }} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">术语</label><input value={editTerm?.term || ""} onChange={(e) => setEditTerm({ ...editTerm!, term: e.target.value })} className="w-full" placeholder="如：GMV" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">别名 (逗号分隔)</label><input value={(editTerm?.aliases || []).join(", ")} onChange={(e) => setEditTerm({ ...editTerm!, aliases: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full" placeholder="成交总额, 总交易额" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={editTerm?.description || ""} onChange={(e) => setEditTerm({ ...editTerm!, description: e.target.value })} className="w-full" rows={3} /></div>
        </div>
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除术语" message="确定要删除此术语吗？" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
