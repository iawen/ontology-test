"use client";
import { useState, useEffect, useMemo } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Plus, X } from "lucide-react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type {
  Metric,
  AnyMetricDefinition,
  MetricDefinition,
  MetricInput,
  MetricOutput,
  DimensionGroup,
  SchemaClass,
} from "@/lib/types";
import {
  normalizeReviewStatus,
  reviewStatusClassName,
  reviewStatusLabel,
  type ReviewStatus,
} from "@/lib/reviewStatus";

const CHART_LABELS: Record<string, string> = {
  bar: "柱状图",
  line: "折线图",
  pie: "饼图",
  scatter: "散点图",
  table: "表格",
  heatmap: "热力图",
  funnel: "漏斗图",
  radar: "雷达图",
};

type SortKey =
  | "name"
  | "category"
  | "target_class"
  | "chart_type"
  | "is_reviewed";
type SortDirection = "asc" | "desc";

const SORTABLE_COLUMNS: Array<{ key: SortKey; label: string }> = [
  { key: "name", label: "名称" },
  { key: "category", label: "分类" },
  { key: "target_class", label: "目标类" },
  { key: "chart_type", label: "图表" },
  { key: "is_reviewed", label: "审核" },
];

const REVIEW_STATUS_OPTIONS: Array<{ value: ReviewStatus; label: string }> = [
  { value: 0, label: "待审核" },
  { value: 1, label: "已通过" },
  { value: -1, label: "不通过" },
];

function metricIdFromName(name: string) {
  return (
    name
      .trim()
      .replace(/[^0-9A-Za-z_\-\u4e00-\u9fff]+/g, "_")
      .replace(/^_+|_+$/g, "") || "metric"
  );
}

function parseFilterValues(value: string) {
  return value
    .split(/[,，、;；\r\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function emptyMetricInput(index: number): MetricInput {
  return {
    id: `input_${Date.now()}_${index}`,
    class_id: "",
    source_shape: "wide",
    field: "",
    aggregation: "SUM",
    filters: [],
  };
}

function emptyMetricDefinition(): MetricDefinition {
  return {
    version: 1,
    anchor_class: "",
    expression_operator: "ADD",
    inputs: [emptyMetricInput(0)],
  };
}

function emptyMetricOutput(index: number): MetricOutput {
  return {
    id: `output_${Date.now()}_${index}`,
    output_name: "",
    expression_operator: "DIVIDE",
    inputs: [emptyMetricInput(0), emptyMetricInput(1)],
  };
}

function emptyParallelMetricDefinition(anchorClass = ""): AnyMetricDefinition {
  return {
    version: 2,
    anchor_class: anchorClass,
    outputs: [emptyMetricOutput(0)],
  };
}

function normalizeMetric(metric: Metric): Metric {
  return {
    ...metric,
    target_class: metric.target_class || metric.definition?.anchor_class || "",
    dimensions: Array.isArray(metric.dimensions) ? metric.dimensions : [],
    required_dimensions: Array.isArray(metric.required_dimensions)
      ? metric.required_dimensions
      : [],
    dimension_group_ids: Array.isArray(metric.dimension_group_ids)
      ? metric.dimension_group_ids
      : [],
    definition: metric.definition || undefined,
  };
}

function metricSortValue(metric: Metric, key: SortKey) {
  if (key === "is_reviewed") return normalizeReviewStatus(metric.is_reviewed);
  if (key === "chart_type")
    return CHART_LABELS[metric.chart_type] || metric.chart_type || "";
  if (key === "target_class") return metric.target_class || "";
  return String(metric[key] || "");
}

function metricTargetClasses(metric: Partial<Metric> | null | undefined) {
  const targetClass = metric?.definition?.anchor_class || metric?.target_class;
  return targetClass ? [targetClass] : [];
}

function schemaClassLabel(schemaClass: SchemaClass) {
  return schemaClass.name_cn
    ? `${schemaClass.name_cn} (${schemaClass.id})`
    : schemaClass.id;
}

export default function MetricManager() {
  const { activeScenario, addToast, token } = useApp();
  const api = useApi(token);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [schemaClasses, setSchemaClasses] = useState<SchemaClass[]>([]);
  const [dimensionGroups, setDimensionGroups] = useState<DimensionGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [filterTargetClass, setFilterTargetClass] = useState("");
  const [filterReviewStatus, setFilterReviewStatus] = useState("");
  const [editMetric, setEditMetric] = useState<Partial<Metric> | null>(null);
  const [targetClassSearch, setTargetClassSearch] = useState("");
  const [metricFilterValueOptions, setMetricFilterValueOptions] = useState<
    Record<string, string[]>
  >({});
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [selectedMetricIds, setSelectedMetricIds] = useState<string[]>([]);
  const [isBatchDeleteOpen, setIsBatchDeleteOpen] = useState(false);
  const [sort, setSort] = useState<{
    key: SortKey;
    direction: SortDirection;
  }>({ key: "name", direction: "asc" });

  const cacheKey = `metrics:${activeScenario}`;

  const loadSchemaClasses = async () => {
    if (!activeScenario) return;
    const schemaCache = getCacheData<{ classes: SchemaClass[] }>(
      `schema:${activeScenario}`,
    );
    if (schemaCache?.classes) {
      setSchemaClasses(schemaCache.classes);
      return;
    }

    try {
      const data = await api(`/api/scenarios/${activeScenario}/schema/classes`);
      setSchemaClasses(data || []);
    } catch {
      addToast("error", "加载目标类失败");
    }
  };

  const loadDimensionGroups = async () => {
    if (!activeScenario) return;
    try {
      const data = await api(
        `/api/admin/scenarios/${activeScenario}/dimension-groups`,
      );
      setDimensionGroups(data || []);
    } catch {
      addToast("error", "加载分析维度组失败");
    }
  };

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) {
      const cached = getCacheData<Metric[]>(cacheKey);
      if (cached) {
        setMetrics(cached);
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    try {
      const d = await api(`/api/scenarios/${activeScenario}/metrics`);
      const data = (d || []).map(normalizeMetric);
      setMetrics(data);
      setCacheData(cacheKey, data);
    } catch {
      addToast("error", "加载指标失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeScenario) {
      setSelectedMetricIds([]);
      load();
      loadSchemaClasses();
      loadDimensionGroups();
    }
  }, [activeScenario]);

  const save = async () => {
    if (!editMetric?.name) {
      addToast("warning", "名称必填");
      return;
    }
    if (!activeMetricDefinition.anchor_class) {
      addToast("warning", "请选择锚点类");
      return;
    }
    const outputs = activeMetricDefinition.version === 2
      ? activeMetricDefinition.outputs
      : [{ output_name: "", inputs: activeMetricDefinition.inputs }];
    if (!outputs.length || outputs.some((output) => !output.inputs.length || output.inputs.some((input) => !input.class_id || !input.field))) {
      addToast("warning", "请完整配置每个指标输出的组成项");
      return;
    }
    if (activeMetricDefinition.version === 2) {
      const names = activeMetricDefinition.outputs.map((output) => output.output_name.trim());
      if (names.some((name) => !name) || new Set(names).size !== names.length) {
        addToast("warning", "并列输出名称必填且不能重复");
        return;
      }
    }
    if (outputs.some((output) => output.inputs.some((input) => input.source_shape === "long" && !input.filters?.length))) {
      addToast("warning", "窄表指标组成项必须配置至少一个固定条件");
      return;
    }
    const isEdit = !!editMetric.id;
    const payload = {
      ...editMetric,
      id: isEdit ? editMetric.id : metricIdFromName(editMetric.name),
      target_class: activeMetricDefinition.anchor_class,
      definition: activeMetricDefinition,
      dimensions: editMetric.dimensions || [],
      required_dimensions: editMetric.required_dimensions || [],
    };
    try {
      const idSuffix = isEdit ? `/${editMetric.id}` : "";
      await api(`/api/scenarios/${activeScenario}/metrics${idSuffix}`, {
        method: isEdit ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      addToast("success", isEdit ? "指标已更新" : "指标已创建");
      setIsModalOpen(false);
      setEditMetric(null);
      setTargetClassSearch("");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const remove = async (id: string) => {
    try {
      await api(`/api/scenarios/${activeScenario}/metrics/${id}`, {
        method: "DELETE",
      });
      addToast("success", "指标已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "删除失败");
    }
  };

  const toggleMetricSelection = (id: string) => {
    setSelectedMetricIds((current) =>
      current.includes(id)
        ? current.filter((selectedId) => selectedId !== id)
        : [...current, id],
    );
  };

  const removeSelected = async () => {
    if (!selectedMetricIds.length) return;
    try {
      const result = await api(
        `/api/scenarios/${activeScenario}/metrics/batch-delete`,
        {
          method: "POST",
          body: JSON.stringify({ ids: selectedMetricIds }),
        },
      );
      addToast(
        "success",
        `已删除 ${result?.deleted ?? selectedMetricIds.length} 个指标`,
      );
      setSelectedMetricIds([]);
      setIsBatchDeleteOpen(false);
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "批量删除失败");
    }
  };

  const categories = useMemo(
    () => [...new Set(metrics.map((m) => m.category).filter(Boolean))],
    [metrics],
  );
  const targetClasses = useMemo(
    () => [
      ...new Set(
        metrics.flatMap((m) => metricTargetClasses(m)).filter(Boolean),
      ),
    ],
    [metrics],
  );
  const schemaClassOptions = useMemo(
    () =>
      [...schemaClasses].sort((left, right) =>
        String(left.name_cn || left.id).localeCompare(
          String(right.name_cn || right.id),
          "zh-Hans-CN",
          { numeric: true },
        ),
      ),
    [schemaClasses],
  );
  const schemaClassById = useMemo(
    () =>
      new Map(
        schemaClassOptions.map((schemaClass) => [schemaClass.id, schemaClass]),
      ),
    [schemaClassOptions],
  );
  const metricDefinition = useMemo<MetricDefinition>(() => {
    const definition = editMetric?.definition;
    if (definition?.version === 1) return definition;
    return {
      ...emptyMetricDefinition(),
      anchor_class: metricTargetClasses(editMetric)[0] || "",
    };
  }, [editMetric]);
  const parallelMetricDefinition = useMemo(() => {
    const definition = editMetric?.definition;
    return definition?.version === 2 ? definition : null;
  }, [editMetric]);
  const activeMetricDefinition: AnyMetricDefinition =
    parallelMetricDefinition || metricDefinition;
  const componentSourceClassOptions = useMemo(() => {
    const anchorClass = activeMetricDefinition.anchor_class;
    if (!anchorClass) return schemaClassOptions;
    return [...schemaClassOptions].sort((left, right) => {
      if (left.id === anchorClass) return -1;
      if (right.id === anchorClass) return 1;
      return 0;
    });
  }, [activeMetricDefinition.anchor_class, schemaClassOptions]);
  const availableTargetClassOptions = useMemo(() => {
    const selected = new Set(metricTargetClasses(editMetric));
    const keyword = targetClassSearch.trim().toLowerCase();

    return schemaClassOptions.filter((schemaClass) => {
      if (selected.has(schemaClass.id)) return false;
      if (normalizeReviewStatus(schemaClass.is_reviewed) === -1) return false;
      if (!keyword) return true;
      return [schemaClass.id, schemaClass.name_cn, schemaClass.description]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword));
    });
  }, [editMetric, schemaClassOptions, targetClassSearch]);
  const reviewStats = useMemo(() => {
    const counts: Record<ReviewStatus, number> = { "-1": 0, "0": 0, "1": 0 };
    for (const metric of metrics) {
      counts[normalizeReviewStatus(metric.is_reviewed)] += 1;
    }
    return counts;
  }, [metrics]);
  const filtered = useMemo(() => {
    const keyword = search.toLowerCase();
    return metrics.filter((m) => {
      const ms =
        !keyword ||
        m.name.toLowerCase().includes(keyword) ||
        m.description?.toLowerCase().includes(keyword);
      const mc = !filterCategory || m.category === filterCategory;
      const mt =
        !filterTargetClass ||
        metricTargetClasses(m).includes(filterTargetClass);
      const mr =
        !filterReviewStatus ||
        normalizeReviewStatus(m.is_reviewed) === Number(filterReviewStatus);
      return ms && mc && mt && mr;
    });
  }, [filterCategory, filterReviewStatus, filterTargetClass, metrics, search]);
  const sortedMetrics = useMemo(() => {
    return [...filtered].sort((left, right) => {
      const leftValue = metricSortValue(left, sort.key);
      const rightValue = metricSortValue(right, sort.key);
      const direction = sort.direction === "asc" ? 1 : -1;

      if (typeof leftValue === "number" && typeof rightValue === "number") {
        return (leftValue - rightValue) * direction;
      }
      return (
        String(leftValue).localeCompare(String(rightValue), "zh-Hans-CN", {
          numeric: true,
        }) * direction
      );
    });
  }, [filtered, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const targetClassLabel = (targetClass: string) => {
    const schemaClass = schemaClassById.get(targetClass);
    return schemaClass
      ? schemaClassLabel(schemaClass)
      : `${targetClass}（未匹配）`;
  };

  const toggleDimensionGroup = (groupId: string) => {
    const current = editMetric?.dimension_group_ids || [];
    const dimension_group_ids = current.includes(groupId)
      ? current.filter((id) => id !== groupId)
      : [...current, groupId];
    setEditMetric({ ...editMetric!, dimension_group_ids });
  };

  const updateMetricDefinition = (definition: MetricDefinition) => {
    setEditMetric({
      ...editMetric!,
      definition,
      target_class: definition.anchor_class,
    });
  };

  const updateParallelMetricDefinition = (
    definition: Extract<AnyMetricDefinition, { version: 2 }>,
  ) => {
    setEditMetric({ ...editMetric!, definition, target_class: definition.anchor_class });
  };

  const updateParallelOutput = (
    outputIndex: number,
    changes: Partial<MetricOutput>,
  ) => {
    if (!parallelMetricDefinition) return;
    updateParallelMetricDefinition({
      ...parallelMetricDefinition,
      outputs: parallelMetricDefinition.outputs.map((output, index) =>
        index === outputIndex ? { ...output, ...changes } : output,
      ),
    });
  };

  const updateParallelInput = (
    outputIndex: number,
    inputIndex: number,
    changes: Partial<MetricInput>,
  ) => {
    const output = parallelMetricDefinition?.outputs[outputIndex];
    if (!output) return;
    updateParallelOutput(outputIndex, {
      inputs: output.inputs.map((input, index) =>
        index === inputIndex ? { ...input, ...changes } : input,
      ),
    });
  };

  const updateMetricInput = (index: number, changes: Partial<MetricInput>) => {
    updateMetricDefinition({
      ...metricDefinition,
      inputs: metricDefinition.inputs.map((input, inputIndex) =>
        inputIndex === index ? { ...input, ...changes } : input,
      ),
    });
  };

  const updateMetricInputFilters = (
    inputIndex: number,
    filters: MetricInput["filters"],
  ) => updateMetricInput(inputIndex, { filters });

  const metricInputFilterValueKey = (classId: string, field: string) =>
    `${classId}:${field}`;
  const loadMetricInputFilterValues = async (
    classId: string,
    field: string,
  ) => {
    const cacheKey = metricInputFilterValueKey(classId, field);
    if (!classId || !field || metricFilterValueOptions[cacheKey]) return;
    try {
      const result = await api(
        `/api/scenarios/${activeScenario}/metrics/field-values?class_id=${encodeURIComponent(classId)}&field=${encodeURIComponent(field)}&limit=100`,
      );
      setMetricFilterValueOptions((current) => ({
        ...current,
        [cacheKey]: (result?.values || []).map(String),
      }));
    } catch (error: any) {
      addToast("warning", error.message || "无法加载字段候选值，可手动输入");
    }
  };

  const renderSortableHeader = (key: SortKey, label: string) => {
    const active = sort.key === key;
    const Icon = active
      ? sort.direction === "asc"
        ? ArrowUp
        : ArrowDown
      : ArrowUpDown;
    return (
      <th key={key}>
        <button
          type="button"
          onClick={() => toggleSort(key)}
          className="inline-flex items-center gap-1.5 text-xs font-medium uppercase text-slate-500 transition-colors hover:text-slate-800"
        >
          <span>{label}</span>
          <Icon className="h-3.5 w-3.5" />
        </button>
      </th>
    );
  };

  if (!activeScenario)
    return (
      <div>
        <h2 className="mb-4 text-lg font-semibold text-slate-800">指标管理</h2>
        <ScenarioSelector />
      </div>
    );

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">指标管理</h2>
        <button
          onClick={() => {
            setEditMetric({
              scenario_id: activeScenario,
              category: "",
              target_class: "",
              dimensions: [],
              required_dimensions: [],
              dimension_group_ids: [],
              definition: emptyMetricDefinition(),
              chart_type: "bar",
              sort_order: 0,
              is_reviewed: 0,
            });
            setTargetClassSearch("");
            setMetricFilterValueOptions({});
            setIsModalOpen(true);
          }}
          className="btn-primary"
        >
          + 新增指标
        </button>
      </div>
      <ScenarioSelector />
      <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem_10rem_8rem]">
        <div className="flex-1">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="搜索指标..."
          />
        </div>
        <select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
          className="text-sm"
        >
          <option value="">全部分类</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={filterTargetClass}
          onChange={(e) => setFilterTargetClass(e.target.value)}
          className="text-sm"
        >
          <option value="">全部目标类</option>
          {targetClasses.map((targetClass) => (
            <option key={targetClass} value={targetClass}>
              {targetClass}
            </option>
          ))}
        </select>
        <select
          value={filterReviewStatus}
          onChange={(e) => setFilterReviewStatus(e.target.value)}
          className="text-sm"
        >
          <option value="">全部审核</option>
          {REVIEW_STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className="mr-1 font-medium text-slate-600">审核统计</span>
        {REVIEW_STATUS_OPTIONS.map((option) => {
          const active = filterReviewStatus === String(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() =>
                setFilterReviewStatus(active ? "" : String(option.value))
              }
              className={`inline-flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                active
                  ? reviewStatusClassName(option.value)
                  : "bg-slate-50 text-slate-600 hover:bg-slate-100"
              }`}
            >
              <span>{option.label}</span>
              <span className="font-semibold">{reviewStats[option.value]}</span>
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => setFilterReviewStatus("")}
          className={`inline-flex items-center gap-1 rounded px-2 py-1 transition-colors ${
            filterReviewStatus === ""
              ? "bg-slate-700 text-white"
              : "bg-slate-50 text-slate-600 hover:bg-slate-100"
          }`}
        >
          <span>全部</span>
          <span className="font-semibold">{metrics.length}</span>
        </button>
      </div>

      {selectedMetricIds.length > 0 && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <span>已选择 {selectedMetricIds.length} 个指标</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSelectedMetricIds([])}
              className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-100"
            >
              取消选择
            </button>
            <button
              type="button"
              onClick={() => setIsBatchDeleteOpen(true)}
              className="rounded bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-700"
            >
              删除所选
            </button>
          </div>
        </div>
      )}

      {loading && <LoadingSpinner />}
      {!loading && sortedMetrics.length === 0 && (
        <EmptyState
          icon="📐"
          title="暂无指标"
          description="使用AI提取或手动创建"
        />
      )}
      {!loading && sortedMetrics.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table min-w-[900px] table-fixed">
              <colgroup>
                <col className="w-10" />
                <col className="w-60" />
                <col className="w-24" />
                <col className="w-56" />
                <col className="w-24" />
                <col className="w-24" />
                <col className="w-40" />
                <col className="w-24" />
              </colgroup>
              <thead>
                <tr>
                  <th className="text-center">
                    <input
                      type="checkbox"
                      checked={
                        sortedMetrics.length > 0 &&
                        sortedMetrics.every((metric) =>
                          selectedMetricIds.includes(metric.id),
                        )
                      }
                      onChange={() => {
                        const visibleIds = sortedMetrics.map(
                          (metric) => metric.id,
                        );
                        const allVisibleSelected = visibleIds.every((id) =>
                          selectedMetricIds.includes(id),
                        );
                        setSelectedMetricIds((current) =>
                          allVisibleSelected
                            ? current.filter((id) => !visibleIds.includes(id))
                            : [...new Set([...current, ...visibleIds])],
                        );
                      }}
                      aria-label="全选当前筛选结果"
                    />
                  </th>
                  {SORTABLE_COLUMNS.map((column) =>
                    renderSortableHeader(column.key, column.label),
                  )}
                  <th>最后更新时间</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {sortedMetrics.map((m) => (
                  <tr key={m.id}>
                    <td className="text-center">
                      <input
                        type="checkbox"
                        checked={selectedMetricIds.includes(m.id)}
                        onChange={() => toggleMetricSelection(m.id)}
                        aria-label={`选择指标 ${m.name}`}
                      />
                    </td>
                    <td
                      className="whitespace-normal break-words font-medium leading-relaxed text-slate-700"
                      title={
                        m.description ? `${m.name}\n\n${m.description}` : m.name
                      }
                    >
                      {m.name}
                    </td>
                    <td>
                      <span className="inline-block max-w-full break-words rounded bg-slate-100 px-1.5 py-0.5 text-xs leading-relaxed text-slate-500">
                        {m.category || "-"}
                      </span>
                    </td>
                    <td className="whitespace-normal break-words text-xs leading-relaxed">
                      <div className="flex flex-wrap gap-1">
                        {metricTargetClasses(m).length ? (
                          metricTargetClasses(m).map((targetClass) => (
                            <span
                              key={targetClass}
                              className="rounded-full bg-sky-50 px-2 py-0.5 text-sky-700"
                              title={targetClassLabel(targetClass)}
                            >
                              {targetClass}
                            </span>
                          ))
                        ) : (
                          <span className="text-slate-400">-</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <span className="inline-block max-w-full break-words rounded bg-indigo-50 px-1.5 py-0.5 text-xs leading-relaxed text-indigo-600">
                        {CHART_LABELS[m.chart_type] || m.chart_type}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`inline-block rounded px-1.5 py-0.5 text-xs ${reviewStatusClassName(m.is_reviewed)}`}
                      >
                        {reviewStatusLabel(m.is_reviewed)}
                      </span>
                    </td>
                    <td className="whitespace-nowrap text-xs text-slate-500">
                      {m.updated_at || "-"}
                    </td>
                    <td className="whitespace-nowrap text-right">
                      <button
                        onClick={() => {
                          setEditMetric(m);
                          setTargetClassSearch("");
                          setIsModalOpen(true);
                        }}
                        className="btn-ghost text-xs"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => setDeleteTarget(m.id)}
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

      <Modal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setEditMetric(null);
          setTargetClassSearch("");
        }}
        title={editMetric?.id ? "编辑指标" : "新增指标"}
        width="max-w-6xl"
        footer={
          <>
            <button
              onClick={() => {
                setIsModalOpen(false);
                setEditMetric(null);
                setTargetClassSearch("");
              }}
              className="btn-outline"
            >
              取消
            </button>
            <button onClick={save} className="btn-primary">
              保存
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(180px,1fr)]">
            <div>
              <label
                htmlFor="metric-field-1"
                className="mb-1.5 block text-xs font-medium text-slate-500"
              >
                名称
              </label>
              <input
                id="metric-field-1"
                value={editMetric?.name || ""}
                onChange={(e) =>
                  setEditMetric({ ...editMetric!, name: e.target.value })
                }
                className="w-full"
              />
            </div>
            <div>
              <label
                htmlFor="metric-field-3"
                className="mb-1.5 block text-xs font-medium text-slate-500"
              >
                分类
              </label>
              <input
                id="metric-field-3"
                value={editMetric?.category || ""}
                onChange={(e) =>
                  setEditMetric({ ...editMetric!, category: e.target.value })
                }
                className="w-full"
              />
            </div>
          </div>
          <div>
            <label
              htmlFor="metric-field-2"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              描述
            </label>
            <textarea
              id="metric-field-2"
              value={editMetric?.description || ""}
              onChange={(e) =>
                setEditMetric({ ...editMetric!, description: e.target.value })
              }
              className="w-full"
              rows={2}
            />
          </div>
          <div className="rounded border border-sky-200 bg-sky-50/40 p-4">
            <h3 className="text-sm font-semibold text-slate-700">
              指标来源与计算
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              每个组成项可选择不同来源类和字段；同一表多字段、跨表指标均使用同一个表达式模型。
            </p>
            <div className="mt-3 rounded border border-sky-200 bg-white/80 p-3">
              <div className="grid items-center gap-2 md:grid-cols-[10rem_minmax(15rem,22rem)_1fr]">
                <label className="text-sm font-medium text-slate-700">
                  计算模式
                </label>
                <select
                  value={parallelMetricDefinition ? "parallel" : "single"}
                  onChange={(event) => {
                    if (event.target.value === "parallel") {
                      updateParallelMetricDefinition(
                        emptyParallelMetricDefinition(activeMetricDefinition.anchor_class) as Extract<AnyMetricDefinition, { version: 2 }>,
                      );
                    } else {
                      updateMetricDefinition({
                        ...metricDefinition,
                        anchor_class: activeMetricDefinition.anchor_class,
                      });
                    }
                  }}
                  className="w-full"
                >
                  <option value="single">单一计算：一个表达式</option>
                  <option value="parallel">多个并列计算：一个 Metric 输出多列</option>
                </select>
                <p className="text-xs text-slate-500">
                  例如同一个“规格达成率”同时输出 50mg、100mg、200mg 的实际额 ÷ 目标额。
                </p>
              </div>
            </div>
            {parallelMetricDefinition && (
              <div className="mt-4 space-y-3 rounded border border-violet-200 bg-violet-50/50 p-3">
                <div className="grid gap-3 md:grid-cols-[minmax(12rem,1fr)_auto]">
                  <select
                    value={parallelMetricDefinition.anchor_class}
                    onChange={(event) =>
                      updateParallelMetricDefinition({ ...parallelMetricDefinition, anchor_class: event.target.value })
                    }
                  >
                    <option value="">请选择锚点类</option>
                    {schemaClassOptions.map((schemaClass) => (
                      <option key={schemaClass.id} value={schemaClass.id}>{schemaClassLabel(schemaClass)}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => updateParallelMetricDefinition({
                      ...parallelMetricDefinition,
                      outputs: [...parallelMetricDefinition.outputs, emptyMetricOutput(parallelMetricDefinition.outputs.length)],
                    })}
                    className="btn-ghost text-xs text-violet-700"
                  >
                    <Plus className="mr-1 inline h-3 w-3" />添加并列输出
                  </button>
                </div>
                {parallelMetricDefinition.outputs.map((output, outputIndex) => (
                  <div key={output.id} className="rounded border border-violet-200 bg-white p-3">
                    <div className="mb-2 grid gap-2 md:grid-cols-[minmax(12rem,1fr)_10rem_auto]">
                      <input
                        value={output.output_name}
                        onChange={(event) => updateParallelOutput(outputIndex, { output_name: event.target.value })}
                        placeholder="输出名称，例如：50mg达成率"
                      />
                      <select
                        value={output.expression_operator}
                        onChange={(event) => updateParallelOutput(outputIndex, { expression_operator: event.target.value as MetricOutput["expression_operator"] })}
                      >
                        <option value="DIVIDE">相除</option><option value="ADD">相加</option><option value="SUBTRACT">相减</option><option value="MULTIPLY">相乘</option>
                      </select>
                      <button
                        type="button"
                        disabled={parallelMetricDefinition.outputs.length === 1}
                        onClick={() => updateParallelMetricDefinition({ ...parallelMetricDefinition, outputs: parallelMetricDefinition.outputs.filter((_, index) => index !== outputIndex) })}
                        className="btn-ghost p-1 text-slate-500 disabled:opacity-30"
                        aria-label="删除并列输出"
                      ><X className="h-4 w-4" /></button>
                    </div>
                    {output.inputs.map((input, inputIndex) => {
                      const inputClass = schemaClassById.get(input.class_id);
                      return <div key={input.id} className="mb-2 grid gap-2 md:grid-cols-[minmax(11rem,1fr)_minmax(11rem,1fr)_8rem_2rem]">
                        <select value={input.class_id} onChange={(event) => updateParallelInput(outputIndex, inputIndex, { class_id: event.target.value, field: "", filters: [] })}>
                          <option value="">选择来源类</option>
                          {componentSourceClassOptions.map((schemaClass) => <option key={schemaClass.id} value={schemaClass.id}>{schemaClassLabel(schemaClass)}</option>)}
                        </select>
                        <select value={input.field} disabled={!inputClass} onChange={(event) => updateParallelInput(outputIndex, inputIndex, { field: event.target.value })}>
                          <option value="">选择字段</option>
                          {(inputClass?.fields || []).map((field) => <option key={field.physical_name || field.name} value={field.physical_name || field.name}>{field.name}（{field.physical_name}）</option>)}
                        </select>
                        <select value={input.aggregation} onChange={(event) => updateParallelInput(outputIndex, inputIndex, { aggregation: event.target.value as MetricInput["aggregation"] })}>
                          <option value="SUM">求和</option><option value="AVG">平均</option><option value="MIN">最小</option><option value="MAX">最大</option><option value="COUNT">计数</option><option value="COUNT_DISTINCT">去重计数</option>
                        </select>
                        <button type="button" disabled={output.inputs.length === 1} onClick={() => updateParallelOutput(outputIndex, { inputs: output.inputs.filter((_, index) => index !== inputIndex) })} className="btn-ghost p-1 text-slate-500 disabled:opacity-30" aria-label="删除组成项"><X className="h-4 w-4" /></button>
                      </div>;
                    })}
                    <button type="button" onClick={() => updateParallelOutput(outputIndex, { inputs: [...output.inputs, emptyMetricInput(output.inputs.length)] })} className="btn-ghost text-xs text-violet-700"><Plus className="mr-1 inline h-3 w-3" />添加组成项</button>
                  </div>
                ))}
              </div>
            )}
            <div className={parallelMetricDefinition ? "hidden" : ""}>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-500">
                  锚点类
                </label>
                <select
                  value={metricDefinition.anchor_class}
                  onChange={(event) =>
                    updateMetricDefinition({
                      ...metricDefinition,
                      anchor_class: event.target.value,
                    })
                  }
                  className="w-full"
                >
                  <option value="">请选择锚点类</option>
                  {schemaClassOptions.map((schemaClass) => (
                    <option key={schemaClass.id} value={schemaClass.id}>
                      {schemaClassLabel(schemaClass)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-500">
                  组成项计算方式
                </label>
                <select
                  value={metricDefinition.expression_operator}
                  onChange={(event) =>
                    updateMetricDefinition({
                      ...metricDefinition,
                      expression_operator: event.target
                        .value as MetricDefinition["expression_operator"],
                    })
                  }
                  className="w-full"
                >
                  <option value="ADD">相加</option>
                  <option value="SUBTRACT">依次相减</option>
                  <option value="MULTIPLY">相乘</option>
                  <option value="DIVIDE">依次相除</option>
                  <option value="CONCAT">拼接（并列展示）</option>
                </select>
              </div>
              <div />
            </div>
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between">
                <label className="text-xs font-medium text-slate-600">
                  指标组成项
                </label>
                <button
                  type="button"
                  onClick={() =>
                    updateMetricDefinition({
                      ...metricDefinition,
                      inputs: [
                        ...metricDefinition.inputs,
                        emptyMetricInput(metricDefinition.inputs.length),
                      ],
                    })
                  }
                  className="btn-ghost text-xs text-sky-700"
                >
                  <Plus className="mr-1 inline h-3 w-3" />
                  添加组成项
                </button>
              </div>
              <div className="space-y-3">
                {metricDefinition.inputs.map((input, index) => {
                  const inputClass = schemaClassById.get(input.class_id);
                  return (
                    <div
                      key={input.id}
                      className="rounded border border-slate-200 bg-white p-3"
                    >
                      <div className="grid gap-2 md:grid-cols-[minmax(11rem,1.2fr)_7rem_minmax(11rem,1fr)_9rem_minmax(10rem,0.9fr)_2rem]">
                        <select
                          value={input.class_id}
                          onChange={(event) =>
                            updateMetricInput(index, {
                              class_id: event.target.value,
                              field: "",
                              filters: [],
                            })
                          }
                        >
                          <option value="">选择来源类</option>
                          {componentSourceClassOptions.map((schemaClass) => (
                            <option key={schemaClass.id} value={schemaClass.id}>
                              {schemaClassLabel(schemaClass)}
                            </option>
                          ))}
                        </select>
                        <select
                          value={input.source_shape || "wide"}
                          onChange={(event) =>
                            updateMetricInput(index, {
                              source_shape: event.target.value as NonNullable<
                                MetricInput["source_shape"]
                              >,
                            })
                          }
                          aria-label="来源数据形态"
                        >
                          <option value="wide">宽表</option>
                          <option value="long">窄表</option>
                        </select>
                        <select
                          value={input.field}
                          disabled={!inputClass}
                          onChange={(event) => {
                            const field = inputClass?.fields.find(
                              (item) =>
                                (item.physical_name || item.name) ===
                                event.target.value,
                            );
                            updateMetricInput(index, {
                              field: event.target.value,
                              output_name: field?.name || "",
                            });
                          }}
                        >
                          <option value="">选择字段</option>
                          {(inputClass?.fields || []).map((field) => (
                            <option
                              key={field.physical_name || field.name}
                              value={field.physical_name || field.name}
                            >
                              {field.name}（{field.physical_name}）
                            </option>
                          ))}
                        </select>
                        <select
                          value={input.aggregation}
                          onChange={(event) =>
                            updateMetricInput(index, {
                              aggregation: event.target
                                .value as MetricInput["aggregation"],
                            })
                          }
                        >
                          <option value="SUM">求和</option>
                          <option value="AVG">平均</option>
                          <option value="MIN">最小</option>
                          <option value="MAX">最大</option>
                          <option value="COUNT">计数</option>
                          <option value="COUNT_DISTINCT">去重计数</option>
                        </select>
                        <input
                          value={input.output_name || ""}
                          onChange={(event) =>
                            updateMetricInput(index, {
                              output_name: event.target.value,
                            })
                          }
                          placeholder="组成项名称（默认字段中文名）"
                          disabled={
                            metricDefinition.expression_operator !== "CONCAT"
                          }
                        />
                        <button
                          type="button"
                          disabled={metricDefinition.inputs.length === 1}
                          onClick={() =>
                            updateMetricDefinition({
                              ...metricDefinition,
                              inputs: metricDefinition.inputs.filter(
                                (_, inputIndex) => inputIndex !== index,
                              ),
                            })
                          }
                          className="btn-ghost p-1 text-slate-500 disabled:opacity-30"
                          aria-label="删除指标组成项"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                      {(input.source_shape || "wide") === "long" && (
                        <div className="mt-3 rounded border border-amber-200 bg-amber-50/60 p-2.5">
                          <div className="mb-2 flex items-center justify-between">
                            <span className="text-xs font-medium text-amber-800">
                              窄表固定条件（WHERE）
                            </span>
                            <button
                              type="button"
                              onClick={() =>
                                updateMetricInputFilters(index, [
                                  ...(input.filters || []),
                                  { field: "", operator: "=", value: "" },
                                ])
                              }
                              className="btn-ghost text-xs text-amber-800"
                            >
                              <Plus className="mr-1 inline h-3 w-3" />
                              添加条件
                            </button>
                          </div>
                          {(input.filters || []).length === 0 ? (
                            <p className="text-xs text-amber-700">
                              请选择用于识别该 KPI、规格或类别的固定条件。
                            </p>
                          ) : (
                            <div className="space-y-2">
                              {input.filters.map((filter, filterIndex) => {
                                const valueKey = metricInputFilterValueKey(
                                  input.class_id,
                                  filter.field,
                                );
                                const hasNoValue =
                                  filter.operator === "IS NULL" ||
                                  filter.operator === "IS NOT NULL";
                                return (
                                  <div
                                    key={`${input.id}-filter-${filterIndex}`}
                                    className="grid gap-2 md:grid-cols-[minmax(10rem,1fr)_8rem_minmax(11rem,1fr)_2rem]"
                                  >
                                    <select
                                      value={filter.field}
                                      disabled={!inputClass}
                                      onChange={(event) => {
                                        const field = event.target.value;
                                        const next = input.filters.map(
                                          (item, itemIndex) =>
                                            itemIndex === filterIndex
                                              ? { ...item, field }
                                              : item,
                                        );
                                        updateMetricInputFilters(index, next);
                                        void loadMetricInputFilterValues(
                                          input.class_id,
                                          field,
                                        );
                                      }}
                                    >
                                      <option value="">选择条件字段</option>
                                      {(inputClass?.fields || []).map(
                                        (field) => (
                                          <option
                                            key={
                                              field.physical_name || field.name
                                            }
                                            value={
                                              field.physical_name || field.name
                                            }
                                          >
                                            {field.name}（{field.physical_name}）
                                          </option>
                                        ),
                                      )}
                                    </select>
                                    <select
                                      value={filter.operator}
                                      onChange={(event) =>
                                        updateMetricInputFilters(
                                          index,
                                          input.filters.map(
                                            (item, itemIndex) =>
                                              itemIndex === filterIndex
                                                ? {
                                                    ...item,
                                                    operator: event.target
                                                      .value as MetricInput["filters"][number]["operator"],
                                                  }
                                                : item,
                                          ),
                                        )
                                      }
                                    >
                                      <option value="=">等于</option>
                                      <option value="!=">不等于</option>
                                      <option value="IN">属于</option>
                                      <option value="NOT IN">不属于</option>
                                      <option value="IS NULL">为空</option>
                                      <option value="IS NOT NULL">
                                        不为空
                                      </option>
                                    </select>
                                    <input
                                      list={`${input.id}-filter-values-${filterIndex}`}
                                      disabled={hasNoValue}
                                      value={
                                        Array.isArray(filter.value)
                                          ? filter.value.join("，")
                                          : String(filter.value || "")
                                      }
                                      onChange={(event) => {
                                        const rawValue = event.target.value;
                                        const value =
                                          filter.operator === "IN" ||
                                          filter.operator === "NOT IN"
                                            ? parseFilterValues(rawValue)
                                            : rawValue;
                                        updateMetricInputFilters(
                                          index,
                                          input.filters.map(
                                            (item, itemIndex) =>
                                              itemIndex === filterIndex
                                                ? { ...item, value }
                                                : item,
                                          ),
                                        );
                                      }}
                                      placeholder={
                                        hasNoValue
                                          ? "无需填写值"
                                          : filter.operator === "IN" ||
                                              filter.operator === "NOT IN"
                                            ? "多个值用逗号分隔"
                                            : "固定条件值"
                                      }
                                    />
                                    <datalist
                                      id={`${input.id}-filter-values-${filterIndex}`}
                                    >
                                      {(
                                        metricFilterValueOptions[valueKey] || []
                                      ).map((value) => (
                                        <option key={value} value={value} />
                                      ))}
                                    </datalist>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        updateMetricInputFilters(
                                          index,
                                          input.filters.filter(
                                            (_, itemIndex) =>
                                              itemIndex !== filterIndex,
                                          ),
                                        )
                                      }
                                      className="btn-ghost p-1 text-slate-500 hover:text-red-500"
                                      aria-label="删除固定条件"
                                    >
                                      <X className="h-4 w-4" />
                                    </button>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
                      {inputClass && (
                        <p className="mt-2 text-xs text-slate-400">
                          组成项 {index + 1}：{input.aggregation}(
                          {schemaClassLabel(inputClass)}.{input.field || "字段"}
                          )
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-slate-500">
                每个组成项独立声明来源数据形态：宽表直接选择对应业务字段；窄表选择公共数值字段，并通过该组成项固定条件区分
                KPI、规格等口径。表达式按组成项顺序计算；“拼接（并列展示）”会将每个组成项独立聚合并输出为一列。
              </p>
            </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label
                htmlFor="metric-field-7"
                className="mb-1.5 block text-xs font-medium text-slate-500"
              >
                图表类型
              </label>
              <select
                id="metric-field-7"
                value={editMetric?.chart_type || "bar"}
                onChange={(e) =>
                  setEditMetric({ ...editMetric!, chart_type: e.target.value })
                }
                className="w-full"
              >
                {Object.entries(CHART_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="metric-field-8"
                className="mb-1.5 block text-xs font-medium text-slate-500"
              >
                排序
              </label>
              <input
                id="metric-field-8"
                type="number"
                value={editMetric?.sort_order || 0}
                onChange={(e) =>
                  setEditMetric({
                    ...editMetric!,
                    sort_order: Number(e.target.value),
                  })
                }
                className="w-32"
              />
            </div>
          </div>
          <div className="rounded border border-indigo-200 bg-indigo-50/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-slate-700">
                  分析维度组
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  为指标关联业务级分析维度。字段映射、业务选项和澄清策略统一在“分析维度组”中维护。
                </p>
              </div>
              <span className="rounded bg-white px-2 py-1 text-xs text-slate-500">
                已关联 {(editMetric?.dimension_group_ids || []).length} 个
              </span>
            </div>
            {dimensionGroups.length === 0 ? (
              <p className="mt-3 text-xs text-amber-700">
                当前场景尚未配置分析维度组，请先到“分析维度组”页面创建并审核通过。
              </p>
            ) : (
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {dimensionGroups
                  .filter(
                    (group) =>
                      group.status === "approved" ||
                      (editMetric?.dimension_group_ids || []).includes(group.id),
                  )
                  .map((group) => {
                    const selected = (editMetric?.dimension_group_ids || []).includes(group.id);
                    const selectable = group.status === "approved";
                    return (
                      <label
                        key={group.id}
                        className={`rounded border p-3 text-sm ${selected ? "border-indigo-300 bg-white" : "border-slate-200 bg-white/70"} ${!selectable && !selected ? "opacity-50" : ""}`}
                      >
                        <div className="flex items-start gap-2">
                          <input
                            type="checkbox"
                            checked={selected}
                            disabled={!selectable && !selected}
                            onChange={() => toggleDimensionGroup(group.id)}
                          />
                          <div className="min-w-0">
                            <div className="font-medium text-slate-700">
                              {group.name}
                              {group.is_required && (
                                <span className="ml-1.5 text-xs font-normal text-rose-600">
                                  必选
                                </span>
                              )}
                            </div>
                            <div className="mt-0.5 text-xs text-slate-400">
                              {group.id} · {group.group_type}
                              {group.status !== "approved" ? " · 未通过" : ""}
                            </div>
                            <div className="mt-2 flex flex-wrap gap-1">
                              {group.options.map((option) => (
                                <span
                                  key={option.value}
                                  className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600"
                                >
                                  {option.label}
                                  {option.value === group.default_option ? " · 默认" : ""}
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                      </label>
                    );
                  })}
              </div>
            )}
            {(editMetric?.dimensions || []).length > 0 && (
              <p className="mt-3 text-xs text-slate-400">
                兼容提示：该指标仍保存了旧版字段维度配置；后续 ClarifyAgent 集成会优先使用分析维度组。
              </p>
            )}
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              人工审核
            </label>
            <select
              value={normalizeReviewStatus(editMetric?.is_reviewed)}
              onChange={(e) =>
                setEditMetric({
                  ...editMetric!,
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
        title="删除指标"
        message="确定要删除此指标吗？"
        onConfirm={() => {
          if (deleteTarget) {
            remove(deleteTarget);
            setDeleteTarget(null);
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
      <ConfirmDialog
        isOpen={isBatchDeleteOpen}
        title="批量删除指标"
        message={`确定要删除已选择的 ${selectedMetricIds.length} 个指标吗？此操作不可撤销。`}
        onConfirm={removeSelected}
        onCancel={() => setIsBatchDeleteOpen(false)}
      />
    </div>
  );
}
