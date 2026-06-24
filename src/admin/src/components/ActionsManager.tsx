"use client";

import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { invalidateCacheByPrefix } from "@/lib/cache";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { Action, ActionLog } from "@/lib/types";

const ACTION_TYPES = [
  { value: "notification", label: "通知", icon: "🔔" },
  { value: "webhook", label: "Webhook", icon: "🔗" },
  { value: "email", label: "邮件", icon: "📧" },
  { value: "data_update", label: "数据更新", icon: "📝" },
  { value: "workflow", label: "工作流", icon: "⚙️" },
];

export default function ActionsManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [actions, setActions] = useState<Action[]>([]);
  const [logs, setLogs] = useState<ActionLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [editAction, setEditAction] = useState<Action | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "", description: "", action_type: "notification",
    trigger_condition: "", target_object: "", parameters: "{}",
    requires_confirm: true, sort_order: 0,
  });

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const load = async (force = false) => {
    if (!activeScenario) return;
    setLoading(true);
    try {
      const d = await api(`/api/admin/scenarios/${activeScenario}/actions`);
      setActions(d || []);
    } catch { addToast("error", "加载失败"); }
    setLoading(false);
  };

  const loadLogs = async () => {
    if (!activeScenario) return;
    try {
      const d = await api(`/api/admin/scenarios/${activeScenario}/actions/logs`);
      setLogs(d || []);
      setShowLogs(true);
    } catch { addToast("error", "加载日志失败"); }
  };

  const resetForm = () => {
    setForm({ name: "", description: "", action_type: "notification", trigger_condition: "", target_object: "", parameters: "{}", requires_confirm: true, sort_order: 0 });
    setEditAction(null);
    setShowForm(false);
  };

  const startEdit = (a: Action) => {
    setEditAction(a);
    setForm({
      name: a.name, description: a.description, action_type: a.action_type,
      trigger_condition: a.trigger_condition, target_object: a.target_object,
      parameters: JSON.stringify(a.parameters, null, 2),
      requires_confirm: !!a.requires_confirm, sort_order: a.sort_order,
    });
    setShowForm(true);
  };

  const save = async () => {
    if (!form.name.trim()) { addToast("error", "名称必填"); return; }
    try {
      let params = {};
      try { params = JSON.parse(form.parameters); } catch { addToast("error", "参数JSON格式错误"); return; }
      const body = { ...form, parameters: params };
      if (editAction) {
        await api(`/api/admin/scenarios/${activeScenario}/actions/${editAction.id}`, { method: "PUT", body: JSON.stringify(body) });
        addToast("success", "Action已更新");
      } else {
        await api(`/api/admin/scenarios/${activeScenario}/actions`, { method: "POST", body: JSON.stringify(body) });
        addToast("success", "Action已创建");
      }
      invalidateCacheByPrefix(`actions_${activeScenario}`);
      resetForm(); load(true);
    } catch (e: any) { addToast("error", e.message); }
  };

  const deleteAction = async (id: string) => {
    try {
      await api(`/api/admin/scenarios/${activeScenario}/actions/${id}`, { method: "DELETE" });
      addToast("success", "Action已删除"); load(true);
    } catch { addToast("error", "删除失败"); }
  };

  const toggleActive = async (a: Action) => {
    try {
      await api(`/api/admin/scenarios/${activeScenario}/actions/${a.id}`, {
        method: "PUT", body: JSON.stringify({ is_active: !a.is_active }),
      });
      load(true);
    } catch { addToast("error", "操作失败"); }
  };

  const executeAction = async (a: Action) => {
    if (a.requires_confirm && !confirm(`确定要执行 Action「${a.name}」吗？`)) return;
    try {
      const res = await api(`/api/admin/scenarios/${activeScenario}/actions/${a.id}/execute`, {
        method: "POST", body: JSON.stringify({ confirmed: true, context: { trigger_type: "manual" } }),
      });
      addToast(res.status === "success" ? "success" : "error", res.status === "success" ? "执行成功" : `执行失败: ${res.message || ""}`);
      loadLogs();
    } catch (e: any) { addToast("error", e.message); }
  };

  if (!activeScenario) return <ScenarioSelector />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-slate-800">行动管理 (Actions)</h2>
        <div className="flex items-center gap-2">
          <button onClick={loadLogs} className="btn-outline text-sm">📋 执行日志</button>
          <ScenarioSelector />
        </div>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
        <p className="font-medium mb-1">💡 什么是 Action？</p>
        <p>基于本体论（Ontology）的 ChatBI 三要素：Data（数据）+ Logic（逻辑）+ Action（行动）。Action 将数据分析的"洞察"转化为"行动"，实现从"看数据"到"用数据"的闭环。例如：发现库存不足时自动触发补货通知，发现异常时自动发送告警邮件。</p>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">共 {actions.length} 个 Action</p>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="btn-primary text-sm">+ 新增 Action</button>
      </div>

      {loading ? <LoadingSpinner /> : actions.length === 0 ? <EmptyState title="暂无 Action" description="创建第一个 Action，让数据洞察驱动业务行动" /> : (
        <div className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
          {actions.map(a => {
            const typeInfo = ACTION_TYPES.find(t => t.value === a.action_type) || ACTION_TYPES[0];
            return (
              <div key={a.id} className="flex items-center justify-between px-5 py-4 hover:bg-slate-50">
                <div className="flex items-center gap-4 min-w-0">
                  <span className="text-2xl">{typeInfo.icon}</span>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{a.name}</span>
                      <span className="px-2 py-0.5 text-xs rounded-full bg-slate-100 text-slate-600">{typeInfo.label}</span>
                      {a.requires_confirm ? <span className="px-2 py-0.5 text-xs rounded-full bg-blue-50 text-blue-600">需确认</span> : <span className="px-2 py-0.5 text-xs rounded-full bg-green-50 text-green-600">自动</span>}
                      {!a.is_active && <span className="px-2 py-0.5 text-xs rounded-full bg-red-50 text-red-600">已禁用</span>}
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5 truncate">{a.description || "无描述"}</p>
                    {a.trigger_condition && <p className="text-xs text-slate-400 mt-0.5">触发条件: {a.trigger_condition}</p>}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button onClick={() => executeAction(a)} className="p-1.5 text-green-400 hover:text-green-600" title="执行">▶</button>
                  <button onClick={() => toggleActive(a)} className="p-1.5 text-slate-400 hover:text-slate-600" title={a.is_active ? "禁用" : "启用"}>{a.is_active ? "⏸" : "▶️"}</button>
                  <button onClick={() => startEdit(a)} className="p-1.5 text-slate-400 hover:text-blue-600" title="编辑">✏️</button>
                  <button onClick={() => setDeleteTarget(a.id)} className="p-1.5 text-slate-400 hover:text-red-500" title="删除">🗑️</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showForm && (
        <div className="fixed inset-0 z-40 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={resetForm} />
          <div className="relative bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-6 space-y-4">
              <h3 className="text-lg font-semibold">{editAction ? "编辑 Action" : "新增 Action"}</h3>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">名称 *</label>
                <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} className="input-field" placeholder="如：发送库存不足通知" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">描述</label>
                <textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} className="input-field" rows={2} placeholder="描述这个 Action 的作用" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">行动类型</label>
                <select value={form.action_type} onChange={e => setForm(f => ({ ...f, action_type: e.target.value }))} className="input-field">
                  {ACTION_TYPES.map(t => <option key={t.value} value={t.value}>{t.icon} {t.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">触发条件</label>
                <input value={form.trigger_condition} onChange={e => setForm(f => ({ ...f, trigger_condition: e.target.value }))} className="input-field" placeholder="关键词，如：库存不足,缺货,补货" />
                <p className="text-xs text-slate-400 mt-1">逗号分隔的关键词，Chat 中匹配到时推荐此 Action</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">目标对象</label>
                <input value={form.target_object} onChange={e => setForm(f => ({ ...f, target_object: e.target.value }))} className="input-field" placeholder="如：Webhook URL、邮件地址、数据表名" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">参数 (JSON)</label>
                <textarea value={form.parameters} onChange={e => setForm(f => ({ ...f, parameters: e.target.value }))} className="input-field font-mono text-xs" rows={4} placeholder='{"webhook_url": "https://...", "template": "库存不足通知"}' />
              </div>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.requires_confirm} onChange={e => setForm(f => ({ ...f, requires_confirm: e.target.checked }))} className="rounded" />
                  需要人工确认
                </label>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={resetForm} className="btn-outline">取消</button>
                <button onClick={save} className="btn-primary">保存</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showLogs && (
        <div className="fixed inset-0 z-40 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShowLogs(false)} />
          <div className="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 max-h-[80vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold">Action 执行日志</h3>
                <button onClick={() => setShowLogs(false)} className="text-slate-400 hover:text-slate-600">✕</button>
              </div>
              {logs.length === 0 ? <EmptyState title="暂无执行记录" /> : (
                <div className="space-y-2">
                  {logs.map(l => (
                    <div key={l.id} className="flex items-center justify-between px-4 py-3 bg-slate-50 rounded-lg">
                      <div>
                        <span className="font-medium text-sm">{l.action_name}</span>
                        <span className={`ml-2 px-2 py-0.5 text-xs rounded-full ${l.status === "success" ? "bg-green-50 text-green-600" : l.status === "failed" ? "bg-red-50 text-red-600" : "bg-yellow-50 text-yellow-600"}`}>{l.status}</span>
                        {l.trigger_reason && <p className="text-xs text-slate-400 mt-0.5">{l.trigger_reason}</p>}
                      </div>
                      <div className="text-right text-xs text-slate-400">
                        <div>{l.executed_at}</div>
                        {l.duration > 0 && <div>{l.duration.toFixed(2)}s</div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog isOpen={!!deleteTarget} title="删除 Action" message="确定要删除此 Action 吗？" onConfirm={() => { if (deleteTarget) { deleteAction(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
