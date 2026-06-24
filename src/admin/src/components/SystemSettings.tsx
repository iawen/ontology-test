"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import type { SystemSettings as T } from "@/lib/types";

export default function SystemSettings() {
  const { token, addToast } = useApp();
  const api = useApi(token);
  const [settings, setSettings] = useState<T>({ llm_provider: "openai", llm_model: "gpt-4", llm_api_key: "", llm_base_url: "", extraction_batch_size: 5, max_concurrent_extractions: 2, auto_extract_on_upload: true, log_level: "INFO" });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  const cacheKey = "settings:global";

  const load = async (force = false) => {
    if (!force) { const cached = getCacheData<T>(cacheKey); if (cached) { setSettings(cached); setLoading(false); return; } }
    setLoading(true);
    try { const d = await api("/api/admin/settings"); if (d) setSettings(prev => ({ ...prev, ...d })); setCacheData(cacheKey, d); }
    catch { /* settings may not exist yet */ } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    try { await api("/api/admin/settings", { method: "PUT", body: JSON.stringify(settings) }); addToast("success", "设置已保存"); invalidateCache(cacheKey); }
    catch (e: any) { addToast("error", e.message || "保存失败"); } finally { setSaving(false); }
  };

  const testConnection = async () => {
    try { await api("/api/admin/settings/test_connection", { method: "POST", body: JSON.stringify(settings) }); addToast("success", "连接测试成功"); }
    catch (e: any) { addToast("error", e.message || "连接测试失败"); }
  };

  if (loading) return <LoadingSpinner />;

  return (
    <div className="max-w-3xl space-y-6">
      <h2 className="text-lg font-semibold text-slate-800">系统设置</h2>

      <div className="card p-6">
        <h3 className="text-sm font-semibold text-slate-700 mb-4 flex items-center gap-2"><span className="w-6 h-6 rounded bg-indigo-50 flex items-center justify-center text-xs">🤖</span>LLM 配置</h3>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">Provider</label><select value={settings.llm_provider} onChange={(e) => setSettings({ ...settings, llm_provider: e.target.value })} className="w-full"><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="deepseek">DeepSeek</option><option value="custom">自定义</option></select></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">模型</label><input value={settings.llm_model} onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })} className="w-full" /></div>
          </div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">API Key</label><div className="flex gap-2"><input type={showApiKey ? "text" : "password"} value={settings.llm_api_key} onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })} className="flex-1" /><button onClick={() => setShowApiKey(!showApiKey)} className="btn-outline text-xs">{showApiKey ? "隐藏" : "显示"}</button></div></div>
          <div><label className="text-xs text-slate-500 font-medium block mb-1.5">Base URL</label><input value={settings.llm_base_url} onChange={(e) => setSettings({ ...settings, llm_base_url: e.target.value })} className="w-full" placeholder="https://api.openai.com/v1" /></div>
          <button onClick={testConnection} className="btn-outline">测试连接</button>
        </div>
      </div>

      <div className="card p-6">
        <h3 className="text-sm font-semibold text-slate-700 mb-4 flex items-center gap-2"><span className="w-6 h-6 rounded bg-amber-50 flex items-center justify-center text-xs">⚙️</span>提取参数</h3>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">批量大小</label><input type="number" value={settings.extraction_batch_size} onChange={(e) => setSettings({ ...settings, extraction_batch_size: Number(e.target.value) })} className="w-full" min={1} max={50} /></div>
            <div><label className="text-xs text-slate-500 font-medium block mb-1.5">最大并发数</label><input type="number" value={settings.max_concurrent_extractions} onChange={(e) => setSettings({ ...settings, max_concurrent_extractions: Number(e.target.value) })} className="w-full" min={1} max={10} /></div>
          </div>
          <div className="flex items-center justify-between">
            <div><p className="text-sm text-slate-700">上传后自动提取</p><p className="text-xs text-slate-400">上传数据文件后自动启动AI提取</p></div>
            <button onClick={() => setSettings({ ...settings, auto_extract_on_upload: !settings.auto_extract_on_upload })} className={`relative w-10 h-5 rounded-full transition-colors ${settings.auto_extract_on_upload ? "bg-indigo-600" : "bg-slate-300"}`}><span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${settings.auto_extract_on_upload ? "translate-x-5" : ""}`} /></button>
          </div>
        </div>
      </div>

      <div className="card p-6">
        <h3 className="text-sm font-semibold text-slate-700 mb-4 flex items-center gap-2"><span className="w-6 h-6 rounded bg-emerald-50 flex items-center justify-center text-xs">📝</span>日志配置</h3>
        <div><label className="text-xs text-slate-500 font-medium block mb-1.5">日志级别</label><select value={settings.log_level} onChange={(e) => setSettings({ ...settings, log_level: e.target.value })} className="w-48"><option value="DEBUG">DEBUG</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></div>
      </div>

      <div className="flex items-center justify-end gap-3">
        <button onClick={() => load(true)} className="btn-outline">重置</button>
        <button onClick={save} disabled={saving} className="btn-primary flex items-center gap-2">{saving && <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}{saving ? "保存中..." : "保存设置"}</button>
      </div>
    </div>
  );
}
