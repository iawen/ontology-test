"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData } from "@/lib/cache";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { ExtractionLog } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = { schema: "Schema 提取", ontology: "本体提取", metrics: "指标提取", concepts: "概念提取", glossary: "术语提取" };
const STATUS_BADGE: Record<string, string> = { running: "bg-blue-50 text-blue-600", success: "bg-emerald-50 text-emerald-600", failed: "bg-red-50 text-red-600" };
const STATUS_LABEL: Record<string, string> = { running: "运行中", success: "成功", failed: "失败" };
const STATUS_ICON: Record<string, string> = { running: "⏳", success: "✅", failed: "❌" };

export default function ExtractionLogs() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [logs, setLogs] = useState<ExtractionLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");

  const cacheKey = `extraction_logs:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) { const cached = getCacheData<ExtractionLog[]>(cacheKey); if (cached) { setLogs(cached); setLoading(false); return; } }
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (activeScenario) params.set("scenario_id", activeScenario);
      if (filterType) params.set("type", filterType);
      if (filterStatus) params.set("status", filterStatus);
      const d = await api(`/api/admin/extraction_logs?${params}`);
      const data = d || []; setLogs(data); setCacheData(cacheKey, data);
    } catch { addToast("error", "加载提取日志失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); }, [activeScenario, filterType, filterStatus]);

  const filtered = logs.filter(l => !search || l.message?.toLowerCase().includes(search.toLowerCase()));

  if (!activeScenario) return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">提取日志</h2><ScenarioSelector /></div>;

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-800 mb-4">提取日志</h2>
      <ScenarioSelector />
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1"><SearchInput value={search} onChange={setSearch} placeholder="搜索日志..." /></div>
        <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="text-sm w-32"><option value="">全部类型</option>{Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="text-sm w-32"><option value="">全部状态</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button onClick={() => load(true)} className="btn-outline text-xs">刷新</button>
      </div>

      {loading ? <LoadingSpinner /> : filtered.length === 0 ? (
        <EmptyState icon="📋" title="暂无提取日志" description="执行AI提取后日志将显示在这里" />
      ) : (
        <div className="space-y-2">
          {filtered.map(log => (
            <div key={log.id} className="card p-4 hover:shadow-sm transition-shadow">
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-3">
                  <span className="text-lg mt-0.5">{STATUS_ICON[log.status] || "📋"}</span>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium text-slate-700">{TYPE_LABELS[log.type] || log.type}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[log.status]}`}>{STATUS_LABEL[log.status]}</span>
                      <span className="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{log.trigger === "manual" ? "手动" : "自动"}</span>
                    </div>
                    <p className="text-xs text-slate-500">{log.message}</p>
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-xs text-slate-400">{log.started_at}</p>
                  {log.duration > 0 && <p className="text-[10px] text-slate-400 mt-0.5">耗时 {log.duration}s</p>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
