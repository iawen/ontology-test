"use client";

import { useEffect, useRef, useState } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { invalidateCache, invalidateCacheByPrefix } from "@/lib/cache";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";

interface OptimizationFile {
  id: string;
  original_filename: string;
  file_ext: string;
  size: number;
  uploaded_at: string;
}

interface OptimizationRun {
  id: string;
  status: "running" | "success" | "failed";
  summary: string;
  error: string;
  created_at: string;
  finished_at: string;
  changes_json?: { applied?: Record<string, number> };
}

interface OptimizationProgress {
  run_id: string;
  running: boolean;
  phase: string;
  progress: number;
  total: number;
  message: string;
  result?: { applied?: Record<string, number>; summary?: string } | null;
}

const formatSize = (size: number) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

export default function SchemaOptimizationManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const inputRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<EventSource | null>(null);
  const [files, setFiles] = useState<OptimizationFile[]>([]);
  const [runs, setRuns] = useState<OptimizationRun[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [progressStatus, setProgressStatus] = useState<OptimizationProgress | null>(null);

  const load = async () => {
    if (!activeScenario) return;
    setLoading(true);
    try {
      const [fileData, runData] = await Promise.all([
        api(`/api/admin/scenarios/${activeScenario}/schema-optimization/files`),
        api(`/api/admin/scenarios/${activeScenario}/schema-optimization/runs`),
      ]);
      setFiles(fileData || []);
      setRuns(runData || []);
    } catch (error: any) {
      addToast("error", error.message || "加载 Schema 优化资料失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    return () => {
      streamRef.current?.close();
      streamRef.current = null;
    };
  }, [activeScenario]);

  const upload = async (fileList: FileList) => {
    if (!activeScenario || !fileList.length) return;
    setUploading(true);
    try {
      const form = new FormData();
      Array.from(fileList).forEach((file) => form.append("files", file));
      await api(`/api/admin/scenarios/${activeScenario}/schema-optimization/files`, {
        method: "POST",
        body: form,
      });
      addToast("success", "优化文档已上传");
      inputRef.current && (inputRef.current.value = "");
      await load();
    } catch (error: any) {
      addToast("error", error.message || "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (id: string) => {
    if (!activeScenario) return;
    try {
      await api(`/api/admin/scenarios/${activeScenario}/schema-optimization/files/${id}`, { method: "DELETE" });
      setSelectedIds((prev) => prev.filter((item) => item !== id));
      addToast("success", "文档已删除");
      await load();
    } catch (error: any) {
      addToast("error", error.message || "删除失败");
    }
  };

  const runOptimization = async () => {
    if (!activeScenario) return;
    setOptimizing(true);
    setProgressStatus({ run_id: "", running: true, phase: "starting", progress: 0, total: 100, message: "正在启动 Schema 优化任务" });
    try {
      const result = await api(`/api/admin/scenarios/${activeScenario}/schema-optimization/optimize`, {
        method: "POST",
        body: JSON.stringify({ file_ids: selectedIds }),
      });
      subscribeProgress(result.run_id);
    } catch (error: any) {
      addToast("error", error.message || "Schema 优化失败");
      setProgressStatus(null);
      setOptimizing(false);
    }
  };

  const subscribeProgress = (runId: string) => {
    if (!activeScenario || !runId) {
      setOptimizing(false);
      return;
    }
    streamRef.current?.close();
    const source = new EventSource(`http://localhost:8000/api/admin/scenarios/${activeScenario}/schema-optimization/stream/${runId}`);
    streamRef.current = source;
    source.onmessage = async (event) => {
      const status = JSON.parse(event.data) as OptimizationProgress;
      setProgressStatus(status);
      if (!status.running && (status.phase === "done" || status.phase === "error")) {
        source.close();
        streamRef.current = null;
        setOptimizing(false);
        if (status.phase === "done") {
          invalidateCache(`schema:${activeScenario}`);
          invalidateCacheByPrefix("metrics:");
          invalidateCacheByPrefix("concepts:");
          addToast("success", status.message || "Schema 优化完成");
          await load();
        } else {
          addToast("error", status.message || "Schema 优化失败");
          await load();
        }
      }
    };
    source.onerror = () => {
      source.close();
      streamRef.current = null;
      setOptimizing(false);
      addToast("error", "Schema 优化进度连接中断");
    };
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]);
  };

  if (!activeScenario) {
    return <div><h2 className="text-lg font-semibold text-slate-800 mb-4">Schema 优化</h2><ScenarioSelector /></div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Schema 优化</h2>
        <div className="flex items-center gap-2">
          <button onClick={() => inputRef.current?.click()} disabled={uploading} className="btn-outline">
            {uploading ? "上传中..." : "上传文档"}
          </button>
          <button onClick={runOptimization} disabled={optimizing || files.length === 0} className="btn-primary">
            {optimizing ? "优化中..." : "开始优化"}
          </button>
        </div>
      </div>
      <ScenarioSelector />
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".doc,.docx,.pdf,.xlsx"
        className="hidden"
        onChange={(event) => event.target.files && upload(event.target.files)}
      />

      {progressStatus && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-700">{progressStatus.message}</div>
              <div className="text-xs text-slate-400 mt-0.5">阶段：{progressStatus.phase}</div>
            </div>
            <span className="text-sm font-semibold text-slate-600">{Math.round((progressStatus.progress / Math.max(progressStatus.total || 100, 1)) * 100)}%</span>
          </div>
          <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
            <div className={`h-full ${progressStatus.phase === "error" ? "bg-red-500" : "bg-indigo-500"}`} style={{ width: `${Math.min(100, Math.round((progressStatus.progress / Math.max(progressStatus.total || 100, 1)) * 100))}%` }} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px] gap-5">
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
            <h3 className="text-sm font-semibold text-slate-700">优化文档 ({files.length})</h3>
            <button onClick={load} className="btn-ghost text-xs">刷新</button>
          </div>
          {loading ? <LoadingSpinner /> : files.length === 0 ? (
            <EmptyState icon="📄" title="暂无优化文档" description="上传业务文档后可用于迭代优化未审核 Schema" />
          ) : (
            <table className="data-table">
              <thead><tr><th className="w-12">选择</th><th>文件名</th><th>类型</th><th>大小</th><th>上传时间</th><th className="text-right">操作</th></tr></thead>
              <tbody>
                {files.map((file) => (
                  <tr key={file.id}>
                    <td><input type="checkbox" checked={selectedIds.includes(file.id)} onChange={() => toggleSelected(file.id)} /></td>
                    <td className="font-medium text-slate-700">{file.original_filename}</td>
                    <td><span className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">{file.file_ext}</span></td>
                    <td className="text-xs text-slate-500">{formatSize(file.size)}</td>
                    <td className="text-xs text-slate-500">{file.uploaded_at}</td>
                    <td className="text-right"><button onClick={() => remove(file.id)} className="btn-ghost text-xs text-red-500">删除</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card overflow-hidden">
          <h3 className="px-5 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">优化记录</h3>
          {runs.length === 0 ? (
            <div className="px-5 py-8 text-sm text-slate-400 text-center">暂无运行记录</div>
          ) : (
            <div className="divide-y divide-slate-100">
              {runs.map((run) => {
                const applied = run.changes_json?.applied || {};
                return (
                  <div key={run.id} className="px-5 py-4 space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${run.status === "success" ? "bg-emerald-50 text-emerald-700" : run.status === "failed" ? "bg-red-50 text-red-600" : "bg-amber-50 text-amber-700"}`}>{run.status}</span>
                      <span className="text-[11px] text-slate-400">{run.created_at}</span>
                    </div>
                    <p className="text-sm text-slate-600 line-clamp-3">{run.summary || run.error || "-"}</p>
                    {run.status === "success" && (
                      <p className="text-xs text-slate-400">类 {applied.classes || 0}，关系 {applied.relationships || 0}，指标 {applied.metrics || 0}，概念 {applied.concepts || 0}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}