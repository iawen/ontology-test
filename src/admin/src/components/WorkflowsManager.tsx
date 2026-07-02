"use client";

import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";

// 定义工作流实例与日志的数据类型
interface WorkflowInstance {
  id: string;
  scenario_id: string;
  workflow_def_id: string;
  workflow_name: string;
  action_id: string;
  status: "pending" | "running" | "completed" | "failed" | "paused";
  current_step: number;
  total_steps: number;
  context: string;
  steps_json: string;
  result: string;
  triggered_by: string;
  created_at: string;
  updated_at: string;
}

interface StepLog {
  id: string;
  instance_id: string;
  step_index: number;
  step_name: string;
  step_type: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "waiting_approval";
  assignee: string;
  result: string;
  started_at: string;
  finished_at: string;
  duration: number;
}

export default function WorkflowsManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);

  // 状态管理
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedInstance, setSelectedInstance] = useState<WorkflowInstance | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  // 获取工作流实例列表
  const fetchInstances = async () => {
    if (!activeScenario) return;
    setLoading(true);
    try {
      const res = await api(`/api/admin/scenarios/${activeScenario}/workflow_instances`, { method: "GET" });
      setInstances(res || []);
    } catch (error: any) {
      addToast(error.message || "获取工作流实例失败", "error");
    } finally {
      setLoading(false);
    }
  };

  // 获取指定实例的详细步骤与日志
  const fetchInstanceDetails = async (instance: WorkflowInstance) => {
    setSelectedInstance(instance);
    setLoadingLogs(true);
    try {
      const res = await api(`/api/admin/scenarios/${activeScenario}/workflow_instances/${instance.id}`, { method: "GET" });
      setStepLogs(res?.step_logs || []);
    } catch (error: any) {
      addToast(error.message || "获取步骤日志失败", "error");
    } finally {
      setLoadingLogs(false);
    }
  };

  useEffect(() => {
    fetchInstances();
  }, [activeScenario]);

  // 状态样式映射
  const getStatusBadge = (status: string) => {
    const config: Record<string, string> = {
      pending: "bg-yellow-50 text-yellow-700 border-yellow-200",
      running: "bg-blue-50 text-blue-700 border-blue-200 animate-pulse",
      completed: "bg-green-50 text-green-700 border-green-200",
      failed: "bg-red-50 text-red-700 border-red-200",
      paused: "bg-slate-100 text-slate-700 border-slate-300",
      skipped: "bg-slate-50 text-slate-400 border-slate-200",
      waiting_approval: "bg-purple-50 text-purple-700 border-purple-200",
    };
    return (
      <span className={`px-2 py-1 text-xs font-medium rounded-md border ${config[status] || "bg-slate-50 text-slate-600"}`}>
        {status.toUpperCase()}
      </span>
    );
  };

  // 过滤后的数据
  const filteredInstances = instances.filter(
    (ins) => statusFilter === "all" || ins.status === statusFilter
  );

  return (
    <div className="space-y-6 p-6 max-w-[1600px] mx-auto">
      {/* 头部区域 */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white p-4 rounded-xl border border-slate-100 shadow-sm">
        <div>
          <h1 className="text-xl font-bold text-slate-800 flex items-center gap-2">
            <span>⚙️</span> 工作流实例管理
          </h1>
          <p className="text-xs text-slate-400 mt-1">监控和管理系统自动化工作流的执行进度及步骤详情。</p>
        </div>
        <div className="flex items-center gap-3">
          <ScenarioSelector />
          <button
            onClick={fetchInstances}
            className="px-3 py-2 bg-slate-50 hover:bg-slate-100 border border-slate-200 text-slate-600 rounded-lg text-sm transition-colors flex items-center gap-1"
          >
            🔄 刷新
          </button>
        </div>
      </div>

      {/* 过滤器选项卡 */}
      <div className="flex border-b border-slate-200 gap-2">
        {["all", "running", "completed", "failed", "pending"].map((tab) => (
          <button
            key={tab}
            onClick={() => setStatusFilter(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
              statusFilter === tab
                ? "border-indigo-600 text-indigo-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {tab === "all" ? "全部实例" : tab.toUpperCase()}
          </button>
        ))}
      </div>

      {/* 主体布局 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧及中间：实例列表 */}
        <div className="lg:col-span-2 space-y-3">
          {loading ? (
            <div className="bg-white p-12 rounded-xl border border-slate-100 flex justify-center"><LoadingSpinner /></div>
          ) : filteredInstances.length === 0 ? (
            <EmptyState title="未找到工作流执行实例" description="当前场景或筛选条件下暂无执行数据。" />
          ) : (
            <div className="space-y-3">
              {filteredInstances.map((ins) => {
                const progressPercent = ins.total_steps > 0 ? (ins.current_step / ins.total_steps) * 100 : 0;
                const isSelected = selectedInstance?.id === ins.id;

                return (
                  <div
                    key={ins.id}
                    onClick={() => fetchInstanceDetails(ins)}
                    className={`bg-white p-4 rounded-xl border transition-all cursor-pointer hover:shadow-md ${
                      isSelected ? "border-indigo-500 ring-1 ring-indigo-500" : "border-slate-100"
                    }`}
                  >
                    <div className="flex justify-between items-start gap-4">
                      <div className="space-y-1 flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold text-slate-800 truncate">{ins.workflow_name}</span>
                          <span className="text-xs px-2 py-0.5 bg-slate-100 text-slate-500 rounded">
                            ID: {ins.id}
                          </span>
                        </div>
                        <p className="text-xs text-slate-400">
                          触发方式: <span className="text-slate-600 font-medium">{ins.triggered_by}</span> · 
                          创建时间: {ins.created_at}
                        </p>
                      </div>
                      <div>{getStatusBadge(ins.status)}</div>
                    </div>

                    {/* 进度条展示 */}
                    <div className="mt-4 space-y-1">
                      <div className="flex justify-between text-xs text-slate-500">
                        <span>节点进度</span>
                        <span className="font-medium text-slate-700">
                          {ins.current_step} / {ins.total_steps} 步
                        </span>
                      </div>
                      <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
                        <div
                          className={`h-full transition-all duration-500 ${
                            ins.status === "failed" ? "bg-red-500" : ins.status === "completed" ? "bg-green-500" : "bg-indigo-500"
                          }`}
                          style={{ width: `${Math.min(progressPercent, 100)}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 右侧：单实例步骤追踪面板 */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-xl border border-slate-100 p-5 shadow-sm sticky top-6 max-h-[calc(100vh-140px)] overflow-y-auto">
            <h2 className="text-base font-bold text-slate-800 pb-3 border-b border-slate-100 mb-4 flex items-center gap-2">
              <span>📊</span> 步骤追踪面板
            </h2>

            {!selectedInstance ? (
              <div className="text-center py-12 text-slate-400 text-sm">
                👈 请在左侧选择一个工作流实例以查看详细的执行步骤
              </div>
            ) : loadingLogs ? (
              <div className="flex justify-center py-12"><LoadingSpinner /></div>
            ) : (
              <div className="space-y-6">
                {/* 详情头部 */}
                <div className="bg-slate-50 p-3 rounded-lg text-xs space-y-1.5 text-slate-600">
                  <div><strong className="text-slate-700">工作流名称:</strong> {selectedInstance.workflow_name}</div>
                  <div><strong className="text-slate-700">最新更新:</strong> {selectedInstance.updated_at}</div>
                  {selectedInstance.result && (
                    <div className="pt-1.5 border-t border-slate-200 mt-1">
                      <strong className="text-slate-700 block mb-0.5">最终执行输出:</strong>
                      <pre className="bg-slate-900 text-slate-100 p-2 rounded text-[11px] overflow-x-auto whitespace-pre-wrap max-h-32">
                        {selectedInstance.result}
                      </pre>
                    </div>
                  )}
                </div>

                {/* 垂直时间轴步骤列表 */}
                <div className="relative pl-6 border-l-2 border-slate-100 ml-2 space-y-6">
                  {stepLogs.length === 0 ? (
                    <p className="text-xs text-slate-400 italic">暂无历史步骤记录</p>
                  ) : (
                    stepLogs.map((log, idx) => {
                      // 根据状态计算小圆圈颜色
                      const circleColors: Record<string, string> = {
                        completed: "bg-green-500 ring-green-100",
                        failed: "bg-red-500 ring-red-100",
                        running: "bg-blue-500 ring-blue-100 animate-ping",
                        pending: "bg-slate-300 ring-slate-100",
                      };

                      return (
                        <div key={log.id || idx} className="relative group">
                          {/* 时间轴圆点 */}
                          <div className={`absolute -left-[31px] top-1 w-3 h-3 rounded-full ring-4 bg-slate-300 ${circleColors[log.status] || ""}`} />
                          
                          {/* 步骤核心内容 */}
                          <div className="space-y-1">
                            <div className="flex justify-between items-center gap-2">
                              <h4 className="text-sm font-semibold text-slate-800">
                                {idx + 1}. {log.step_name || `节点 ${log.step_index}`}
                              </h4>
                              {getStatusBadge(log.status)}
                            </div>
                            
                            <div className="flex items-center gap-2 text-xs text-slate-400">
                              <span className="bg-slate-100 px-1.5 py-0.5 rounded text-slate-500">
                                {log.step_type}
                              </span>
                              {log.duration > 0 && (
                                <span>⏱️ {log.duration.toFixed(2)}s</span>
                              )}
                              {log.assignee && (
                                <span>👤 {log.assignee}</span>
                              )}
                            </div>

                            {/* 步骤结果或报错详情 */}
                            {log.result && (
                              <div className="mt-2 text-[11px] bg-slate-50 p-2 rounded border border-slate-100 text-slate-600 max-h-24 overflow-y-auto whitespace-pre-wrap font-mono">
                                {log.result}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}