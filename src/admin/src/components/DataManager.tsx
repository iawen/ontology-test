"use client";

import { useState, useEffect, useRef } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import Modal from "@/components/ui/Modal";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { FileInfo, DataConnection, DBTable, DBTablePreview } from "@/lib/types";

export default function DataManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [search, setSearch] = useState("");
  const [batchSize, setBatchSize] = useState(5);
  const [extractStatus, setExtractStatus] = useState({ running: false, phase: "", progress: 0, total: 0, message: "" });
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total_rows: number } | null>(null);
  const [loadingCsvPreview, setLoadingCsvPreview] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  // ── 数据库直连状态 ──
  const [activeTab, setActiveTab] = useState<"csv" | "database">("csv");
  const [connections, setConnections] = useState<DataConnection[]>([]);
  const [showConnForm, setShowConnForm] = useState(false);
  const [connForm, setConnForm] = useState({ name: "", db_type: "postgresql" as "postgresql" | "mysql", connection_url: "" });
  const [testingConn, setTestingConn] = useState(false);
  const [dbTables, setDbTables] = useState<DBTable[]>([]);
  const [dbPreview, setDbPreview] = useState<DBTablePreview | null>(null);
  const [dbPreviewTable, setDbPreviewTable] = useState<string | null>(null);
  const [dbTablesConnId, setDbTablesConnId] = useState<string | null>(null);
  const [loadingTables, setLoadingTables] = useState(false);
  const [loadingTablesConnId, setLoadingTablesConnId] = useState<string | null>(null);
  const [loadingDbPreview, setLoadingDbPreview] = useState(false);
  const [extractDataSource, setExtractDataSource] = useState<"auto" | "csv" | "database">("auto");
  const [selectedCsvFiles, setSelectedCsvFiles] = useState<string[]>([]);
  const [selectedDbTables, setSelectedDbTables] = useState<string[]>([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const extractEventSourceRef = useRef<EventSource | null>(null);
  const activeScenarioRef = useRef<string | null>(activeScenario || null);
  const cacheKey = `files_${activeScenario}`;
  const connCacheKey = `dbconn_${activeScenario}`;

  // ── 加载文件列表 ──
  const loadFiles = async (force = false, scenarioId = activeScenario) => {
    if (!scenarioId) return;
    const scenarioCacheKey = `files_${scenarioId}`;
    if (!force) { const c = getCacheData<FileInfo[]>(scenarioCacheKey); if (c) { if (activeScenarioRef.current === scenarioId) setFiles(c); return; } }
    setLoading(true);
    try { const d = await api(`/api/scenarios/${scenarioId}/files`); const data = d?.files || []; if (activeScenarioRef.current === scenarioId) setFiles(data); setCacheData(scenarioCacheKey, data); }
    catch { addToast("error", "加载文件列表失败"); }
    finally { if (activeScenarioRef.current === scenarioId) setLoading(false); }
  };

  // ── 加载数据库连接列表 ──
  const loadConnections = async (force = false, scenarioId = activeScenario) => {
    if (!scenarioId) return;
    const scenarioConnCacheKey = `dbconn_${scenarioId}`;
    if (!force) { const c = getCacheData<DataConnection[]>(scenarioConnCacheKey); if (c) { if (activeScenarioRef.current === scenarioId) setConnections(c); return; } }
    try {
      const d = await api(`/api/admin/scenarios/${scenarioId}/data_connections`);
      const data = d || [];
      if (activeScenarioRef.current === scenarioId) setConnections(data);
      setCacheData(scenarioConnCacheKey, data);
    } catch { /* 静默 */ }
  };

  // ── 上传文件 ──
  const uploadFiles = async (fileList: FileList) => {
    if (!activeScenario || !fileList.length) return;
    setUploading(true);
    try {
      const fd = new FormData();
      Array.from(fileList).forEach(f => fd.append("files", f));
      await api(`http://localhost:8000/api/scenarios/${activeScenario}/upload`, { method: "POST", body: fd });
      addToast("success", `${fileList.length} 个文件上传成功`);
      invalidateCache(cacheKey);
      loadFiles(true);
    } catch (e: any) { addToast("error", e.message || "上传失败"); }
    finally { setUploading(false); }
  };

  // ── 删除文件 ──
  const deleteFile = async (name: string) => {
    try {
      await api(`/api/scenarios/${activeScenario}/files/${name}`, { method: "DELETE" });
      addToast("success", "文件已删除");
      setSelectedCsvFiles(prev => prev.filter(item => item !== name));
      invalidateCache(cacheKey);
      loadFiles(true);
    } catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  // ── 预览文件 ──
  const previewCsv = async (name: string) => {
    const scenarioId = activeScenario;
    if (!scenarioId) return;
    if (previewFile === name && previewData && !loadingCsvPreview) { return; }
    setPreviewFile(name);
    setPreviewData(null);
    setLoadingCsvPreview(true);
    setDbPreview(null); setDbPreviewTable(null);
    try {
      const d = await api(`/api/scenarios/${scenarioId}/files/${name}/preview`);
      if (activeScenarioRef.current === scenarioId) setPreviewData(d);
    } catch { if (activeScenarioRef.current === scenarioId) { addToast("error", "预览失败"); setPreviewFile(null); } }
    finally { if (activeScenarioRef.current === scenarioId) setLoadingCsvPreview(false); }
  };

  const closeCsvPreview = () => {
    setPreviewFile(null);
    setPreviewData(null);
    setLoadingCsvPreview(false);
  };

  // ── 提取本体 ──
  const startExtract = async () => {
    try {
      let effectiveDataSource = extractDataSource;
      if (extractDataSource === "auto") {
        if (selectedDbTables.length > 0) effectiveDataSource = "database";
        else if (selectedCsvFiles.length > 0) effectiveDataSource = "csv";
        else effectiveDataSource = activeTab;
      }

      if (effectiveDataSource === "csv" && selectedCsvFiles.length === 0) {
        addToast("warning", "请先选择要提取的 CSV 文件");
        return;
      }
      if (effectiveDataSource === "database" && selectedDbTables.length === 0) {
        addToast("warning", "请先选择要提取的数据库表");
        return;
      }

      const body: Record<string, any> = { batch_size: batchSize, data_source: effectiveDataSource };
      if (effectiveDataSource === "csv") body.selected_files = selectedCsvFiles;
      if (effectiveDataSource === "database") body.selected_tables = selectedDbTables;
      // 如果选择数据库模式，传入活跃连接
      if (effectiveDataSource === "database") {
        const selectedConn = connections.find(c => c.id === dbTablesConnId) || connections.find(c => c.is_active === 1) || connections[0];
        if (selectedConn) body.db_connection_id = selectedConn.id;
      }
      await api(`/api/scenarios/${activeScenario}/extract`, { method: "POST", body: JSON.stringify(body) });
      addToast("success", "提取已启动");
      startExtractStream();
    } catch (e: any) { addToast("error", e.message || "启动提取失败"); }
  };

  // SSE 流式监听提取进度
  const startExtractStream = () => {
    if (!activeScenario) return;

    // 关闭之前的连接（如果有）
    if (extractEventSourceRef.current) {
      extractEventSourceRef.current.close();
    }

    // 使用相对路径，通过 next.config.ts 的 rewrite 代理到后端
    const eventSource = new EventSource("http://localhost:8000/api/extract/stream");
    extractEventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      try {
        const status = JSON.parse(event.data);
        setExtractStatus(status);

        // 如果已完成或出错，关闭连接
        if (!status.running && (status.phase === "done" || status.phase === "error")) {
          eventSource.close();
          extractEventSourceRef.current = null;

          // 完成后刷新文件列表
          if (status.phase === "done") {
            invalidateCache(cacheKey);
            loadFiles(true);
          }
        }
      } catch (e) {
        console.error("解析 SSE 消息失败:", e);
      }
    };

    eventSource.onerror = (error) => {
      console.error("SSE 连接错误:", error);
      eventSource.close();
      extractEventSourceRef.current = null;

      // 降级为轮询模式
      if (extractStatus.running) {
        pollExtractStatus();
      }
    };
  };

  const pollExtractStatus = async () => {
    try {
      const d = await api("/api/extract/status");
      setExtractStatus(d);
      if (d.running) setTimeout(pollExtractStatus, 2000);
    } catch { /* 静默 */ }
  };

  // ── 测试数据库连接 ──
  const testConnection = async () => {
    setTestingConn(true);
    try {
      const d = await api("/api/admin/data_connections/test", {
        method: "POST",
        body: JSON.stringify(connForm),
      });
      if (d.ok) addToast("success", `连接成功！发现 ${d.table_count || 0} 张表`);
      else addToast("error", d.error || "连接失败");
    } catch (e: any) { addToast("error", e.message || "连接测试失败"); }
    finally { setTestingConn(false); }
  };

  // ── 保存数据库连接 ──
  const saveConnection = async () => {
    if (!connForm.name || !connForm.connection_url) {
      addToast("warning", "请填写连接名称和连接URL");
      return;
    }
    try {
      await api(`/api/admin/scenarios/${activeScenario}/data_connections`, {
        method: "POST",
        body: JSON.stringify(connForm),
      });
      addToast("success", "数据库连接已保存");
      setShowConnForm(false);
      setConnForm({ name: "", db_type: "postgresql", connection_url: "" });
      invalidateCache(connCacheKey);
      loadConnections(true);
    } catch (e: any) { addToast("error", e.message || "保存失败"); }
  };

  // ── 删除数据库连接 ──
  const deleteConnection = async (connId: string) => {
    try {
      await api(`/api/admin/scenarios/${activeScenario}/data_connections/${connId}`, { method: "DELETE" });
      addToast("success", "连接已删除");
      invalidateCache(connCacheKey);
      loadConnections(true);
      setDbTables([]); setSelectedDbTables([]); setDbTablesConnId(null); setDbPreview(null); setDbPreviewTable(null);
    } catch (e: any) { addToast("error", e.message || "删除失败"); }
  };

  // ── 浏览数据库表 ──
  const browseTables = async (connId: string) => {
    const scenarioId = activeScenario;
    if (!scenarioId) return;
    setLoadingTables(true);
    setLoadingTablesConnId(connId);
    setDbTables([]);
    setSelectedDbTables([]);
    setDbTablesConnId(connId);
    setDbPreview(null); setDbPreviewTable(null);
    try {
      const d = await api(`/api/admin/scenarios/${scenarioId}/data_connections/${connId}/tables`);
      if (activeScenarioRef.current === scenarioId) {
        setDbTables(d || []);
        setDbTablesConnId(connId);
        setDbPreview(null); setDbPreviewTable(null);
      }
    } catch (e: any) { if (activeScenarioRef.current === scenarioId) addToast("error", e.message || "获取表列表失败"); }
    finally { if (activeScenarioRef.current === scenarioId) { setLoadingTables(false); setLoadingTablesConnId(null); } }
  };

  // ── 预览数据库表 ──
  const previewDbTable = async (connId: string, tableName: string) => {
    const scenarioId = activeScenario;
    if (!scenarioId) return;
    if (dbPreviewTable === tableName && dbPreview && !loadingDbPreview) { return; }
    setDbPreviewTable(tableName);
    setDbPreview(null);
    setLoadingDbPreview(true);
    try {
      const d = await api(`/api/admin/scenarios/${scenarioId}/data_connections/${connId}/tables/${encodeURIComponent(tableName)}/preview`);
      if (activeScenarioRef.current === scenarioId) {
        setDbPreview(d);
        setPreviewFile(null); setPreviewData(null);
      }
    } catch { if (activeScenarioRef.current === scenarioId) { addToast("error", "预览失败"); setDbPreviewTable(null); } }
    finally { if (activeScenarioRef.current === scenarioId) setLoadingDbPreview(false); }
  };

  const closeDbPreview = () => {
    setDbPreview(null);
    setDbPreviewTable(null);
    setLoadingDbPreview(false);
  };

  useEffect(() => {
    activeScenarioRef.current = activeScenario || null;
    setFiles([]);
    setLoading(false);
    setConnections([]);
    setDbTables([]);
    setDbTablesConnId(null);
    setLoadingTables(false);
    setLoadingTablesConnId(null);
    setPreviewFile(null);
    setPreviewData(null);
    setLoadingCsvPreview(false);
    setDbPreview(null);
    setDbPreviewTable(null);
    setLoadingDbPreview(false);
    setShowConnForm(false);
    setSelectedCsvFiles([]);
    setSelectedDbTables([]);
    setSearch("");
    if (activeScenario) {
      loadFiles(false, activeScenario);
      loadConnections(false, activeScenario);
      pollExtractStatus();
    }
    // 清理 SSE 连接
    return () => {
      if (extractEventSourceRef.current) {
        extractEventSourceRef.current.close();
        extractEventSourceRef.current = null;
      }
    };
  }, [activeScenario]);

  if (!activeScenario) return <ScenarioSelector />;

  const filteredFiles = files.filter(f => f.name.toLowerCase().includes(search.toLowerCase()));
  const getDbTableName = (table: DBTable) => table.table_name || table.name || "";
  const getDbTableKey = (table: DBTable, index: number) => `${table.schema || "default"}:${getDbTableName(table) || "unnamed"}:${index}`;
  const getDbPreviewColumns = () => (dbPreview?.columns || []).map((column) => typeof column === "string" ? column : column.name);
  const visibleFileNames = filteredFiles.map(f => f.name);
  const visibleDbTableNames = dbTables.map(getDbTableName).filter(Boolean);
  const allVisibleCsvSelected = visibleFileNames.length > 0 && visibleFileNames.every(name => selectedCsvFiles.includes(name));
  const allVisibleDbSelected = visibleDbTableNames.length > 0 && visibleDbTableNames.every(name => selectedDbTables.includes(name));
  const toggleCsvFile = (name: string) => setSelectedCsvFiles(prev => prev.includes(name) ? prev.filter(item => item !== name) : [...prev, name]);
  const toggleDbTable = (name: string) => setSelectedDbTables(prev => prev.includes(name) ? prev.filter(item => item !== name) : [...prev, name]);
  const toggleVisibleCsvFiles = () => setSelectedCsvFiles(prev => allVisibleCsvSelected ? prev.filter(name => !visibleFileNames.includes(name)) : Array.from(new Set([...prev, ...visibleFileNames])));
  const toggleVisibleDbTables = () => setSelectedDbTables(prev => allVisibleDbSelected ? prev.filter(name => !visibleDbTableNames.includes(name)) : Array.from(new Set([...prev, ...visibleDbTableNames])));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-slate-800">数据管理</h2>
        <ScenarioSelector />
      </div>

      {/* ── Tab 切换 ── */}
      <div className="flex border-b border-slate-200">
        <button onClick={() => setActiveTab("csv")} className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${activeTab === "csv" ? "border-blue-500 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
          📁 CSV 文件上传
        </button>
        <button onClick={() => setActiveTab("database")} className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${activeTab === "database" ? "border-blue-500 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
          🗄️ 数据库直连
        </button>
      </div>

      {/* ══════════════════════════════════════════════ */}
      {/* CSV 文件上传 Tab                              */}
      {/* ══════════════════════════════════════════════ */}
      {activeTab === "csv" && (
        <div className="space-y-4">
          {/* 上传区域 */}
          <div className="bg-white rounded-xl border border-slate-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-slate-700">上传 CSV 文件</h3>
              <button onClick={() => fileInputRef.current?.click()} disabled={uploading} className="px-4 py-2 bg-blue-500 text-white text-sm rounded-lg hover:bg-blue-600 disabled:opacity-50">
                {uploading ? "上传中..." : "选择文件"}
              </button>
            </div>
            <input ref={fileInputRef} type="file" multiple accept=".csv" className="hidden" onChange={e => e.target.files && uploadFiles(e.target.files)} />
            <p className="text-xs text-slate-400">支持 .csv 格式，可多选</p>
          </div>

          {/* 文件列表 */}
          <div className="bg-white rounded-xl border border-slate-200">
            <div className="flex items-center justify-between p-4 border-b border-slate-100">
              <div className="flex items-center gap-3">
                <input type="checkbox" checked={allVisibleCsvSelected} disabled={visibleFileNames.length === 0} onChange={toggleVisibleCsvFiles} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" aria-label="选择当前文件列表" />
                <h3 className="text-sm font-semibold text-slate-700">已上传文件 ({files.length})</h3>
                <span className="text-xs text-slate-400">已选 {selectedCsvFiles.length}</span>
              </div>
              <div className="flex items-center gap-2">
                <SearchInput value={search} onChange={setSearch} placeholder="搜索文件..." />
                <button onClick={() => loadFiles(true)} className="p-1.5 text-slate-400 hover:text-slate-600"><svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg></button>
              </div>
            </div>
            {loading ? <LoadingSpinner /> : filteredFiles.length === 0 ? <EmptyState title="暂无文件" /> : (
              <div className="divide-y divide-slate-50">
                {filteredFiles.map(f => (
                  <div key={f.name} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50">
                    <div className="flex items-center gap-3 min-w-0">
                      <input type="checkbox" checked={selectedCsvFiles.includes(f.name)} onChange={() => toggleCsvFile(f.name)} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" aria-label={`选择 ${f.name}`} />
                      <span className="text-lg">📄</span>
                      <div className="min-w-0">
                        <button onClick={() => previewCsv(f.name)} className="text-sm text-blue-600 hover:underline truncate block">{f.name}</button>
                        <span className="text-xs text-slate-400">{(f.size / 1024).toFixed(1)} KB · {f.rows} 行</span>
                      </div>
                    </div>
                    <button onClick={() => setDeleteTarget(f.name)} className="p-1 text-slate-300 hover:text-red-500"><svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg></button>
                  </div>
                ))}
              </div>
            )}
          </div>

        </div>
      )}

      {/* ══════════════════════════════════════════════ */}
      {/* 数据库直连 Tab                                */}
      {/* ══════════════════════════════════════════════ */}
      {activeTab === "database" && (
        <div className="space-y-4">
          {/* 添加连接 */}
          <div className="bg-white rounded-xl border border-slate-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-slate-700">数据库连接</h3>
              <button onClick={() => setShowConnForm(!showConnForm)} className="px-4 py-2 bg-blue-500 text-white text-sm rounded-lg hover:bg-blue-600">
                {showConnForm ? "取消" : "+ 添加连接"}
              </button>
            </div>

            {/* 连接表单 */}
            {showConnForm && (
              <div className="space-y-4 p-4 bg-slate-50 rounded-lg mb-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">连接名称</label>
                    <input value={connForm.name} onChange={e => setConnForm({ ...connForm, name: e.target.value })} placeholder="如：智能工厂 PostgreSQL" className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">数据库类型</label>
                    <select value={connForm.db_type} onChange={e => setConnForm({ ...connForm, db_type: e.target.value as any})} className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
                      <option value="postgresql">PostgreSQL</option>
                      <option value="mysql">MySQL</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1">连接 URL</label>
                  <input value={connForm.connection_url} onChange={e => setConnForm({ ...connForm, connection_url: e.target.value })} placeholder="postgresql://user:password@host:5432/dbname" className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  <p className="mt-1 text-xs text-slate-400">
                    PostgreSQL: postgresql://user:pass@host:port/dbname &nbsp;|&nbsp;
                    MySQL: mysql://user:pass@host:port/dbname
                  </p>
                </div>
                <div className="flex gap-2">
                  <button onClick={testConnection} disabled={testingConn || !connForm.connection_url} className="px-4 py-2 bg-slate-600 text-white text-sm rounded-lg hover:bg-slate-700 disabled:opacity-50">
                    {testingConn ? "测试中..." : "测试连接"}
                  </button>
                  <button onClick={saveConnection} disabled={!connForm.name || !connForm.connection_url} className="px-4 py-2 bg-blue-500 text-white text-sm rounded-lg hover:bg-blue-600 disabled:opacity-50">
                    保存连接
                  </button>
                </div>
              </div>
            )}

            {/* 已有连接列表 */}
            {connections.length === 0 ? (
              <p className="text-sm text-slate-400 text-center py-6">暂无数据库连接，点击"添加连接"开始</p>
            ) : (
              <div className="space-y-3">
                {connections.map(conn => (
                  <div key={conn.id} className={`p-4 rounded-lg border ${conn.is_active ? "border-green-200 bg-green-50" : "border-slate-200 bg-white"}`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className="text-lg">{conn.db_type === "postgresql" ? "🐘" : "🐬"}</span>
                        <div>
                          <span className="text-sm font-medium text-slate-700">{conn.name}</span>
                          <span className={`ml-2 px-2 py-0.5 text-xs rounded-full ${conn.is_active ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"}`}>
                            {conn.is_active ? "已激活" : "未激活"}
                          </span>
                          <p className="text-xs text-slate-400 font-mono mt-0.5">{conn.connection_url_masked}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button onClick={() => browseTables(conn.id)} disabled={loadingTables} className="px-3 py-1.5 text-xs bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed">
                          {loadingTablesConnId === conn.id ? "浏览中..." : "浏览表"}
                        </button>
                        <button onClick={() => deleteConnection(conn.id)} className="px-3 py-1.5 text-xs bg-red-50 text-red-600 rounded-lg hover:bg-red-100">删除</button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 数据库表列表 */}
          {(loadingTables || dbTables.length > 0) && (
            <div className="bg-white rounded-xl border border-slate-200">
              <div className="p-4 border-b border-slate-100">
                <div className="flex items-center gap-3">
                  <input type="checkbox" checked={allVisibleDbSelected} disabled={visibleDbTableNames.length === 0} onChange={toggleVisibleDbTables} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" aria-label="选择当前数据库表列表" />
                  <h3 className="text-sm font-semibold text-slate-700">数据库表 ({dbTables.length})</h3>
                  <span className="text-xs text-slate-400">已选 {selectedDbTables.length}</span>
                </div>
              </div>
              {loadingTables ? <LoadingSpinner text="正在加载数据库表..." /> : (
                <div className="divide-y divide-slate-50">
                  {dbTables.map((t, index) => {
                    const tableName = getDbTableName(t);
                    return (
                    <div key={getDbTableKey(t, index)} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50">
                      <div className="flex items-center gap-3">
                        <input type="checkbox" checked={selectedDbTables.includes(tableName)} onChange={() => tableName && toggleDbTable(tableName)} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" aria-label={`选择 ${tableName || "未命名表"}`} />
                        <span className="text-lg">🗃️</span>
                        <div>
                          <button onClick={() => { const connId = dbTablesConnId || connections.find(c => c.is_active === 1)?.id || connections[0]?.id; if (connId && tableName) previewDbTable(connId, tableName); }} className="text-sm text-blue-600 hover:underline">{tableName || "未命名表"}</button>
                          <span className="text-xs text-slate-400 ml-2">{t.row_count} 行 · {t.columns?.length || 0} 列</span>
                        </div>
                      </div>
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      )}

      {/* ══════════════════════════════════════════════ */}
      {/* 提取本体（共用）                               */}
      {/* ══════════════════════════════════════════════ */}
      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h3 className="text-sm font-semibold text-slate-700 mb-4">提取本体</h3>
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
          仅会抽取已勾选的数据源：CSV {selectedCsvFiles.length} 个，数据库表 {selectedDbTables.length} 个。抽取时会在选中集合范围内统一推导 Class 关系、Concept 层级和 Metric 关联。
        </div>
        <div className="flex items-center gap-4 mb-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">数据源:</label>
            <select value={extractDataSource} onChange={e => setExtractDataSource(e.target.value as any)} className="px-3 py-1.5 border border-slate-300 rounded-lg text-sm">
              <option value="auto">自动检测</option>
              <option value="csv">仅 CSV 文件</option>
              <option value="database">仅数据库</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">批次大小:</label>
            <input type="number" value={batchSize} onChange={e => setBatchSize(Number(e.target.value))} min={1} max={20} className="w-16 px-2 py-1.5 border border-slate-300 rounded-lg text-sm text-center" />
          </div>
          <button onClick={startExtract} disabled={extractStatus.running} className="px-5 py-2 bg-green-500 text-white text-sm rounded-lg hover:bg-green-600 disabled:opacity-50">
            {extractStatus.running ? "提取中..." : "开始提取"}
          </button>
        </div>

        {/* 提取进度 */}
        {extractStatus.running && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>{extractStatus.message || extractStatus.phase}</span>
              <span>{extractStatus.progress}/{extractStatus.total}</span>
            </div>
            <div className="w-full bg-slate-100 rounded-full h-2">
              <div className="bg-blue-500 h-2 rounded-full transition-all" style={{ width: `${extractStatus.total ? (extractStatus.progress / extractStatus.total * 100) : 0}%` }} />
            </div>
          </div>
        )}
        {extractStatus.phase === "done" && <p className="text-sm text-green-600">✅ 提取完成！</p>}
        {extractStatus.phase === "error" && <p className="text-sm text-red-600">❌ {extractStatus.message}</p>}
      </div>

      <ConfirmDialog isOpen={!!deleteTarget} title="删除文件" message={`确定要删除文件 "${deleteTarget}" 吗？`} onConfirm={() => { if (deleteTarget) { deleteFile(deleteTarget); setDeleteTarget(null); } }} onCancel={() => setDeleteTarget(null)} />
      <Modal isOpen={!!previewFile} onClose={closeCsvPreview} title={`预览: ${previewFile || "CSV 文件"}`} width="max-w-6xl">
        {loadingCsvPreview ? <LoadingSpinner text="正在加载 CSV 数据..." /> : previewData ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>{previewData.total_rows ?? previewData.rows?.length ?? 0} 行</span>
              <span>{(previewData.columns || []).length} 列 · 最多展示 50 行</span>
            </div>
            <div className="overflow-auto max-h-[65vh] border border-slate-100 rounded-lg">
              <table className="w-full text-xs">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50">{(previewData.columns || []).map((c) => (<th key={c} className="px-3 py-2 text-left font-medium text-slate-500 whitespace-nowrap border-b border-slate-100">{c}</th>))}</tr></thead>
                <tbody>{(previewData.rows || []).slice(0, 50).map((row: any, i: number) => (<tr key={i} className="border-t border-slate-50 hover:bg-slate-50/60">{(previewData.columns || []).map((c: string) => (<td key={c} className="px-3 py-2 text-slate-600 whitespace-nowrap max-w-[240px] truncate">{String(row[c] ?? "")}</td>))}</tr>))}</tbody>
              </table>
            </div>
          </div>
        ) : <EmptyState title="暂无预览数据" />}
      </Modal>
      <Modal isOpen={!!dbPreviewTable} onClose={closeDbPreview} title={`预览: ${dbPreviewTable || "数据库表"}`} width="max-w-6xl">
        {loadingDbPreview ? <LoadingSpinner text="正在加载表数据..." /> : dbPreview ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>{dbPreview.row_count >= 0 ? `${dbPreview.row_count} 行` : "行数未知"}</span>
              <span>{getDbPreviewColumns().length} 列 · 最多展示 50 行</span>
            </div>
            <div className="overflow-auto max-h-[65vh] border border-slate-100 rounded-lg">
              <table className="w-full text-xs">
                <thead className="sticky top-0 z-10"><tr className="bg-slate-50">{getDbPreviewColumns().map((c) => (<th key={c} className="px-3 py-2 text-left font-medium text-slate-500 whitespace-nowrap border-b border-slate-100">{c}</th>))}</tr></thead>
                <tbody>{(dbPreview.sample_rows || dbPreview.rows || []).slice(0, 50).map((row: any, i: number) => (<tr key={i} className="border-t border-slate-50 hover:bg-slate-50/60">{getDbPreviewColumns().map((c: string) => (<td key={c} className="px-3 py-2 text-slate-600 whitespace-nowrap max-w-[240px] truncate">{String(row[c] ?? "")}</td>))}</tr>))}</tbody>
              </table>
            </div>
          </div>
        ) : <EmptyState title="暂无预览数据" />}
      </Modal>
    </div>
  );
}
