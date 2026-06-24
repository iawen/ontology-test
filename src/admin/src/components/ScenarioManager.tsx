"use client";

import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCacheByPrefix } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import type { Scenario } from "@/lib/types";

/**
 * 场景管理页面
 *
 * 关键设计：
 * - 「设为当前」按钮仅修改前端用户偏好（localStorage），不调用后端 is_active
 * - 场景的 CRUD 操作会清除相关缓存
 */

export default function ScenarioManager() {
  const { token, addToast, setScenarios } = useApp();
  const api = useApi(token);
  const [scenarios, setLocalScenarios] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [newScenario, setNewScenario] = useState({ id: "", name: "", description: "" });
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editScenario, setEditScenario] = useState<Scenario | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const load = async (force = false) => {
    const cacheKey = "scenarios:all";
    if (!force) {
      const cached = getCacheData<Scenario[]>(cacheKey);
      if (cached) { setLocalScenarios(cached); setScenarios(cached); setLoading(false); return; }
    }
    setLoading(true);
    try {
      const d = await api("/api/admin/scenarios");
      setLocalScenarios(d);
      setScenarios(d);
      setCacheData(cacheKey, d);
    } catch { addToast("error", "加载场景失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!newScenario.id || !newScenario.name) { addToast("warning", "ID 和名称必填"); return; }
    try {
      await api("/api/admin/scenarios", { method: "POST", body: JSON.stringify(newScenario) });
      addToast("success", "场景已创建");
      setIsCreateOpen(false);
      setNewScenario({ id: "", name: "", description: "" });
      invalidateCacheByPrefix("scenarios:");
      load(true);
    } catch (e: any) { addToast("error", e.message || "创建失败"); }
  };

  const update = async () => {
    if (!editScenario) return;
    try {
      await api(`/api/admin/scenarios/${editScenario.id}`, { method: "PUT", body: JSON.stringify(editScenario) });
      addToast("success", "场景已更新");
      setEditScenario(null);
      invalidateCacheByPrefix("scenarios:");
      load(true);
    } catch (e: any) { addToast("error", e.message || "更新失败"); }
  };

  const remove = async (id: string) => {
    try {
      await api(`/api/admin/scenarios/${id}`, { method: "DELETE" });
      addToast("success", "场景已删除");
      invalidateCacheByPrefix("scenarios:");
      load(true);
    } catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  const toggleScenario = async (id: string, is_active: number) => {
    await api(`/api/admin/scenarios/${id}/toggle`, { method: "POST", body: JSON.stringify({is_active: is_active}) } );
    load(true);
    addToast("success", "✅ 已激活");
  };

  const setDefaultScenario = async (id: string) => {
    await api(`/api/admin/scenarios/${id}/default`, { method: "POST" });
    load(true);
    addToast("success", "✅ 已设为默认场景");
  };

  const filtered = scenarios.filter((s) =>
    s.name.toLowerCase().includes(search.toLowerCase()) || s.id.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">场景列表</h2>
        <button onClick={() => setIsCreateOpen(true)} className="btn-primary">+ 新建场景</button>
      </div>

      <div className="mb-4"><SearchInput value={search} onChange={setSearch} placeholder="搜索场景..." /></div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📁" title="暂无场景" description="创建第一个场景开始使用" action={{ label: "新建场景", onClick: () => setIsCreateOpen(true) }} />
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th><th>名称</th><th>描述</th><th>创建时间</th><th className="text-center">当前</th><th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.id}>
                  <td className="font-mono text-xs text-slate-500">{s.id}</td>
                  <td className="font-medium text-slate-700">{s.name}</td>
                  <td className="text-slate-500 max-w-xs truncate">{s.description}</td>
                  <td className="text-slate-400 text-xs whitespace-nowrap">{s.created_at}</td>
                  <td className="text-center">
                    { s.is_default ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-600 font-medium"><span className="w-2 h-2 rounded-full bg-emerald-500" />默认</span>
                    ) : (
                      <button onClick={() => setDefaultScenario(s.id)} className="text-xs text-indigo-600 hover:text-indigo-800">设为默认</button>
                    )}
                  </td>
                  <td className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => toggleScenario(s.id, s.is_active)} className="text-xs text-indigo-600 hover:underline">{!s.is_active ? "激活" : "禁用"}</button>                    
                      <button onClick={() => setEditScenario(s)} className="btn-ghost text-xs">编辑</button>
                      <button onClick={() => setDeleteTarget(s.id)} className="btn-ghost text-xs text-red-500 hover:text-red-700">删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      <Modal isOpen={isCreateOpen} onClose={() => setIsCreateOpen(false)} title="新建场景" footer={
        <><button onClick={() => setIsCreateOpen(false)} className="btn-outline">取消</button><button onClick={create} className="btn-primary">创建</button></>
      }>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">场景 ID</label><input value={newScenario.id} onChange={(e) => setNewScenario({ ...newScenario, id: e.target.value })} className="w-full" placeholder="如：sales" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">场景名称</label><input value={newScenario.name} onChange={(e) => setNewScenario({ ...newScenario, name: e.target.value })} className="w-full" placeholder="如：销售分析" /></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={newScenario.description} onChange={(e) => setNewScenario({ ...newScenario, description: e.target.value })} className="w-full" rows={3} /></div>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal isOpen={!!editScenario} onClose={() => setEditScenario(null)} title="编辑场景" footer={
        <><button onClick={() => setEditScenario(null)} className="btn-outline">取消</button><button onClick={update} className="btn-primary">保存</button></>
      }>
        {editScenario && (
          <div className="space-y-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">场景 ID</label><input value={editScenario.id} disabled className="w-full bg-slate-50 text-slate-400" /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">场景名称</label><input value={editScenario.name} onChange={(e) => setEditScenario({ ...editScenario, name: e.target.value })} className="w-full" /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label><textarea value={editScenario.description} onChange={(e) => setEditScenario({ ...editScenario, description: e.target.value })} className="w-full" rows={3} /></div>
          </div>
        )}
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除场景" message="确定要删除此场景吗？场景下的所有数据、Schema、指标等将被永久删除，此操作不可撤销。" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
