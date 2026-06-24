"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData } from "@/lib/cache";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import type { AuditLog } from "@/lib/types";

const ACTION_LABELS: Record<string, string> = { create: "创建", update: "更新", delete: "删除", upload: "上传", extract: "提取", login: "登录", logout: "登出", import: "导入", export: "导出" };
const ACTION_COLORS: Record<string, string> = { create: "bg-emerald-50 text-emerald-600", update: "bg-blue-50 text-blue-600", delete: "bg-red-50 text-red-600", upload: "bg-amber-50 text-amber-600", extract: "bg-purple-50 text-purple-600", login: "bg-indigo-50 text-indigo-600", logout: "bg-slate-100 text-slate-500" };
const RESOURCE_LABELS: Record<string, string> = { scenario: "场景", file: "文件", schema_class: "Schema类", schema_relationship: "Schema关系", concept: "概念", metric: "指标", chart_rule: "图表规则", glossary: "术语", skill: "技能", user: "用户", settings: "设置" };

export default function AuditLogs() {
  const { token, addToast } = useApp();
  const api = useApi(token);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterAction, setFilterAction] = useState("");
  const [filterResource, setFilterResource] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const cacheKey = `audit_logs:${page}:${filterAction}:${filterResource}`;

  const load = async (force = false) => {
    if (!force) { const cached = getCacheData<AuditLog[]>(cacheKey); if (cached) { setLogs(cached); setLoading(false); return; } }
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("page", String(page)); params.set("page_size", String(pageSize));
      if (filterAction) params.set("action", filterAction);
      if (filterResource) params.set("resource_type", filterResource);
      const d = await api(`/api/admin/audit_logs?${params}`);
      const data = d || []; setLogs(data); setCacheData(cacheKey, data);
    } catch { addToast("error", "加载操作日志失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [page, filterAction, filterResource]);

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-800 mb-4">操作日志</h2>
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1"><SearchInput value={search} onChange={setSearch} placeholder="搜索日志..." /></div>
        <select value={filterAction} onChange={(e) => { setFilterAction(e.target.value); setPage(1); }} className="text-sm w-32"><option value="">全部操作</option>{Object.entries(ACTION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select value={filterResource} onChange={(e) => { setFilterResource(e.target.value); setPage(1); }} className="text-sm w-32"><option value="">全部资源</option>{Object.entries(RESOURCE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button onClick={() => load(true)} className="btn-outline text-xs">刷新</button>
      </div>

      {loading ? <LoadingSpinner /> : logs.length === 0 ? (
        <EmptyState icon="📝" title="暂无操作日志" />
      ) : (
        <div className="card overflow-hidden">
          <table className="data-table"><thead><tr><th>时间</th><th>用户</th><th>操作</th><th>资源</th><th>详情</th><th>IP</th></tr></thead>
          <tbody>{logs.map(l => (
            <tr key={l.id}><td className="text-xs text-slate-400 whitespace-nowrap">{l.created_at}</td><td className="text-sm text-slate-700">{l.username}</td><td><span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${ACTION_COLORS[l.action] || "bg-slate-100 text-slate-500"}`}>{ACTION_LABELS[l.action] || l.action}</span></td><td><span className="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{RESOURCE_LABELS[l.resource_type] || l.resource_type}</span></td><td className="text-xs text-slate-500 max-w-xs truncate">{l.detail}</td><td className="text-xs text-slate-400 font-mono">{l.ip}</td></tr>
          ))}</tbody></table>
        </div>
      )}

      {logs.length >= pageSize && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1} className="btn-outline text-xs disabled:opacity-50">上一页</button>
          <span className="text-xs text-slate-400">第 {page} 页</span>
          <button onClick={() => setPage(page + 1)} disabled={logs.length < pageSize} className="btn-outline text-xs disabled:opacity-50">下一页</button>
        </div>
      )}
    </div>
  );
}
