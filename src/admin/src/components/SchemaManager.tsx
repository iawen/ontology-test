"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
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

const REVIEW_COLORS = {
  approved: { fill: "#ecfdf5", stroke: "#10b981", text: "#047857" },
  pending: { fill: "#fffbeb", stroke: "#f59e0b", text: "#b45309" },
};

const escapeHtml = (value: unknown) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

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

  const handleFitGraph = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    chartInstance.setOption({
      series: [{ zoom: 0.82, center: ["50%", "52%"] }],
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

  const classById = useMemo(
    () => new Map(classes.map((schemaClass) => [schemaClass.id, schemaClass])),
    [classes],
  );

  const reviewedClassCount = classes.filter((schemaClass) => schemaClass.is_reviewed).length;
  const reviewedRelationshipCount = relationships.filter((relationship) => relationship.is_reviewed).length;
  const isolatedClassCount = classes.filter(
    (schemaClass) => !relationships.some((relationship) => relationship.source === schemaClass.id || relationship.target === schemaClass.id),
  ).length;

  const graphOption = useMemo(() => ({
    backgroundColor: "transparent",
    animationDurationUpdate: 450,
    tooltip: {
      trigger: "item",
      appendToBody: true,
      backgroundColor: "rgba(15, 23, 42, 0.94)",
      borderWidth: 0,
      padding: [10, 12],
      textStyle: { color: "#f8fafc", fontSize: 12 },
      formatter: (params: any) => {
        if (params.dataType === "edge") {
          const relationship = params.data.raw as SchemaRelationship | undefined;
          if (!relationship) return "";
          return `<div style="min-width:180px">
            <div style="font-weight:600;margin-bottom:6px">${escapeHtml(relationship.type || "关系")}</div>
            <div>源: ${escapeHtml(relationship.source)}</div>
            <div>目标: ${escapeHtml(relationship.target)}</div>
            <div>JOIN: ${escapeHtml(relationship.join_key || "-")}</div>
            <div>审核: ${relationship.is_reviewed ? "通过" : "待审"}</div>
          </div>`;
        }
        const schemaClass = params.data.raw as SchemaClass | undefined;
        if (!schemaClass) return "";
        return `<div style="min-width:220px">
          <div style="font-weight:700;margin-bottom:6px">${escapeHtml(schemaClass.name_cn || schemaClass.id)}</div>
          <div style="color:#cbd5e1;margin-bottom:6px">${escapeHtml(schemaClass.id)}</div>
          <div>字段: ${(schemaClass.fields || []).length}</div>
          <div>数据文件: ${escapeHtml(schemaClass.csv_file || "-")}</div>
          <div>主键: ${escapeHtml(schemaClass.primary_key || "-")}</div>
          <div>审核: ${schemaClass.is_reviewed ? "通过" : "待审"}</div>
        </div>`;
      },
    },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        draggable: true,
        cursor: "pointer",
        scaleLimit: { min: 0.25, max: 3 },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: [0, 10],
        label: {
          show: true,
          formatter: (params: any) => params.data.label,
          color: "#0f172a",
          fontSize: 12,
          fontWeight: 600,
          width: 120,
          overflow: "truncate",
        },
        edgeLabel: {
          show: true,
          formatter: (params: any) => params.data.label,
          color: "#64748b",
          fontSize: 10,
          backgroundColor: "rgba(255,255,255,0.86)",
          borderColor: "#e2e8f0",
          borderWidth: 1,
          borderRadius: 4,
          padding: [2, 5],
        },
        emphasis: {
          focus: "adjacency",
          lineStyle: { width: 2.5, color: "#4f46e5" },
          label: { color: "#111827" },
        },
        data: classes.map((schemaClass) => {
          const fieldCount = (schemaClass.fields || []).length;
          const palette = schemaClass.is_reviewed ? REVIEW_COLORS.approved : REVIEW_COLORS.pending;
          return {
            id: schemaClass.id,
            name: schemaClass.id,
            label: schemaClass.name_cn || schemaClass.id,
            symbol: "roundRect",
            symbolSize: [Math.min(170, Math.max(106, (schemaClass.name_cn || schemaClass.id).length * 13)), 46 + Math.min(fieldCount, 18)],
            raw: schemaClass,
            itemStyle: {
              color: palette.fill,
              borderColor: palette.stroke,
              borderWidth: 2,
              shadowBlur: 12,
              shadowColor: "rgba(15, 23, 42, 0.08)",
            },
          };
        }),
        links: relationships
          .filter((relationship) => classById.has(relationship.source) && classById.has(relationship.target))
          .map((relationship) => ({
            source: relationship.source,
            target: relationship.target,
            label: relationship.type || "关联",
            raw: relationship,
            lineStyle: {
              color: relationship.is_reviewed ? "#94a3b8" : "#f59e0b",
              width: relationship.is_reviewed ? 1.4 : 1.8,
              curveness: 0.16,
              opacity: 0.88,
            },
          })),
        force: {
          repulsion: Math.max(420, classes.length * 34),
          gravity: 0.055,
          edgeLength: [150, 260],
          friction: 0.58,
        },
      },
    ],
  }), [classById, classes, relationships]);

  const graphEvents = useMemo(() => ({
    click: (params: any) => {
      if (params.dataType === "node" && params.data?.raw) {
        const schemaClass = params.data.raw as SchemaClass;
        setEditClass({ ...schemaClass, fields: schemaClass.fields || [] });
        setIsClassModalOpen(true);
      }
    },
  }), []);

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
        <div className="card overflow-hidden border border-slate-200 bg-white">
          <div className="flex flex-col gap-3 border-b border-slate-100 bg-slate-50/80 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">Schema 关系图谱</h3>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span>类 {classes.length}</span>
                <span>关系 {relationships.length}</span>
                <span>已审核类 {reviewedClassCount}</span>
                <span>已审核关系 {reviewedRelationshipCount}</span>
                <span>孤立类 {isolatedClassCount}</span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-100 bg-emerald-50 px-2 py-1 text-emerald-700">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />通过
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-100 bg-amber-50 px-2 py-1 text-amber-700">
                <span className="h-2 w-2 rounded-full bg-amber-500" />待审
              </span>
            </div>
          </div>
          <div className="relative h-[620px] bg-[radial-gradient(circle_at_1px_1px,#e2e8f0_1px,transparent_0)] [background-size:22px_22px]">
            <div className="absolute right-4 top-4 z-10 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/95 p-1.5 shadow-sm backdrop-blur">
              <button
                onClick={handleZoomIn}
                title="放大"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <ZoomIn size={17} />
              </button>
              <button
                onClick={handleZoomOut}
                title="缩小"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <ZoomOut size={17} />
              </button>
              <button
                onClick={handleFitGraph}
                title="适配视图"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <Maximize2 size={16} />
              </button>
              <button
                onClick={handleResetLayout}
                title="自动布局复位"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <RotateCcw size={16} />
              </button>
            </div>
            <ReactEChartsCore
              ref={echartsRef}
              echarts={echarts}
              option={graphOption}
              onEvents={graphEvents}
              notMerge
              style={{ height: "100%", width: "100%" }}
            />
          </div>
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