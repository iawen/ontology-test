"use client";

import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { AlertRule, Action } from "@/lib/types";

const SEVERITY_MAP: Record<string, { label: string; color: string }> = {
  info: { label: "信息", color: "bg-blue-50 text-blue-600" },
  warning: { label: "警告", color: "bg-amber-50 text-amber-600" },
  critical: { label: "严重", color: "bg-red-50 text-red-600" },
};

export default function AlertRulesManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editRule, setEditRule] = useState<AlertRule | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "", description: "", target_class: "",
    condition_expression: "", action_id: "", severity: "warning",
  });

  useEffect(() => { if (activeScenario) load(); }, [activeScenario]);

  const load = async () => {
    if (!activeScenario) return;
    setLoading(true);
    try {
      const [r, a] = await Promise.all([
        api(`/api/admin/scenarios/${activeScenario}/alert_rules`),
        api(`/api/admin/scenarios/${activeScenario}/actions`),
      ]);
      setRules(r || []);
      setActions(a || []);
    } catch { addToast("error", "加载失败"); }
    setLoading(false);
  };

  const resetForm = () => {
    setForm({ name: "", description: "", target_class: "", condition_expression: "", action_id: "", severity: "warning" });
    setEditRule(null); setShowForm(false);
  };

  const startEdit = (r: AlertRule) => {
    setEditRule(r);
    setForm({ name: r.name, description: r.description, target_class: r.target_class, condition_expression: r.condition_expression, action_id: r.action_id, severity: r.severity });
    setShowForm(true);
  };

  const save = async () => {
    if (!form.name.trim() || !form.target_class.trim() || !form.condition_expression.trim()) { addToast("error", "名称、目标类、条件表达式必填"); return; }
    try {
      if (editRule) {
        await api(`/api/admin/scenarios/${activeScenario}/alert_rules/${editRule.id}`, { method: "PUT", body: JSON.stringify(form) });
        addToast("success", "规则已更新");
      } else {
        await api(`/api/admin/scenarios/${activeScenario}/alert_rules`, { method: "POST", body: JSON.stringify(form) });
        addToast("success", "规则已创建");
      }
      resetForm(); load();
    } catch (e: any) { addToast("error", e.message); }
  };

  const deleteRule = async (id: string) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/alert_rules/${id}`, { method: "DELETE" }); addToast("success", "规则已删除"); load(); } catch { addToast("error", "删除失败"); }
  };

  const toggleActive = async (r: AlertRule) => {
    try { await api(`/api/admin/scenarios/${activeScenario}/alert_rules/${r.id}`, { method: "PUT", body: JSON.stringify({ is_active: !r.is_active }) }); load(); } catch { addToast("error", "操作失败"); }
  };

  const checkRule = async (id: string) => {
    try {
      const res = await api(`/api/admin/scenarios/${activeScenario}/alert_rules/${id}/check`, { method: "POST" });
      if (res.triggered) addToast("warning", `规则已触发！${res.alert_count} 条数据满足条件`);
      else addToast("info", "规则未触发，当前数据不满足条件");
    } catch (e: any) { addToast("error", e.message); }
  };

  if (!activeScenario) return <ScenarioSelector />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-slate-800">告警规则</h2>
        <ScenarioSelector />
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
        <p className="font-medium mb-1">🔔 告警规则</p>
        <p>基于本体论的数据监控规则。当数据满足条件时自动触发关联的 Action，实现"洞察→行动"的闭环。例如：当库存低于安全阈值时自动触发补货通知。</p>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">共 {rules.length} 条规则</p>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="btn-primary text-sm">+ 新增规则</button>
      </div>

      {loading ? <LoadingSpinner /> : rules.length === 0 ? <EmptyState title="暂无告警规则" description="创建第一条告警规则，让数据异常自动驱动行动" /> : (
        <div className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
          {rules.map(r => {
            const sev = SEVERITY_MAP[r.severity] || SEVERITY_MAP.warning;
            const linkedAction = actions.find(a => a.id === r.action_id);
            return (
              <div key={r.id} className="flex items-center justify-between px-5 py-4 hover:bg-slate-50">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-800">{r.name}</span>
                    <span className={`px-2 py-0.5 text-xs rounded-full ${sev.color}`}>{sev.label}</span>
                    {!r.is_active && <span className="px-2 py-0.5 text-xs rounded-full bg-red-50 text-red-600">已禁用</span>}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">目标: {r.target_class} | 条件: {r.condition_expression}</p>
                  {linkedAction && <p className="text-xs text-green-600 mt-0.5">→ 关联 Action: {linkedAction.name}</p>}
                  {r.trigger_count > 0 && <p className="text-xs text-slate-400 mt-0.5">已触发 {r.trigger_count} 次 | 最近: {r.last_triggered_at || "-"}</p>}
                </div>
                <div className="flex items-center gap-1">
                  <button onClick={() => checkRule(r.id)} className="p-1.5 text-blue-400 hover:text-blue-600" title="手动检查">🔍</button>
                  <button onClick={() => toggleActive(r)} className="p-1.5 text-slate-400 hover:text-slate-600" title={r.is_active ? "禁用" : "启用"}>{r.is_active ? "⏸" : "▶️"}</button>
                  <button onClick={() => startEdit(r)} className="p-1.5 text-slate-400 hover:text-blue-600" title="编辑">✏️</button>
                  <button onClick={() => setDeleteTarget(r.id)} className="p-1.5 text-slate-400 hover:text-red-500" title="删除">🗑️</button>
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
              <h3 className="text-lg font-semibold">{editRule ? "编辑告警规则" : "新增告警规则"}</h3>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">规则名称 *</label>
                <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} className="input-field" placeholder="如：库存不足告警" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">描述</label>
                <textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} className="input-field" rows={2} />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">目标实体类 *</label>
                <input value={form.target_class} onChange={e => setForm(f => ({ ...f, target_class: e.target.value }))} className="input-field" placeholder="如：Inventory" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">条件表达式 *</label>
                <input value={form.condition_expression} onChange={e => setForm(f => ({ ...f, condition_expression: e.target.value }))} className="input-field" placeholder="如：stock_quantity < safety_stock" />
                <p className="text-xs text-slate-400 mt-1">SQL WHERE 风格的条件表达式</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">关联 Action</label>
                <select value={form.action_id} onChange={e => setForm(f => ({ ...f, action_id: e.target.value }))} className="input-field">
                  <option value="">不关联 Action</option>
                  {actions.filter(a => a.is_active).map(a => <option key={a.id} value={a.id}>{a.name} ({a.action_type})</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">严重程度</label>
                <select value={form.severity} onChange={e => setForm(f => ({ ...f, severity: e.target.value }))} className="input-field">
                  <option value="info">信息</option>
                  <option value="warning">警告</option>
                  <option value="critical">严重</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={resetForm} className="btn-outline">取消</button>
                <button onClick={save} className="btn-primary">保存</button>
              </div>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog isOpen={!!deleteTarget} title="删除告警规则" message="确定要删除此规则吗？" onConfirm={() => { if (deleteTarget) { deleteRule(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}
