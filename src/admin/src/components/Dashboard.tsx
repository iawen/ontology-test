"use client";

import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import StatCard from "@/components/ui/StatCard";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { DashboardStats, ExtractionLog } from "@/lib/types";

const STATUS_BADGE: Record<string, string> = { running: "bg-blue-50 text-blue-600", success: "bg-emerald-50 text-emerald-600", failed: "bg-red-50 text-red-600" };
const STATUS_LABEL: Record<string, string> = { running: "运行中", success: "成功", failed: "失败" };
const TYPE_LABELS: Record<string, string> = { schema: "Schema", ontology: "本体", metrics: "指标", concepts: "概念", glossary: "术语" };

export default function Dashboard() {
  const { token, activeScenario, scenarios } = useApp();
  const api = useApi(token);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async (force = false) => {
    const cacheKey = `dashboard:${activeScenario}`;
    if (!force) {
      const cached = getCacheData<DashboardStats>(cacheKey);
      if (cached) { setStats(cached); setLoading(false); return; }
    }
    setLoading(true);
    try {
      const d = await api("/api/admin/dashboard");
      setStats(d);
      setCacheData(cacheKey, d);
    } catch {
      const fallback: DashboardStats = {
        total_scenarios: scenarios.length, active_scenarios: scenarios.filter((s) => s.is_active).length,
        total_files: 0, total_schema_classes: 0, total_metrics: 0, total_concepts: 0,
        total_glossary_terms: 0, total_skills: 0, recent_extractions: [],
      };
      setStats(fallback);
      setCacheData(cacheKey, fallback);
    } finally { setLoading(false); }
  };

  useEffect(() => { if (activeScenario) load(); else if (scenarios.length === 0) setLoading(false); }, [activeScenario]);

  if (!activeScenario && scenarios.length === 0) {
    return (
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-4">仪表盘</h2>
        <div className="card p-8 text-center text-slate-400">
          <p className="text-sm">请先创建场景以查看统计数据</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-800 mb-4">仪表盘</h2>
      <ScenarioSelector />

      {loading ? <LoadingSpinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <StatCard icon="📁" label="场景数" value={stats?.total_scenarios ?? 0} color="bg-indigo-50 text-indigo-600" />
            <StatCard icon="📄" label="数据文件" value={stats?.total_files ?? 0} color="bg-emerald-50 text-emerald-600" />
            <StatCard icon="🔗" label="Schema 类" value={stats?.total_schema_classes ?? 0} color="bg-amber-50 text-amber-600" />
            <StatCard icon="📐" label="指标数" value={stats?.total_metrics ?? 0} color="bg-purple-50 text-purple-600" />
            <StatCard icon="🌳" label="概念数" value={stats?.total_concepts ?? 0} color="bg-cyan-50 text-cyan-600" />
            <StatCard icon="📖" label="专用名称" value={stats?.total_glossary_terms ?? 0} color="bg-pink-50 text-pink-600" />
            <StatCard icon="⚡" label="技能包" value={stats?.total_skills ?? 0} color="bg-orange-50 text-orange-600" />
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            <div className="card p-5">
              <h3 className="text-sm font-semibold text-slate-700 mb-4">最近提取记录</h3>
              {(!stats?.recent_extractions || stats.recent_extractions.length === 0) ? (
                <p className="text-xs text-slate-400">暂无提取记录</p>
              ) : (
                <div className="space-y-2">
                  {stats.recent_extractions.slice(0, 5).map((log) => (
                    <div key={log.id} className="flex items-center justify-between py-2 border-b border-slate-50 last:border-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[log.status]}`}>{STATUS_LABEL[log.status]}</span>
                        <span className="text-xs text-slate-600">{TYPE_LABELS[log.type] || log.type}</span>
                      </div>
                      <span className="text-xs text-slate-400">{log.started_at}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="card p-5">
              <h3 className="text-sm font-semibold text-slate-700 mb-4">快捷操作</h3>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { icon: "📁", label: "新建场景", page: "scenarios" as const },
                  { icon: "📄", label: "上传数据", page: "data" as const },
                  { icon: "🔗", label: "提取Schema", page: "schema" as const },
                  { icon: "📖", label: "维护术语", page: "glossary" as const },
                ].map((a) => (
                  <div key={a.label} className="flex items-center gap-2 p-3 rounded-lg border border-slate-200 text-sm text-slate-600">
                    <span>{a.icon}</span><span>{a.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
