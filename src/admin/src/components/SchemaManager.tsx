"use client";

import { useState, useEffect, useRef } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import {
  getCacheData,
  setCacheData,
  invalidateCache,
} from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { SchemaClass, SchemaField, SchemaRelationship } from "@/lib/types";

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer]);

export default function SchemaManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [classes, setClasses] = useState<SchemaClass[]>([]);
  const [relationships, setRelationships] = useState<SchemaRelationship[]>([]);
  const [loading, setLoading] = useState(false);
  const [editClass, setEditClass] = useState<Partial<SchemaClass> | null>(null);
  const [isClassModalOpen, setIsClassModalOpen] = useState(false);
  const [newRel, setNewRel] = useState({
    id: undefined as number | undefined,
    source: "",
    target: "",
    type: "",
    join_key: "",
    is_reviewed: false,
  });
  const [isRelModalOpen, setIsRelModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    type: "class" | "rel";
    id: string | number;
  } | null>(null);
  const [viewMode, setViewMode] = useState<"graph" | "table">("graph");

  // 🛠️ 声明 ECharts 实例的 Ref
  const echartsRef = useRef<any>(null);

  const cacheKey = `schema:${activeScenario}`;

  const emptyField = (): SchemaField => ({
    name: "",
    physical_name: "",
    type: "text",
    description: "",
    is_primary_key: false,
    is_foreign_key: false,
  });

  const updateEditField = (index: number, patch: Partial<SchemaField>) => {
    const fields = [...(editClass?.fields || [])];
    fields[index] = { ...fields[index], ...patch } as SchemaField;
    const properties = fields.map((field) => field.name || field.physical_name).filter(Boolean);
    setEditClass({ ...editClass!, fields, properties });
  };

  const addEditField = () => {
    setEditClass({ ...editClass!, fields: [...(editClass?.fields || []), emptyField()] });
  };

  const removeEditField = (index: number) => {
    const fields = (editClass?.fields || []).filter((_, i) => i !== index);
    const properties = fields.map((field) => field.name || field.physical_name).filter(Boolean);
    setEditClass({ ...editClass!, fields, properties });
  };

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) {
      const cached = getCacheData<{
        classes: SchemaClass[];
        relationships: SchemaRelationship[];
      }>(cacheKey);
      if (cached) {
        setClasses(cached.classes);
        setRelationships(cached.relationships);
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    try {
      const [c, r] = await Promise.all([
        api(`/api/admin/scenarios/${activeScenario}/schema/classes`),
        api(`/api/admin/scenarios/${activeScenario}/schema/relationships`),
      ]);
      setClasses(c || []);
      setRelationships(r || []);
      setCacheData(cacheKey, { classes: c || [], relationships: r || [] });
    } catch {
      addToast("error", "加载Schema失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeScenario) load();
  }, [activeScenario]);

  // 🛠️ 缩放与布局控制函数
  const handleZoomIn = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    const option = chartInstance.getOption();
    // 放大：增大 zoom 系数
    const currentZoom = option.series[0].zoom || 1;
    chartInstance.setOption({
      series: [{ zoom: currentZoom + 0.2 }]
    });
  };

  const handleZoomOut = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    const option = chartInstance.getOption();
    // 缩小：减小 zoom 系数，最低不小于 0.2
    const currentZoom = option.series[0].zoom || 1;
    chartInstance.setOption({
      series: [{ zoom: Math.max(0.2, currentZoom - 0.2) }]
    });
  };

  const handleResetLayout = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    // 复位：重置 zoom 级别，并清空用户的拖拽位移偏移量、平移中心点
    chartInstance.setOption({
      series: [{
        zoom: 1,
        center: null
      }]
    });
    // 重新触发力引导布局使其平滑重排
    chartInstance.dispatchAction({
      type: "graphRoam",
      dx: 0,
      dy: 0
    });
  };

  const saveClass = async () => {
    if (!editClass?.id || !editClass.name_cn) {
      addToast("warning", "ID和中文名必填");
      return;
    }
    const isEdit = classes.some((c) => c.id === editClass.id);
    const fields = (editClass.fields || []).filter((field) => field.name || field.physical_name);
    const payload = {
      ...editClass,
      fields,
      properties: editClass.properties?.length
        ? editClass.properties
        : fields.map((field) => field.name || field.physical_name).filter(Boolean),
    };
    try {
      await api(
        `/api/admin/scenarios/${activeScenario}/schema/classes${isEdit ? `/${editClass.id}` : ""}`,
        { method: isEdit ? "PUT" : "POST", body: JSON.stringify(payload) },
      );
      addToast("success", isEdit ? "类已更新" : "类已创建");
      setIsClassModalOpen(false);
      setEditClass(null);
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const deleteClass = async (id: string) => {
    try {
      await api(`/api/admin/scenarios/${activeScenario}/schema/classes/${id}`, {
        method: "DELETE",
      });
      addToast("success", "类已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "删除失败");
    }
  };

  const saveRelationship = async () => {
    if (!newRel.source || !newRel.target || !newRel.type) {
      addToast("warning", "源、目标和类型必填");
      return;
    }
    const isEdit = typeof newRel.id === "number";
    try {
      await api(`/api/admin/scenarios/${activeScenario}/schema/relationships${isEdit ? `/${newRel.id}` : ""}`, {
        method: isEdit ? "PUT" : "POST",
        body: JSON.stringify(newRel),
      });
      addToast("success", isEdit ? "关系已更新" : "关系已创建");
      setIsRelModalOpen(false);
      setNewRel({ id: undefined, source: "", target: "", type: "", join_key: "", is_reviewed: false });
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const deleteRelationship = async (id: number) => {
    try {
      await api(
        `/api/admin/scenarios/${activeScenario}/schema/relationships/${id}`,
        { method: "DELETE" },
      );
      addToast("success", "关系已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "删除失败");
    }
  };

  const graphOption = {
    tooltip: {},
    legend: { data: classes.map((c) => c.id) },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,        
        draggable: true,   
        label: { show: true, fontSize: 12 },
        data: classes.map((c) => ({
          name: c.id,
          symbolSize: 40,
          itemStyle: { color: "#6366f1" },
        })),
        links: relationships.map((r) => ({
          source: r.source,
          target: r.target,
          label: { show: true, formatter: r.type },
        })),
        force: { 
          repulsion: 300,        // 🛠️ 略微加大排斥力使其分布更均匀
          edgeLength: [120, 240], // 🛠️ 调整边长区间
        },
      },
    ],
  };

  if (!activeScenario)
    return (
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-4">
          Schema 管理
        </h2>
        <ScenarioSelector />
      </div>
    );

  return (
    <div>
      {/* 头部及控制栏保持原样 */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">Schema 管理</h2>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-slate-200 overflow-hidden">
            <button
              onClick={() => setViewMode("graph")}
              className={`px-3 py-1.5 text-xs ${viewMode === "graph" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}
            >
              图谱
            </button>
            <button
              onClick={() => setViewMode("table")}
              className={`px-3 py-1.5 text-xs ${viewMode === "table" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}
            >
              表格
            </button>
          </div>
          <button
            onClick={() => {
              setEditClass({
                scenario_id: activeScenario,
                properties: [],
                fields: [],
                csv_file: "",
                primary_key: "",
                is_reviewed: false,
              });
              setIsClassModalOpen(true);
            }}
            className="btn-primary"
          >
            + 新增类
          </button>
          <button
            onClick={() => setIsRelModalOpen(true)}
            className="btn-outline"
          >
            + 新增关系
          </button>
        </div>
      </div>
      <ScenarioSelector />

      {loading ? (
        <LoadingSpinner />
      ) : classes.length === 0 ? (
        <EmptyState
          icon="🔗"
          title="暂无Schema"
          description="上传数据后使用AI提取，或手动创建"
        />
      ) : viewMode === "graph" ? (
        /* 🛠️ 优化图谱视窗结构：引入相对定位容器与外部控制按钮 */
        <div className="card p-4 relative" style={{ height: 500 }}>
          
          {/* 工具控制浮层 */}
          <div className="absolute top-6 right-6 z-10 flex flex-col gap-1.5 bg-white/90 backdrop-blur border border-slate-200 p-1.5 rounded-lg shadow-sm">
            <button
              onClick={handleZoomIn}
              title="放大"
              className="w-8 h-8 flex items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line><line x1="11" y1="8" x2="11" y2="14"></line><line x1="8" y1="11" x2="14" y2="11"></line></svg>
            </button>
            <button
              onClick={handleZoomOut}
              title="缩小"
              className="w-8 h-8 flex items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line><line x1="8" y1="11" x2="14" y2="11"></line></svg>
            </button>
            <div className="h-[1px] bg-slate-100 my-0.5 mx-1" />
            <button
              onClick={handleResetLayout}
              title="自动布局复位"
              className="w-8 h-8 flex items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"></path><path d="M16 3h5v5"></path><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"></path><path d="M8 21H3v-5"></path></svg>
            </button>
          </div>

          <ReactEChartsCore
            ref={echartsRef} // 🛠️ 绑定 Ref
            echarts={echarts}
            option={graphOption}
            style={{ height: "100%" }}
          />
        </div>
      ) : (
        /* 下方表格逻辑和各种 Modal 保持原样不作改动 */
        <div className="space-y-6">
          <div className="card overflow-hidden">
            <h3 className="px-5 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">
              类 ({classes.length})
            </h3>
            <div className="overflow-x-auto">
              <table className="data-table min-w-[1060px] table-fixed">
                <colgroup>
                  <col className="w-32" />
                  <col className="w-24" />
                  <col className="w-56" />
                  {/* <col className="w-64" /> */}
                  <col className="w-24" />
                  <col className="w-20" />
                  <col className="w-20" />
                  <col className="w-20" />
                  <col className="w-16" />
                </colgroup>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>中文名</th>
                    <th>描述</th>
                    <th>数据文件</th>
                    <th>主键</th>
                    <th>字段</th>
                    <th>审核</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {classes.map((c) => (
                    <tr key={c.id}>
                      <td className="font-mono text-xs truncate" title={c.id}>
                        {c.id}
                      </td>
                      <td className="font-medium truncate" title={c.name_cn}>
                        {c.name_cn}
                      </td>
                      <td className="text-slate-500 truncate" title={c.description}>
                        {c.description}
                      </td>
                      <td className="text-xs truncate" title={c.csv_file}>
                        {c.csv_file}
                      </td>
                      <td className="text-xs truncate" title={c.primary_key}>
                        {c.primary_key}
                      </td>
                      <td className="text-xs text-slate-500">
                        {(c.fields || []).length}
                      </td>
                      <td>
                        <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${c.is_reviewed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                          {c.is_reviewed ? "通过" : "待审"}
                        </span>
                      </td>
                      <td className="text-right whitespace-nowrap">
                        <button
                          onClick={() => {
                            setEditClass({ ...c, fields: c.fields || [] });
                            setIsClassModalOpen(true);
                          }}
                          className="btn-ghost text-xs"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() =>
                            setDeleteTarget({ type: "class", id: c.id })
                          }
                          className="btn-ghost text-xs text-red-500"
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card overflow-hidden">
            <h3 className="px-5 py-3 text-sm font-semibold text-slate-700 border-b border-slate-100">
              关系 ({relationships.length})
            </h3>
            <table className="data-table">
              <thead>
                <tr>
                  <th>源</th>
                  <th>目标</th>
                  <th>类型</th>
                  <th>JOIN字段</th>
                  <th>审核</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((r) => (
                  <tr key={r.id}>
                    <td className="font-mono text-xs">{r.source}</td>
                    <td className="font-mono text-xs">{r.target}</td>
                    <td>{r.type}</td>
                    <td className="text-xs">{r.join_key}</td>
                    <td>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${r.is_reviewed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                        {r.is_reviewed ? "通过" : "待审"}
                      </span>
                    </td>
                    <td className="text-right">
                      <button
                        onClick={() => {
                          setNewRel({
                            id: r.id,
                            source: r.source,
                            target: r.target,
                            type: r.type,
                            join_key: r.join_key,
                            is_reviewed: !!r.is_reviewed,
                          });
                          setIsRelModalOpen(true);
                        }}
                        className="btn-ghost text-xs"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() =>
                          setDeleteTarget({ type: "rel", id: r.id })
                        }
                        className="btn-ghost text-xs text-red-500"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 以下 Modal 及 ConfirmDialog 维持原逻辑不变... */}
      <Modal
        isOpen={isClassModalOpen}
        onClose={() => {
          setIsClassModalOpen(false);
          setEditClass(null);
        }}
        title={editClass?.id && classes.some((c) => c.id === editClass.id) ? "编辑类" : "新增类"}
        width="max-w-6xl"
        footer={
          <>
            <button onClick={() => { setIsClassModalOpen(false); setEditClass(null); }} className="btn-outline">取消</button>
            <button onClick={saveClass} className="btn-primary">保存</button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">类 ID</label>
            <input value={editClass?.id || ""} onChange={(e) => setEditClass({ ...editClass!, id: e.target.value })} className="w-full" placeholder="Sale" />
          </div>
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">中文名</label>
            <input value={editClass?.name_cn || ""} onChange={(e) => setEditClass({ ...editClass!, name_cn: e.target.value })} className="w-full" placeholder="销售记录" />
          </div>
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">描述</label>
            <textarea value={editClass?.description || ""} onChange={(e) => setEditClass({ ...editClass!, description: e.target.value })} className="w-full" rows={2} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">数据文件</label>
              <input value={editClass?.csv_file || ""} onChange={(e) => setEditClass({ ...editClass!, csv_file: e.target.value })} className="w-full" placeholder="sale.csv" />
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">主键</label>
              <input value={editClass?.primary_key || ""} onChange={(e) => setEditClass({ ...editClass!, primary_key: e.target.value })} className="w-full" placeholder="sale_id" />
            </div>
          </div>
          <label className="inline-flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={!!editClass?.is_reviewed} onChange={(e) => setEditClass({ ...editClass!, is_reviewed: e.target.checked })} />
            用户审核通过
          </label>
          {/* <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">属性 (逗号分隔)</label>
            <input value={(editClass?.properties || []).join(", ")} onChange={(e) => setEditClass({ ...editClass!, properties: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} className="w-full" />
          </div> */}
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 bg-slate-50 border-b border-slate-200">
              <div>
                <div className="text-sm font-semibold text-slate-700">字段明细</div>
                <div className="text-xs text-slate-500 mt-0.5">维护 schema_classes.fields 中的列信息</div>
              </div>
              <button type="button" onClick={addEditField} className="btn-outline text-xs">
                + 添加字段
              </button>
            </div>
            {(editClass?.fields || []).length === 0 ? (
              <div className="px-4 py-6 text-sm text-slate-400 text-center">
                暂无字段，点击添加字段开始维护
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table min-w-[1100px] table-fixed">
                  <colgroup>
                    <col className="w-40" />
                    <col className="w-44" />
                    <col className="w-28" />
                    <col className="w-72" />
                    <col className="w-20" />
                    <col className="w-20" />
                    <col className="w-20" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>业务名</th>
                      <th>物理列名</th>
                      <th>类型</th>
                      <th>描述</th>
                      <th>主键</th>
                      <th>外键</th>
                      <th className="text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(editClass?.fields || []).map((field, index) => (
                      <tr key={index}>
                        <td>
                          <input
                            value={field.name || ""}
                            onChange={(e) => updateEditField(index, { name: e.target.value })}
                            className="w-full text-xs"
                            placeholder="销售金额"
                          />
                        </td>
                        <td>
                          <input
                            value={field.physical_name || ""}
                            onChange={(e) => updateEditField(index, { physical_name: e.target.value })}
                            className="w-full text-xs font-mono"
                            placeholder="sales_amount"
                          />
                        </td>
                        <td>
                          <select
                            value={field.type || "text"}
                            onChange={(e) => updateEditField(index, { type: e.target.value as SchemaField["type"] })}
                            className="w-full text-xs"
                          >
                            <option value="text">text</option>
                            <option value="numeric">numeric</option>
                            <option value="date">date</option>
                            <option value="boolean">boolean</option>
                          </select>
                        </td>
                        <td>
                          <input
                            value={field.description || ""}
                            onChange={(e) => updateEditField(index, { description: e.target.value })}
                            className="w-full text-xs"
                            placeholder="字段描述及业务含义"
                          />
                        </td>
                        <td className="text-center">
                          <input
                            type="checkbox"
                            checked={!!field.is_primary_key}
                            onChange={(e) => updateEditField(index, { is_primary_key: e.target.checked })}
                          />
                        </td>
                        <td className="text-center">
                          <input
                            type="checkbox"
                            checked={!!field.is_foreign_key}
                            onChange={(e) => updateEditField(index, { is_foreign_key: e.target.checked })}
                          />
                        </td>
                        <td className="text-right">
                          <button type="button" onClick={() => removeEditField(index)} className="btn-ghost text-xs text-red-500">
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={isRelModalOpen}
        onClose={() => {
          setIsRelModalOpen(false);
          setNewRel({ id: undefined, source: "", target: "", type: "", join_key: "", is_reviewed: false });
        }}
        title={newRel.id ? "编辑关系" : "新增关系"}
        footer={
          <>
            <button onClick={() => { setIsRelModalOpen(false); setNewRel({ id: undefined, source: "", target: "", type: "", join_key: "", is_reviewed: false }); }} className="btn-outline">取消</button>
            <button onClick={saveRelationship} className="btn-primary">保存</button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">源类</label>
              <select value={newRel.source} onChange={(e) => setNewRel({ ...newRel, source: e.target.value })} className="w-full">
                <option value="">选择...</option>
                {classes.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">目标类</label>
              <select value={newRel.target} onChange={(e) => setNewRel({ ...newRel, target: e.target.value })} className="w-full">
                <option value="">选择...</option>
                {classes.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">关系类型</label>
              <input value={newRel.type} onChange={(e) => setNewRel({ ...newRel, type: e.target.value })} className="w-full" placeholder="has_detail" />
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">JOIN字段</label>
              <input value={newRel.join_key} onChange={(e) => setNewRel({ ...newRel, join_key: e.target.value })} className="w-full" placeholder="sale_id" />
            </div>
          </div>
          <label className="inline-flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={newRel.is_reviewed} onChange={(e) => setNewRel({ ...newRel, is_reviewed: e.target.checked })} />
            人工审核通过
          </label>
        </div>
      </Modal>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={deleteTarget?.type === "class" ? "删除类" : "删除关系"}
        message={deleteTarget?.type === "class" ? "确定要删除此类吗？关联的关系也将被删除。" : "确定要删除此关系吗？"}
        onConfirm={() => {
          if (deleteTarget?.type === "class") deleteClass(deleteTarget.id as string);
          else if (deleteTarget?.type === "rel") deleteRelationship(deleteTarget.id as number);
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}