"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import type { User } from "@/lib/types";

const ROLE_LABELS: Record<string, string> = { admin: "管理员", editor: "编辑者", viewer: "查看者" };
const ROLE_COLORS: Record<string, string> = { admin: "bg-indigo-50 text-indigo-600", editor: "bg-emerald-50 text-emerald-600", viewer: "bg-slate-100 text-slate-500" };

export default function UsersManager() {
  const { token, addToast } = useApp();
  const api = useApi(token);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [editUser, setEditUser] = useState<Partial<User> & { password?: string } | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const cacheKey = "users:all";

  const load = async (force = false) => {
    if (!force) { const cached = getCacheData<User[]>(cacheKey); if (cached) { setUsers(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api("/api/admin/users"); const data = d || []; setUsers(data); setCacheData(cacheKey, data); }
    catch { addToast("error", "加载用户列表失败"); } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!editUser?.username) { addToast("warning", "用户名必填"); return; }
    const isEdit = !!editUser.id;
    try {
      await api(`/api/admin/users${isEdit ? `/${editUser.id}` : ""}`, { method: isEdit ? "PUT" : "POST", body: JSON.stringify(editUser) });
      addToast("success", isEdit ? "用户已更新" : "用户已创建"); setIsModalOpen(false); setEditUser(null);
      invalidateCache(cacheKey); load(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  const remove = async (id: number) => {
    try { await api(`/api/admin/users/${id}`, { method: "DELETE" }); addToast("success", "用户已删除"); invalidateCache(cacheKey); load(true); }
    catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">用户管理</h2>
        <button onClick={() => { setEditUser({ username: "", password: "", role: "viewer" }); setIsModalOpen(true); }} className="btn-primary">+ 新增用户</button>
      </div>

      {loading ? <LoadingSpinner /> : users.length === 0 ? (
        <EmptyState icon="👥" title="暂无用户" />
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table"><thead><tr><th>用户名</th><th>角色</th><th>创建时间</th><th className="text-right">操作</th></tr></thead>
          <tbody>{users.map(u => (
            <tr key={u.id}><td className="font-medium text-slate-700">{u.username}</td><td><span className={`text-xs px-2 py-0.5 rounded font-medium ${ROLE_COLORS[u.role] || ""}`}>{ROLE_LABELS[u.role] || u.role}</span></td><td className="text-xs text-slate-400 whitespace-nowrap">{u.created_at}</td><td className="text-right"><button onClick={() => { setEditUser(u); setIsModalOpen(true); }} className="btn-ghost text-xs">编辑</button><button onClick={() => setDeleteTarget(u.id)} className="btn-ghost text-xs text-red-500">删除</button></td></tr>
          ))}</tbody></table>
        </div>
      )}

      <Modal isOpen={isModalOpen} onClose={() => { setIsModalOpen(false); setEditUser(null); }} title={editUser?.id ? "编辑用户" : "新增用户"} footer={<><button onClick={() => { setIsModalOpen(false); setEditUser(null); }} className="btn-outline">取消</button><button onClick={save} className="btn-primary">保存</button></>}>
        <div className="space-y-4">
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">用户名</label><input value={editUser?.username || ""} onChange={(e) => setEditUser({ ...editUser!, username: e.target.value })} className="w-full" /></div>
          {!editUser?.id && <div><label className="text-xs text-slate-500 font-medium block mb-1.5">密码</label><input type="password" value={editUser?.password || ""} onChange={(e) => setEditUser({ ...editUser!, password: e.target.value })} className="w-full" /></div>}
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">角色</label><select value={editUser?.role || "viewer"} onChange={(e) => setEditUser({ ...editUser!, role: e.target.value })} className="w-full"><option value="admin">管理员</option><option value="editor">编辑者</option><option value="viewer">查看者</option></select></div>
        </div>
      </Modal>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除用户" message="确定要删除此用户吗？" onConfirm={() => { if (deleteTarget) { remove(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
