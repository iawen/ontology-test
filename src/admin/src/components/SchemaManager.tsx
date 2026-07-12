"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CheckCircle2,
  CircleDashed,
  Database,
  GitBranch,
  Network,
  Plus,
  RotateCcw,
  Table2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import SearchInput from "@/components/ui/SearchInput";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { SchemaClass, SchemaField, SchemaRelationship } from "@/lib/types";
import {
  normalizeReviewStatus,
  reviewStatusClassName,
  reviewStatusLabel,
  type ReviewStatus,
} from "@/lib/reviewStatus";

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer]);

const escapeTooltipHtml = (value: unknown) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const reviewTooltipColor = (value: unknown) => {
  const status = normalizeReviewStatus(value);
  if (status === 1) return "#86efac";
  if (status === -1) return "#fca5a5";
  return "#fde68a";
};

const tooltipText = (value: unknown, fallback = "未设置") =>
  escapeTooltipHtml(value ? value : fallback);

const genFieldUid = () => `f_${crypto.randomUUID()}`;

type SortDirection = "asc" | "desc";
type ClassSortKey = "id" | "name_cn" | "csv_file" | "is_reviewed" | "fields";
type RelationshipSortKey =
  | "source"
  | "target"
  | "type"
  | "source_key"
  | "target_key"
  | "is_reviewed";

const CLASS_SORT_COLUMNS: Array<{ key: ClassSortKey; label: string }> = [
  { key: "id", label: "ID" },
  { key: "name_cn", label: "中文名" },
  { key: "csv_file", label: "数据表" },
  { key: "is_reviewed", label: "审核" },
  { key: "fields", label: "字段" },
];

const RELATIONSHIP_SORT_COLUMNS: Array<{
  key: RelationshipSortKey;
  label: string;
}> = [
  { key: "source", label: "源" },
  { key: "target", label: "目标" },
  { key: "type", label: "类型" },
  { key: "source_key", label: "源字段" },
  { key: "target_key", label: "目标字段" },
  { key: "is_reviewed", label: "审核" },
];

function compareSortValues(
  leftValue: string | number,
  rightValue: string | number,
  direction: SortDirection,
) {
  const multiplier = direction === "asc" ? 1 : -1;
  if (typeof leftValue === "number" && typeof rightValue === "number") {
    return (leftValue - rightValue) * multiplier;
  }
  return (
    String(leftValue).localeCompare(String(rightValue), "zh-Hans-CN", {
      numeric: true,
    }) * multiplier
  );
}

function classSortValue(schemaClass: SchemaClass, key: ClassSortKey) {
  if (key === "is_reviewed")
    return normalizeReviewStatus(schemaClass.is_reviewed);
  if (key === "fields") return (schemaClass.fields || []).length;
  return String(schemaClass[key] || "");
}

function relationshipSortValue(
  relationship: SchemaRelationship,
  key: RelationshipSortKey,
) {
  if (key === "is_reviewed")
    return normalizeReviewStatus(relationship.is_reviewed);
  return String(relationship[key] || "");
}

const emptyField = (): SchemaField => ({
  _uid: genFieldUid(),
  name: "",
  physical_name: "",
  type: "text",
  description: "",
  is_primary_key: false,
  is_foreign_key: false,
});

const schemaFieldOptionValue = (field: SchemaField) =>
  field.physical_name || field.name || "";

const schemaFieldOptionLabel = (field: SchemaField) => {
  const name = field.name || "";
  const physicalName = field.physical_name || "";
  if (name && physicalName && name !== physicalName) {
    return `${name} (${physicalName})`;
  }
  return name || physicalName || "未命名字段";
};

export default function SchemaManager() {
  const { activeScenario, addToast } = useApp();
  const api = useApi();
  const [classes, setClasses] = useState<SchemaClass[]>([]);
  const [relationships, setRelationships] = useState<SchemaRelationship[]>([]);
  const [loading, setLoading] = useState(false);
  const [editClass, setEditClass] = useState<Partial<SchemaClass> | null>(null);
  const [originalClassId, setOriginalClassId] = useState<string | null>(null);
  const [isClassModalOpen, setIsClassModalOpen] = useState(false);
  const emptyRelationship = (): Partial<SchemaRelationship> => ({
    source: "",
    target: "",
    type: "",
    source_key: "",
    target_key: "",
    is_reviewed: 0,
  });
  const [editRel, setEditRel] =
    useState<Partial<SchemaRelationship>>(emptyRelationship());
  const [isRelModalOpen, setIsRelModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    type: "class" | "rel";
    ids: Array<string | number>;
  } | null>(null);
  const [selectedClassIds, setSelectedClassIds] = useState<Set<string>>(new Set());
  const [selectedRelationshipIds, setSelectedRelationshipIds] = useState<Set<number>>(new Set());
  const [viewMode, setViewMode] = useState<"graph" | "table">("graph");
  const [graphNodePositions, setGraphNodePositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [classSearch, setClassSearch] = useState("");
  const [relationshipSearch, setRelationshipSearch] = useState("");
  const [classSort, setClassSort] = useState<{
    key: ClassSortKey;
    direction: SortDirection;
  }>({ key: "id", direction: "asc" });
  const [relationshipSort, setRelationshipSort] = useState<{
    key: RelationshipSortKey;
    direction: SortDirection;
  }>({ key: "source", direction: "asc" });

  // 🛠️ 声明 ECharts 实例的 Ref
  const echartsRef = useRef<any>(null);
  const graphDraggingRef = useRef(false);

  const cacheKey = `schema:${activeScenario}`;

  const updateEditField = (index: number, patch: Partial<SchemaField>) => {
    const fields = [...(editClass?.fields || [])];
    fields[index] = { ...fields[index], ...patch } as SchemaField;
    const properties = fields
      .map((field) => field.name || field.physical_name)
      .filter(Boolean);
    setEditClass({ ...editClass!, fields, properties });
  };

  const addEditField = () => {
    setEditClass({
      ...editClass!,
      fields: [...(editClass?.fields || []), emptyField()],
    });
  };

  const removeEditField = (index: number) => {
    const fields = (editClass?.fields || []).filter((_, i) => i !== index);
    const properties = fields
      .map((field) => field.name || field.physical_name)
      .filter(Boolean);
    setEditClass({ ...editClass!, fields, properties });
  };

  const openClassEditor = (schemaClass: SchemaClass) => {
    setOriginalClassId(schemaClass.id);
    setEditClass({
      ...schemaClass,
      fields: (schemaClass.fields || []).map((field) =>
        field._uid ? field : { ...field, _uid: genFieldUid() },
      ),
    });
    setIsClassModalOpen(true);
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
        api(`/api/scenarios/${activeScenario}/schema/classes`),
        api(`/api/scenarios/${activeScenario}/schema/relationships`),
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

  useEffect(() => {
    setGraphNodePositions({});
  }, [activeScenario]);

  // 🛠️ 缩放与布局控制函数
  const handleZoomIn = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    const option = chartInstance.getOption();
    // 放大：增大 zoom 系数
    const currentZoom = option.series[0].zoom || 1;
    chartInstance.setOption({
      series: [{ zoom: currentZoom + 0.2 }],
    });
  };

  const handleZoomOut = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    if (!chartInstance) return;
    const option = chartInstance.getOption();
    // 缩小：减小 zoom 系数，最低不小于 0.2
    const currentZoom = option.series[0].zoom || 1;
    chartInstance.setOption({
      series: [{ zoom: Math.max(0.2, currentZoom - 0.2) }],
    });
  };

  const handleResetLayout = () => {
    const chartInstance = echartsRef.current?.getEchartsInstance();
    setGraphNodePositions({});
    if (!chartInstance) return;
    // 复位：重置 zoom 级别，并清空用户的拖拽位移偏移量、平移中心点
    chartInstance.setOption({
      series: [
        {
          zoom: 1,
          center: null,
        },
      ],
    });
    // 重新触发力引导布局使其平滑重排
    chartInstance.dispatchAction({
      type: "graphRoam",
      dx: 0,
      dy: 0,
    });
  };

  const persistDraggedNodePosition = useCallback((params: any) => {
    if (params.dataType !== "node" || !params.data?.name) return;

    const chartInstance = echartsRef.current?.getEchartsInstance();
    const nativeEvent = params.event?.event ?? params.event;
    const offsetX = nativeEvent?.offsetX ?? nativeEvent?.zrX;
    const offsetY = nativeEvent?.offsetY ?? nativeEvent?.zrY;
    if (
      !chartInstance ||
      !Number.isFinite(offsetX) ||
      !Number.isFinite(offsetY)
    ) {
      return;
    }

    const graphPoint = chartInstance.convertFromPixel({ seriesIndex: 0 }, [
      offsetX,
      offsetY,
    ]);
    if (
      !Array.isArray(graphPoint) ||
      !Number.isFinite(graphPoint[0]) ||
      !Number.isFinite(graphPoint[1])
    ) {
      return;
    }

    const [x, y] = graphPoint;
    const nodeName = String(params.data.name);
    setGraphNodePositions((positions) => ({
      ...positions,
      [nodeName]: { x, y },
    }));

    const option = chartInstance.getOption();
    const series = option?.series?.[0];
    const data = Array.isArray(series?.data) ? series.data : [];
    chartInstance.setOption(
      {
        series: [
          {
            id: "schema-graph",
            data: data.map((item: any) =>
              item.name === nodeName ? { ...item, fixed: true, x, y } : item,
            ),
          },
        ],
      },
      false,
    );
  }, []);

  const saveClass = async () => {
    if (!editClass?.id || !editClass.name_cn) {
      addToast("warning", "ID和中文名必填");
      return;
    }
    const isEdit = originalClassId !== null;
    const fields = (editClass.fields || [])
      .filter((field) => field.name || field.physical_name)
      .map((field) => {
        const rest = { ...field };
        delete rest._uid;
        return rest;
      });
    const payload = {
      ...editClass,
      fields,
      properties: editClass.properties?.length
        ? editClass.properties
        : fields
            .map((field) => field.name || field.physical_name)
            .filter(Boolean),
    };
    try {
      await api(
        `/api/scenarios/${activeScenario}/schema/classes${isEdit ? `/${originalClassId}` : ""}`,
        { method: isEdit ? "PUT" : "POST", body: JSON.stringify(payload) },
      );
      addToast("success", isEdit ? "类已更新，相关引用已同步" : "类已创建");
      setIsClassModalOpen(false);
      setEditClass(null);
      setOriginalClassId(null);
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const deleteClasses = async (ids: string[]) => {
    const results = await Promise.allSettled(ids.map((id) => api(`/api/scenarios/${activeScenario}/schema/classes/${id}`, { method: "DELETE" })));
    const succeeded = results.filter((result) => result.status === "fulfilled").length;
    if (succeeded) {
      const cascaded = results.reduce(
        (total, result) => {
          if (result.status !== "fulfilled") return total;
          const deleted = result.value?.deleted || {};
          return {
            relationships: total.relationships + Number(deleted.relationships || 0),
            metrics: total.metrics + Number(deleted.metrics || 0),
            concepts: total.concepts + Number(deleted.concepts || 0),
          };
        },
        { relationships: 0, metrics: 0, concepts: 0 },
      );
      addToast(
        "success",
        `已删除 ${succeeded} 个类，并清理关系 ${cascaded.relationships} 条、指标 ${cascaded.metrics} 个、概念 ${cascaded.concepts} 个`,
      );
      setSelectedClassIds(new Set());
      invalidateCache(cacheKey);
      load(true);
    }
    if (succeeded !== ids.length) addToast("error", `${ids.length - succeeded} 个类删除失败`);
  };

  const saveRelationship = async () => {
    if (!editRel.source || !editRel.target || !editRel.type) {
      addToast("warning", "源、目标和类型必填");
      return;
    }
    const isEdit = typeof editRel.id === "number";
    try {
      await api(
        `/api/scenarios/${activeScenario}/schema/relationships${isEdit ? `/${editRel.id}` : ""}`,
        {
          method: isEdit ? "PUT" : "POST",
          body: JSON.stringify({
            source: editRel.source,
            target: editRel.target,
            type: editRel.type,
            source_key: editRel.source_key || "",
            target_key: editRel.target_key || "",
            is_reviewed: normalizeReviewStatus(editRel.is_reviewed),
          }),
        },
      );
      addToast("success", isEdit ? "关系已更新" : "关系已创建");
      setIsRelModalOpen(false);
      setEditRel(emptyRelationship());
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const deleteRelationships = async (ids: number[]) => {
    const results = await Promise.allSettled(ids.map((id) => api(`/api/scenarios/${activeScenario}/schema/relationships/${id}`, { method: "DELETE" })));
    const succeeded = results.filter((result) => result.status === "fulfilled").length;
    if (succeeded) {
      addToast("success", `已删除 ${succeeded} 条关系`);
      setSelectedRelationshipIds(new Set());
      invalidateCache(cacheKey);
      load(true);
    }
    if (succeeded !== ids.length) addToast("error", `${ids.length - succeeded} 条关系删除失败`);
  };

  const classById = new Map(
    classes.map((schemaClass) => [schemaClass.id, schemaClass]),
  );
  const sourceFieldOptions = classById.get(editRel.source || "")?.fields || [];
  const targetFieldOptions = classById.get(editRel.target || "")?.fields || [];
  const relationshipCountByClass = relationships.reduce<Record<string, number>>(
    (counts, relationship) => {
      counts[relationship.source] = (counts[relationship.source] || 0) + 1;
      counts[relationship.target] = (counts[relationship.target] || 0) + 1;
      return counts;
    },
    {},
  );
  const reviewedClassCount = classes.filter(
    (schemaClass) => normalizeReviewStatus(schemaClass.is_reviewed) === 1,
  ).length;
  const reviewedRelationshipCount = relationships.filter(
    (relationship) => normalizeReviewStatus(relationship.is_reviewed) === 1,
  ).length;
  const sortedClasses = useMemo(() => {
    const keyword = classSearch.trim().toLowerCase();
    const filteredClasses = classes.filter((schemaClass) => {
      if (!keyword) return true;
      const fieldText = (schemaClass.fields || [])
        .map(
          (field) =>
            `${field.name} ${field.physical_name} ${field.description}`,
        )
        .join(" ");
      return [
        schemaClass.id,
        schemaClass.name_cn,
        schemaClass.description,
        schemaClass.csv_file,
        schemaClass.primary_key,
        reviewStatusLabel(schemaClass.is_reviewed),
        fieldText,
      ]
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
    return [...filteredClasses].sort((left, right) =>
      compareSortValues(
        classSortValue(left, classSort.key),
        classSortValue(right, classSort.key),
        classSort.direction,
      ),
    );
  }, [classSearch, classSort, classes]);
  const sortedRelationships = useMemo(() => {
    const keyword = relationshipSearch.trim().toLowerCase();
    const filteredRelationships = relationships.filter((relationship) => {
      if (!keyword) return true;
      return [
        relationship.source,
        relationship.target,
        relationship.type,
        relationship.source_key,
        relationship.target_key,
        reviewStatusLabel(relationship.is_reviewed),
      ]
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
    return [...filteredRelationships].sort((left, right) =>
      compareSortValues(
        relationshipSortValue(left, relationshipSort.key),
        relationshipSortValue(right, relationshipSort.key),
        relationshipSort.direction,
      ),
    );
  }, [relationshipSearch, relationshipSort, relationships]);

  const toggleClassSort = (key: ClassSortKey) => {
    setClassSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const toggleRelationshipSort = (key: RelationshipSortKey) => {
    setRelationshipSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const renderSortHeader = (
    key: ClassSortKey | RelationshipSortKey,
    label: string,
    active: boolean,
    direction: SortDirection,
    onClick: () => void,
  ) => {
    const Icon = active
      ? direction === "asc"
        ? ArrowUp
        : ArrowDown
      : ArrowUpDown;
    return (
      <th key={key}>
        <button
          type="button"
          onClick={onClick}
          className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-800"
        >
          <span>{label}</span>
          <Icon className="h-3.5 w-3.5" />
        </button>
      </th>
    );
  };
  const graphEvents = {
    dragstart: (params: any) => {
      if (params.dataType === "node") graphDraggingRef.current = true;
    },
    dragend: (params: any) => {
      persistDraggedNodePosition(params);
      window.setTimeout(() => {
        graphDraggingRef.current = false;
      }, 0);
    },
    click: (params: any) => {
      if (graphDraggingRef.current) return;
      if (params.dataType !== "node" || !params.data?.raw) return;
      openClassEditor(params.data.raw as SchemaClass);
    },
  };
  const graphOption = {
    backgroundColor: "transparent",
    color: ["#2563eb", "#f59e0b"],
    tooltip: {
      trigger: "item",
      confine: true,
      borderWidth: 0,
      backgroundColor: "rgba(15, 23, 42, 0.92)",
      textStyle: { color: "#f8fafc", fontSize: 12 },
      extraCssText:
        "border-radius: 10px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.18); padding: 0;",
      formatter: (params: any) => {
        if (params.dataType === "edge") {
          const relationship = params.data.raw as SchemaRelationship;
          const source = classById.get(relationship.source);
          const target = classById.get(relationship.target);
          const sourceName = tooltipText(
            source?.name_cn || relationship.source,
          );
          const targetName = tooltipText(
            target?.name_cn || relationship.target,
          );
          return `
            <div style="padding:10px 12px;min-width:220px">
              <div style="font-weight:700;margin-bottom:6px">${tooltipText(relationship.type)}</div>
              <div style="color:#cbd5e1;line-height:1.7">${sourceName} -> ${targetName}</div>
              <div style="color:#94a3b8;line-height:1.7">Source Key: ${tooltipText(relationship.source_key)}</div>
              <div style="color:#94a3b8;line-height:1.7">Target Key: ${tooltipText(relationship.target_key)}</div>
              <div style="color:${reviewTooltipColor(relationship.is_reviewed)};line-height:1.7">${reviewStatusLabel(relationship.is_reviewed)}</div>
            </div>
          `;
        }
        const schemaClass = params.data.raw as SchemaClass;
        const fieldCount =
          schemaClass.fields?.length || schemaClass.properties?.length || 0;
        const connectionCount = relationshipCountByClass[schemaClass.id] || 0;
        return `
          <div style="padding:10px 12px;min-width:240px">
            <div style="font-weight:700;margin-bottom:4px">${tooltipText(schemaClass.name_cn || schemaClass.id)}</div>
            <div style="color:#cbd5e1;margin-bottom:8px">${tooltipText(schemaClass.id)}</div>
            <div style="color:#e2e8f0;line-height:1.7">字段: ${fieldCount} · 关系: ${connectionCount}</div>
            <div style="color:#94a3b8;line-height:1.7">主键: ${tooltipText(schemaClass.primary_key)}</div>
            <div style="color:#94a3b8;line-height:1.7">文件: ${tooltipText(schemaClass.csv_file, "未绑定")}</div>
            <div style="color:${reviewTooltipColor(schemaClass.is_reviewed)};line-height:1.7">${reviewStatusLabel(schemaClass.is_reviewed)}</div>
          </div>
        `;
      },
    },
    legend: {
      top: 16,
      left: 18,
      itemWidth: 10,
      itemHeight: 10,
      icon: "circle",
      textStyle: { color: "#64748b", fontSize: 12 },
      data: ["已通过", "待审核", "不通过"],
    },
    series: [
      {
        id: "schema-graph",
        name: "Schema 图谱",
        type: "graph",
        layout: "force",
        roam: true,
        draggable: true,
        top: 40,
        bottom: 28,
        left: 16,
        right: 16,
        categories: [
          { name: "已通过" },
          { name: "待审核" },
          { name: "不通过" },
        ],
        label: {
          show: true,
          position: "inside",
          align: "center",
          verticalAlign: "middle",
          color: "#334155",
          fontSize: 12,
          fontWeight: 600,
          overflow: "truncate",
          formatter: (params: any) => params.data.label,
        },
        edgeLabel: {
          show: true,
          color: "#64748b",
          fontSize: 11,
          backgroundColor: "rgba(255,255,255,0.86)",
          borderColor: "#e2e8f0",
          borderWidth: 1,
          borderRadius: 4,
          padding: [2, 5],
          formatter: (params: any) => params.data.label,
        },
        emphasis: {
          focus: "adjacency",
          scale: true,
          lineStyle: { width: 3 },
        },
        data: classes.map((c) => {
          const savedPosition = graphNodePositions[c.id];
          return {
            name: c.id,
            label: c.name_cn || c.id,
            raw: c,
            category:
              normalizeReviewStatus(c.is_reviewed) === 1
                ? 0
                : normalizeReviewStatus(c.is_reviewed) === -1
                  ? 2
                  : 1,
            draggable: true,
            fixed: !!savedPosition,
            x: savedPosition?.x,
            y: savedPosition?.y,
            symbol: "roundRect",
            symbolSize: [
              Math.min(132, 64 + (c.name_cn?.length || c.id.length) * 4),
              Math.min(
                46,
                30 +
                  Math.min(c.fields?.length || c.properties?.length || 0, 8) *
                    1.4,
              ),
            ],
            itemStyle: {
              color:
                normalizeReviewStatus(c.is_reviewed) === -1
                  ? "#fef2f2"
                  : normalizeReviewStatus(c.is_reviewed) === 1
                    ? "#eff6ff"
                    : "#fffbeb",
              borderColor:
                normalizeReviewStatus(c.is_reviewed) === -1
                  ? "#ef4444"
                  : normalizeReviewStatus(c.is_reviewed) === 1
                    ? "#2563eb"
                    : "#f59e0b",
              borderWidth: 2,
              shadowBlur: 14,
              shadowColor:
                normalizeReviewStatus(c.is_reviewed) === 1
                  ? "rgba(37, 99, 235, 0.18)"
                  : "rgba(245, 158, 11, 0.18)",
            },
          };
        }),
        links: relationships.map((r) => ({
          source: r.source,
          target: r.target,
          raw: r,
          label: r.type,
          lineStyle: {
            color:
              normalizeReviewStatus(r.is_reviewed) === -1
                ? "#ef4444"
                : normalizeReviewStatus(r.is_reviewed) === 1
                  ? "#94a3b8"
                  : "#f59e0b",
            width: normalizeReviewStatus(r.is_reviewed) === 1 ? 1.6 : 2,
            opacity: 0.78,
            curveness: 0.16,
          },
          emphasis: {
            lineStyle: {
              color: "#2563eb",
              width: 3,
              opacity: 0.95,
            },
          },
        })),
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: [0, 9],
        force: {
          repulsion: 520,
          gravity: 0.08,
          edgeLength: [120, 210],
          friction: 0.58,
          layoutAnimation: true,
        },
        animation: false,
        animationDuration: 700,
        animationEasing: "cubicOut",
      },
    ],
  };

  if (!activeScenario)
    return (
      <div>
        <h2 className="mb-4 text-lg font-semibold text-slate-800">
          Schema 管理
        </h2>
        <ScenarioSelector />
      </div>
    );

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Schema 管理</h2>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded-lg border border-slate-200">
            <button
              onClick={() => setViewMode("graph")}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs ${viewMode === "graph" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}
            >
              <Network className="h-3.5 w-3.5" />
              图谱
            </button>
            <button
              onClick={() => setViewMode("table")}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs ${viewMode === "table" ? "bg-indigo-50 text-indigo-600" : "text-slate-500"}`}
            >
              <Table2 className="h-3.5 w-3.5" />
              表格
            </button>
          </div>
          <button
            onClick={() => {
              setOriginalClassId(null);
              setEditClass({
                scenario_id: activeScenario,
                properties: [],
                fields: [],
                csv_file: "",
                primary_key: "",
                is_reviewed: 0,
              });
              setIsClassModalOpen(true);
            }}
            className="btn-primary flex items-center gap-1.5"
          >
            <Plus className="h-4 w-4" />
            新增类
          </button>
          <button
            onClick={() => {
              setEditRel(emptyRelationship());
              setIsRelModalOpen(true);
            }}
            className="btn-outline flex items-center gap-1.5"
          >
            <Plus className="h-4 w-4" />
            新增关系
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
        <div className="card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">
                Schema 关系图谱
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                节点大小随字段数变化，悬停查看详情，点击节点编辑属性
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs sm:flex">
              <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600">
                <Database className="h-4 w-4 text-blue-600" />
                <span className="font-semibold text-slate-800">
                  {classes.length}
                </span>
                类
              </div>
              <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600">
                <GitBranch className="h-4 w-4 text-amber-600" />
                <span className="font-semibold text-slate-800">
                  {relationships.length}
                </span>
                关系
              </div>
              <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600">
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                <span className="font-semibold text-slate-800">
                  {reviewedClassCount + reviewedRelationshipCount}
                </span>
                已审核
              </div>
              <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-600">
                <CircleDashed className="h-4 w-4 text-amber-500" />
                <span className="font-semibold text-slate-800">
                  {classes.length +
                    relationships.length -
                    reviewedClassCount -
                    reviewedRelationshipCount}
                </span>
                待审核
              </div>
            </div>
          </div>

          <div className="relative h-[560px] bg-[radial-gradient(circle_at_20px_20px,#e2e8f0_1px,transparent_1px)] bg-[length:28px_28px]">
            <div className="absolute right-4 top-4 z-10 flex flex-col gap-1.5 rounded-lg border border-slate-200 bg-white/95 p-1.5 shadow-sm backdrop-blur">
              <button
                onClick={handleZoomIn}
                title="放大"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <ZoomIn className="h-4 w-4" />
              </button>
              <button
                onClick={handleZoomOut}
                title="缩小"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <ZoomOut className="h-4 w-4" />
              </button>
              <div className="mx-1 my-0.5 h-[1px] bg-slate-100" />
              <button
                onClick={handleResetLayout}
                title="自动布局复位"
                className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-slate-100 active:bg-slate-200"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>

            <ReactEChartsCore
              ref={echartsRef}
              echarts={echarts}
              option={graphOption}
              onEvents={graphEvents}
              style={{ height: "100%", width: "100%" }}
            />
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="card overflow-hidden">
            <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="text-sm font-semibold text-slate-700">
                类 ({sortedClasses.length}/{classes.length})
              </h3>
              <div className="flex w-full items-center gap-3 sm:w-auto">
                {selectedClassIds.size > 0 && <button onClick={() => setDeleteTarget({ type: "class", ids: [...selectedClassIds] })} className="btn-ghost whitespace-nowrap text-xs text-red-500">删除已选 ({selectedClassIds.size})</button>}
                <div className="w-full sm:w-72">
                <SearchInput
                  value={classSearch}
                  onChange={setClassSearch}
                  placeholder="搜索类、字段..."
                />
                </div>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table min-w-[980px] table-fixed">
                <colgroup>
                  <col className="w-10" />
                  <col className="w-28" />
                  <col className="w-20" />
                  <col className="w-40" />
                  <col className="w-10" />
                  <col className="w-10" />
                  <col className="w-10" />
                </colgroup>
                <thead>
                  <tr>
                    <th className="w-10 text-center"><input aria-label="全选类" type="checkbox" checked={sortedClasses.length > 0 && sortedClasses.every((item) => selectedClassIds.has(item.id))} onChange={(event) => setSelectedClassIds((current) => { const next = new Set(current); sortedClasses.forEach((item) => event.target.checked ? next.add(item.id) : next.delete(item.id)); return next; })} /></th>
                    {CLASS_SORT_COLUMNS.map((column) =>
                      renderSortHeader(
                        column.key,
                        column.label,
                        classSort.key === column.key,
                        classSort.direction,
                        () => toggleClassSort(column.key),
                      ),
                    )}
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedClasses.length === 0 && (
                    <tr>
                      <td colSpan={7} className="text-center text-slate-400">
                        没有匹配的类
                      </td>
                    </tr>
                  )}
                  {sortedClasses.map((c) => (
                    <tr key={c.id}>
                      <td className="text-center"><input aria-label={`选择类 ${c.id}`} type="checkbox" checked={selectedClassIds.has(c.id)} onChange={() => setSelectedClassIds((current) => { const next = new Set(current); next.has(c.id) ? next.delete(c.id) : next.add(c.id); return next; })} /></td>
                      <td
                        className="whitespace-normal break-words font-mono text-xs leading-relaxed"
                        title={c.id}
                      >
                        {c.id}
                      </td>
                      <td
                        className="whitespace-normal break-words font-medium leading-relaxed"
                        title={
                          c.description
                            ? `${c.name_cn}\n\n${c.description}`
                            : c.name_cn
                        }
                      >
                        {c.name_cn}
                      </td>
                      <td
                        className="whitespace-normal break-words text-xs leading-relaxed"
                        title={c.csv_file}
                      >
                        {c.csv_file}
                      </td>
                      <td>
                        <span
                          className={`rounded px-1.5 py-0.5 text-xs ${reviewStatusClassName(c.is_reviewed)}`}
                        >
                          {reviewStatusLabel(c.is_reviewed)}
                        </span>
                      </td>
                      <td className="text-xs text-slate-500">
                        {(c.fields || []).length}
                      </td>
                      <td className="whitespace-nowrap text-right">
                        <button
                          onClick={() => openClassEditor(c)}
                          className="btn-ghost text-xs"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() =>
                            setDeleteTarget({ type: "class", ids: [c.id] })
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
            <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="text-sm font-semibold text-slate-700">
                关系 ({sortedRelationships.length}/{relationships.length})
              </h3>
              <div className="flex w-full items-center gap-3 sm:w-auto">
                {selectedRelationshipIds.size > 0 && <button onClick={() => setDeleteTarget({ type: "rel", ids: [...selectedRelationshipIds] })} className="btn-ghost whitespace-nowrap text-xs text-red-500">删除已选 ({selectedRelationshipIds.size})</button>}
                <div className="w-full sm:w-72">
                <SearchInput
                  value={relationshipSearch}
                  onChange={setRelationshipSearch}
                  placeholder="搜索关系..."
                />
                </div>
              </div>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="w-10 text-center"><input aria-label="全选关系" type="checkbox" checked={sortedRelationships.length > 0 && sortedRelationships.every((item) => selectedRelationshipIds.has(item.id))} onChange={(event) => setSelectedRelationshipIds((current) => { const next = new Set(current); sortedRelationships.forEach((item) => event.target.checked ? next.add(item.id) : next.delete(item.id)); return next; })} /></th>
                  {RELATIONSHIP_SORT_COLUMNS.map((column) =>
                    renderSortHeader(
                      column.key,
                      column.label,
                      relationshipSort.key === column.key,
                      relationshipSort.direction,
                      () => toggleRelationshipSort(column.key),
                    ),
                  )}
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {sortedRelationships.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center text-slate-400">
                      没有匹配的关系
                    </td>
                  </tr>
                )}
                {sortedRelationships.map((r) => (
                  <tr key={r.id}>
                    <td className="text-center"><input aria-label={`选择关系 ${r.id}`} type="checkbox" checked={selectedRelationshipIds.has(r.id)} onChange={() => setSelectedRelationshipIds((current) => { const next = new Set(current); next.has(r.id) ? next.delete(r.id) : next.add(r.id); return next; })} /></td>
                    <td className="whitespace-normal break-words font-mono text-xs leading-relaxed">
                      {r.source}
                    </td>
                    <td className="whitespace-normal break-words font-mono text-xs leading-relaxed">
                      {r.target}
                    </td>
                    <td className="whitespace-normal break-words leading-relaxed">
                      {r.type}
                    </td>
                    <td className="whitespace-normal break-words text-xs leading-relaxed">
                      {r.source_key}
                    </td>
                    <td className="whitespace-normal break-words text-xs leading-relaxed">
                      {r.target_key}
                    </td>
                    <td>
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${reviewStatusClassName(r.is_reviewed)}`}
                      >
                        {reviewStatusLabel(r.is_reviewed)}
                      </span>
                    </td>
                    <td className="whitespace-nowrap text-right">
                      <button
                        onClick={() => {
                          setEditRel({
                            ...r,
                            source_key: r.source_key || "",
                            target_key: r.target_key || "",
                          });
                          setIsRelModalOpen(true);
                        }}
                        className="btn-ghost text-xs"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() =>
                          setDeleteTarget({ type: "rel", ids: [r.id] })
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
          setOriginalClassId(null);
        }}
        title={
          originalClassId ? "编辑类" : "新增类"
        }
        width="max-w-6xl"
        footer={
          <>
            <button
              onClick={() => {
                setIsClassModalOpen(false);
                setEditClass(null);
                setOriginalClassId(null);
              }}
              className="btn-outline"
            >
              取消
            </button>
            <button onClick={saveClass} className="btn-primary">
              保存
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              类 ID
            </label>
            <input
              value={editClass?.id || ""}
              onChange={(e) =>
                setEditClass({ ...editClass!, id: e.target.value })
              }
              className="w-full"
              placeholder="Sale"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              中文名
            </label>
            <input
              value={editClass?.name_cn || ""}
              onChange={(e) =>
                setEditClass({ ...editClass!, name_cn: e.target.value })
              }
              className="w-full"
              placeholder="销售记录"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              描述
            </label>
            <textarea
              value={editClass?.description || ""}
              onChange={(e) =>
                setEditClass({ ...editClass!, description: e.target.value })
              }
              className="w-full"
              rows={2}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                数据文件
              </label>
              <input
                value={editClass?.csv_file || ""}
                onChange={(e) =>
                  setEditClass({ ...editClass!, csv_file: e.target.value })
                }
                className="w-full"
                placeholder="sale.csv"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                主键
              </label>
              <input
                value={editClass?.primary_key || ""}
                onChange={(e) =>
                  setEditClass({ ...editClass!, primary_key: e.target.value })
                }
                className="w-full"
                placeholder="sale_id"
              />
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              人工审核
            </label>
            <select
              value={normalizeReviewStatus(editClass?.is_reviewed)}
              onChange={(e) =>
                setEditClass({
                  ...editClass!,
                  is_reviewed: Number(e.target.value) as ReviewStatus,
                })
              }
              className="w-full text-sm"
            >
              <option value={0}>待审核</option>
              <option value={1}>已通过</option>
              <option value={-1}>不通过</option>
            </select>
          </div>
          {/* <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">属性 (逗号分隔)</label>
            <input value={(editClass?.properties || []).join(", ")} onChange={(e) => setEditClass({ ...editClass!, properties: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} className="w-full" />
          </div> */}
          <div className="overflow-hidden rounded-lg border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-slate-700">
                  字段明细
                </div>
                <div className="mt-0.5 text-xs text-slate-500">
                  维护 schema_classes.fields 中的列信息
                </div>
              </div>
              <button
                type="button"
                onClick={addEditField}
                className="btn-outline text-xs"
              >
                + 添加字段
              </button>
            </div>
            {(editClass?.fields || []).length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-slate-400">
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
                      <tr key={field._uid ?? index}>
                        <td>
                          <input
                            value={field.name || ""}
                            onChange={(e) =>
                              updateEditField(index, { name: e.target.value })
                            }
                            className="w-full text-xs"
                            placeholder="销售金额"
                          />
                        </td>
                        <td>
                          <input
                            value={field.physical_name || ""}
                            onChange={(e) =>
                              updateEditField(index, {
                                physical_name: e.target.value,
                              })
                            }
                            className="w-full font-mono text-xs"
                            placeholder="sales_amount"
                          />
                        </td>
                        <td>
                          <select
                            value={field.type || "text"}
                            onChange={(e) =>
                              updateEditField(index, {
                                type: e.target.value as SchemaField["type"],
                              })
                            }
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
                            onChange={(e) =>
                              updateEditField(index, {
                                description: e.target.value,
                              })
                            }
                            className="w-full text-xs"
                            placeholder="字段描述及业务含义"
                          />
                        </td>
                        <td className="text-center">
                          <input
                            type="checkbox"
                            checked={!!field.is_primary_key}
                            onChange={(e) =>
                              updateEditField(index, {
                                is_primary_key: e.target.checked,
                              })
                            }
                          />
                        </td>
                        <td className="text-center">
                          <input
                            type="checkbox"
                            checked={!!field.is_foreign_key}
                            onChange={(e) =>
                              updateEditField(index, {
                                is_foreign_key: e.target.checked,
                              })
                            }
                          />
                        </td>
                        <td className="text-right">
                          <button
                            type="button"
                            onClick={() => removeEditField(index)}
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
            )}
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={isRelModalOpen}
        onClose={() => {
          setIsRelModalOpen(false);
          setEditRel(emptyRelationship());
        }}
        title={typeof editRel.id === "number" ? "编辑关系" : "新增关系"}
        footer={
          <>
            <button
              onClick={() => {
                setIsRelModalOpen(false);
                setEditRel(emptyRelationship());
              }}
              className="btn-outline"
            >
              取消
            </button>
            <button onClick={saveRelationship} className="btn-primary">
              保存
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                源类
              </label>
              <select
                value={editRel.source || ""}
                onChange={(e) =>
                  setEditRel({
                    ...editRel,
                    source: e.target.value,
                    source_key: "",
                  })
                }
                className="w-full"
              >
                <option value="">选择...</option>
                {classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                目标类
              </label>
              <select
                value={editRel.target || ""}
                onChange={(e) =>
                  setEditRel({
                    ...editRel,
                    target: e.target.value,
                    target_key: "",
                  })
                }
                className="w-full"
              >
                <option value="">选择...</option>
                {classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              关系类型
            </label>
            <input
              value={editRel.type || ""}
              onChange={(e) => setEditRel({ ...editRel, type: e.target.value })}
              className="w-full"
              placeholder="has_detail"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                源 JOIN 字段
              </label>
              <select
                value={editRel.source_key || ""}
                onChange={(e) =>
                  setEditRel({ ...editRel, source_key: e.target.value })
                }
                className="w-full"
                disabled={!editRel.source}
              >
                <option value="">选择...</option>
                {editRel.source_key &&
                  !sourceFieldOptions.some(
                    (field) =>
                      schemaFieldOptionValue(field) === editRel.source_key,
                  ) && (
                    <option value={editRel.source_key}>
                      {editRel.source_key}（未匹配到已有字段）
                    </option>
                  )}
                {sourceFieldOptions.map((field, index) => {
                  const value = schemaFieldOptionValue(field);
                  if (!value) return null;
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {schemaFieldOptionLabel(field)}
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-500">
                目标 JOIN 字段
              </label>
              <select
                value={editRel.target_key || ""}
                onChange={(e) =>
                  setEditRel({ ...editRel, target_key: e.target.value })
                }
                className="w-full"
                disabled={!editRel.target}
              >
                <option value="">选择...</option>
                {editRel.target_key &&
                  !targetFieldOptions.some(
                    (field) =>
                      schemaFieldOptionValue(field) === editRel.target_key,
                  ) && (
                    <option value={editRel.target_key}>
                      {editRel.target_key}（未匹配到已有字段）
                    </option>
                  )}
                {targetFieldOptions.map((field, index) => {
                  const value = schemaFieldOptionValue(field);
                  if (!value) return null;
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {schemaFieldOptionLabel(field)}
                    </option>
                  );
                })}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              人工审核
            </label>
            <select
              value={normalizeReviewStatus(editRel.is_reviewed)}
              onChange={(e) =>
                setEditRel({
                  ...editRel,
                  is_reviewed: Number(e.target.value) as ReviewStatus,
                })
              }
              className="w-full text-sm"
            >
              <option value={0}>待审核</option>
              <option value={1}>已通过</option>
              <option value={-1}>不通过</option>
            </select>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title={deleteTarget?.type === "class" ? "删除类" : "删除关系"}
        message={
          deleteTarget?.type === "class"
            ? `确定要删除选中的 ${deleteTarget.ids.length} 个类吗？所有关联的 Relationship、Metric 和 Concept 都将一并删除。`
            : `确定要删除选中的 ${deleteTarget?.ids.length || 0} 条关系吗？`
        }
        onConfirm={() => {
          if (deleteTarget?.type === "class")
            deleteClasses(deleteTarget.ids.map(String));
          else if (deleteTarget?.type === "rel")
            deleteRelationships(deleteTarget.ids.map(Number));
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
