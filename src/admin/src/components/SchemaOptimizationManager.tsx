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
  started_at: string;
  finished_at: string;
  changes_json?: {
    applied?: Record<string, number>;
    diff?: unknown;
    quality?: unknown;
  };
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

const progressPercent = (status: OptimizationProgress) => {
  if (!status.total) return 0;
  return Math.min(100, Math.round((status.progress / status.total) * 100));
};

const RUN_STATUS_BADGE: Record<string, string> = {
  success: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-600",
};

export default function SchemaOptimizationManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const inputRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<EventSource | null>(null);
  const completedRunRef = useRef<string | null>(null);
  const [files, setFiles] = useState<OptimizationFile[]>([]);
  const [runs, setRuns] = useState<OptimizationRun[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [progressStatus, setProgressStatus] =
    useState<OptimizationProgress | null>(null);

  const load = async () => {
    if (!activeScenario) return;
    setLoading(true);
    try {
      const [fileData, runData] = await Promise.all([
        api(`/api/scenarios/${activeScenario}/schema-optimization/files`),
        api(`/api/scenarios/${activeScenario}/schema-optimization/runs`),
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
      await api(
        `/api/scenarios/${activeScenario}/schema-optimization/files`,
        {
          method: "POST",
          body: form,
        },
      );
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
      await api(
        `/api/scenarios/${activeScenario}/schema-optimization/files/${id}`,
        {
          method: "DELETE",
        },
      );
      setSelectedIds((prev) => prev.filter((item) => item !== id));
      addToast("success", "文档已删除");
      await load();
    } catch (error: any) {
      addToast("error", error.message || "删除失败");
    }
  };

  const runOptimization = async () => {
    if (!activeScenario) return;
    completedRunRef.current = null;
    setOptimizing(true);
    setProgressStatus({
      run_id: "",
      running: true,
      phase: "starting",
      progress: 0,
      total: 100,
      message: "正在启动 Schema 优化任务",
    });
    try {
      const result = await api(
        `/api/scenarios/${activeScenario}/schema-optimization/optimize`,
        {
          method: "POST",
          body: JSON.stringify({ file_ids: selectedIds }),
        },
      );
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

    const stopMonitoring = () => {
      streamRef.current?.close();
      streamRef.current = null;
    };
    const applyStatus = async (status: OptimizationProgress) => {
      setProgressStatus(status);
      if (
        !status.running &&
        (status.phase === "done" || status.phase === "error")
      ) {
        if (completedRunRef.current === runId) return;
        completedRunRef.current = runId;
        stopMonitoring();
        setOptimizing(false);
        if (status.phase === "done") {
          invalidateCache(`schema:${activeScenario}`);
          invalidateCacheByPrefix("metrics:");
          invalidateCacheByPrefix("concepts:");
          addToast("success", status.message || "Schema 优化完成");
        } else {
          addToast("error", status.message || "Schema 优化失败");
        }
        await load();
      }
    };
    const source = new EventSource(
      `/api/scenarios/${activeScenario}/schema-optimization/stream/${runId}`,
    );
    streamRef.current = source;
    source.onmessage = async (event) => {
      await applyStatus(JSON.parse(event.data) as OptimizationProgress);
    };
    source.onerror = () => {
      // 不主动关闭：EventSource 会自动重连，确保持续从流式接口接收最终状态。
      console.warn("Schema 优化进度流暂时断开，正在自动重连");
    };
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  if (!activeScenario) {
    return (
      <div>
        <h2 className="mb-4 text-lg font-semibold text-slate-800">
          Schema 优化
        </h2>
        <ScenarioSelector />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Schema 优化</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
            className="btn-outline"
          >
            {uploading ? "上传中..." : "上传文档"}
          </button>
          <button
            onClick={runOptimization}
            disabled={optimizing || files.length === 0}
            className="btn-primary"
          >
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
        <div className="card space-y-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-700">
                {progressStatus.message}
              </div>
              <div className="mt-0.5 text-xs text-slate-400">
                阶段：{progressStatus.phase}
              </div>
            </div>
            <span className="text-sm font-semibold text-slate-600">
              {progressPercent(progressStatus)}%
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full ${progressStatus.phase === "error" ? "bg-red-500" : "bg-indigo-500"}`}
              style={{
                width: `${progressPercent(progressStatus)}%`,
              }}
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
            <h3 className="text-sm font-semibold text-slate-700">
              优化文档 ({files.length})
            </h3>
            <button onClick={load} className="btn-ghost text-xs">
              刷新
            </button>
          </div>
          {loading && <LoadingSpinner />}
          {!loading && files.length === 0 && (
            <EmptyState
              icon="📄"
              title="暂无优化文档"
              description="上传业务文档后可用于迭代优化未审核 Schema"
            />
          )}
          {!loading && files.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th className="w-12">选择</th>
                  <th>文件名</th>
                  <th>类型</th>
                  <th>大小</th>
                  <th>上传时间</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr key={file.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(file.id)}
                        onChange={() => toggleSelected(file.id)}
                      />
                    </td>
                    <td className="font-medium text-slate-700">
                      {file.original_filename}
                    </td>
                    <td>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                        {file.file_ext}
                      </span>
                    </td>
                    <td className="text-xs text-slate-500">
                      {formatSize(file.size)}
                    </td>
                    <td className="text-xs text-slate-500">
                      {file.uploaded_at}
                    </td>
                    <td className="text-right">
                      <button
                        onClick={() => remove(file.id)}
                        className="btn-ghost text-xs text-red-500"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card overflow-hidden">
          <h3 className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
            优化记录
          </h3>
          {runs.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-slate-400">
              暂无运行记录
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {runs.map((run) => {
                const applied = run.changes_json?.applied || {};
                const statusBadge =
                  RUN_STATUS_BADGE[run.status] ?? "bg-amber-50 text-amber-700";
                return (
                  <div key={run.id} className="space-y-2 px-5 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${statusBadge}`}
                      >
                        {run.status}
                      </span>
                      <span className="text-[11px] text-slate-400">
                        {run.started_at}
                      </span>
                    </div>
                    <p className="line-clamp-3 text-sm text-slate-600">
                      {run.summary || run.error || "-"}
                    </p>
                    {run.status === "success" && (
                      <p className="text-xs text-slate-400">
                        类 {applied.classes || 0}，关系{" "}
                        {applied.relationships || 0}，指标{" "}
                        {applied.metrics || 0}，概念 {applied.concepts || 0}
                      </p>
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
