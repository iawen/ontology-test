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
import type { Metric, SchemaClass } from "@/lib/types";
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

function parseDimensionList(value: string) {
  return value
    .split(/[,，、;；\r\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeMetric(metric: Metric): Metric {
  const targetClasses = Array.isArray(metric.target_classes)
    ? metric.target_classes
    : metric.target_class
      ? [metric.target_class]
      : [];
  return {
    ...metric,
    target_class: metric.target_class || targetClasses[0] || "",
    target_classes: targetClasses,
    dimensions: Array.isArray(metric.dimensions) ? metric.dimensions : [],
    required_dimensions: Array.isArray(metric.required_dimensions)
      ? metric.required_dimensions
      : [],
  };
}

function metricSortValue(metric: Metric, key: SortKey) {
  if (key === "is_reviewed") return normalizeReviewStatus(metric.is_reviewed);
  if (key === "chart_type")
    return CHART_LABELS[metric.chart_type] || metric.chart_type || "";
  if (key === "target_class") return (metric.target_classes || []).join(",");
  return String(metric[key] || "");
}

function metricTargetClasses(metric: Partial<Metric> | null | undefined) {
  if (!metric) return [];
  if (Array.isArray(metric.target_classes)) return metric.target_classes;
  return metric.target_class ? [metric.target_class] : [];
}

function schemaClassLabel(schemaClass: SchemaClass) {
  return schemaClass.name_cn
    ? `${schemaClass.name_cn} (${schemaClass.id})`
    : schemaClass.id;
}

export default function MetricManager() {
  const { activeScenario, addToast } = useApp();
  const api = useApi();
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [schemaClasses, setSchemaClasses] = useState<SchemaClass[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState("");
  const [filterTargetClass, setFilterTargetClass] = useState("");
  const [filterReviewStatus, setFilterReviewStatus] = useState("");
  const [editMetric, setEditMetric] = useState<Partial<Metric> | null>(null);
  const [targetClassSearch, setTargetClassSearch] = useState("");
  const [dimensionText, setDimensionText] = useState("");
  const [requiredDimensionText, setRequiredDimensionText] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
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
      const data = await api(
        `/api/scenarios/${activeScenario}/schema/classes`,
      );
      setSchemaClasses(data || []);
    } catch {
      addToast("error", "加载目标类失败");
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
      load();
      loadSchemaClasses();
    }
  }, [activeScenario]);

  const save = async () => {
    if (!editMetric?.name) {
      addToast("warning", "名称必填");
      return;
    }
    if (!metricTargetClasses(editMetric).length) {
      addToast("warning", "目标类必选");
      return;
    }
    const isEdit = !!editMetric.id;
    const payload = {
      ...editMetric,
      id: isEdit ? editMetric.id : metricIdFromName(editMetric.name),
      target_class: metricTargetClasses(editMetric),
      target_classes: metricTargetClasses(editMetric),
      dimensions: parseDimensionList(dimensionText),
      required_dimensions: parseDimensionList(requiredDimensionText),
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
      setDimensionText("");
      setRequiredDimensionText("");
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
  const availableTargetClassOptions = useMemo(() => {
    const selected = new Set(metricTargetClasses(editMetric));
    const keyword = targetClassSearch.trim().toLowerCase();

    return schemaClassOptions.filter((schemaClass) => {
      if (selected.has(schemaClass.id)) return false;
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

  const updateTargetClasses = (targetClasses: string[]) => {
    setEditMetric({
      ...editMetric!,
      target_class: targetClasses[0] || "",
      target_classes: targetClasses,
    });
  };

  const addTargetClass = (targetClass: string) => {
    const current = metricTargetClasses(editMetric);
    if (current.includes(targetClass)) return;
    updateTargetClasses([...current, targetClass]);
  };

  const removeTargetClass = (targetClass: string) => {
    updateTargetClasses(
      metricTargetClasses(editMetric).filter((item) => item !== targetClass),
    );
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
              target_classes: [],
              calculation: "",
              formula: "",
              dimensions: [],
              required_dimensions: [],
              filters_hint: "",
              chart_type: "bar",
              sort_order: 0,
              is_reviewed: 0,
            });
            setDimensionText("");
            setRequiredDimensionText("");
            setTargetClassSearch("");
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
                <col className="w-60" />
                <col className="w-24" />
                <col className="w-56" />
                <col className="w-24" />
                <col className="w-24" />
                <col className="w-24" />
              </colgroup>
              <thead>
                <tr>
                  {SORTABLE_COLUMNS.map((column) =>
                    renderSortableHeader(column.key, column.label),
                  )}
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {sortedMetrics.map((m) => (
                  <tr key={m.id}>
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
                    <td className="whitespace-nowrap text-right">
                      <button
                        onClick={() => {
                          setEditMetric(m);
                          setDimensionText((m.dimensions || []).join(", "));
                          setRequiredDimensionText(
                            (m.required_dimensions || []).join(", "),
                          );
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
          setDimensionText("");
          setRequiredDimensionText("");
        }}
        title={editMetric?.id ? "编辑指标" : "新增指标"}
        footer={
          <>
            <button
              onClick={() => {
                setIsModalOpen(false);
                setEditMetric(null);
                setTargetClassSearch("");
                setDimensionText("");
                setRequiredDimensionText("");
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
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-500">
              目标类
            </label>
            <div className="rounded border border-slate-200 bg-slate-50/80 p-2.5">
              <div className="mb-2 flex min-h-9 flex-wrap items-center gap-2">
                {metricTargetClasses(editMetric).length ? (
                  metricTargetClasses(editMetric).map((targetClass) => (
                    <span
                      key={targetClass}
                      className={[
                        "inline-flex max-w-full items-center gap-1 rounded-full",
                        "border border-sky-200 bg-sky-50 px-2.5 py-1",
                        "text-xs font-medium text-sky-700",
                      ].join(" ")}
                    >
                      <span className="truncate">
                        {targetClassLabel(targetClass)}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeTargetClass(targetClass)}
                        className="rounded-full p-0.5 text-sky-500 hover:bg-sky-100 hover:text-sky-800"
                        aria-label={`移除 ${targetClass}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-slate-400">未选择目标类</span>
                )}
              </div>
              <div className="mb-2 flex items-center gap-2 border-t border-slate-200 pt-2">
                <input
                  value={targetClassSearch}
                  onChange={(event) => setTargetClassSearch(event.target.value)}
                  className="h-8 min-w-0 flex-1 bg-white text-xs"
                  placeholder="搜索目标类名称、ID 或描述"
                />
                <span className="whitespace-nowrap text-xs text-slate-400">
                  {availableTargetClassOptions.length} 个可选
                </span>
                {targetClassSearch && (
                  <button
                    type="button"
                    onClick={() => setTargetClassSearch("")}
                    className={[
                      "inline-flex h-8 items-center gap-1 rounded border",
                      "border-slate-200 bg-white px-2 text-xs text-slate-500",
                      "hover:bg-slate-100",
                    ].join(" ")}
                  >
                    <X className="h-3 w-3" />
                    清空
                  </button>
                )}
              </div>
              <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto border-t border-slate-200 pt-2">
                {availableTargetClassOptions.length ? (
                  availableTargetClassOptions.map((schemaClass) => (
                    <button
                      key={schemaClass.id}
                      type="button"
                      onClick={() => addTargetClass(schemaClass.id)}
                      className={[
                        "inline-flex max-w-full items-center gap-1 rounded-full",
                        "border border-slate-200 bg-white px-2.5 py-1 text-xs",
                        "text-slate-600 transition-colors hover:border-sky-300",
                        "hover:bg-sky-50 hover:text-sky-700",
                      ].join(" ")}
                    >
                      <Plus className="h-3 w-3 shrink-0" />
                      <span className="truncate">
                        {schemaClassLabel(schemaClass)}
                      </span>
                    </button>
                  ))
                ) : (
                  <span className="py-1 text-xs text-slate-400">
                    没有匹配的目标类
                  </span>
                )}
              </div>
            </div>
          </div>
          <div>
            <label
              htmlFor="metric-field-5"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              计算方式
            </label>
            <textarea
              id="metric-field-5"
              value={editMetric?.calculation || ""}
              onChange={(e) =>
                setEditMetric({ ...editMetric!, calculation: e.target.value })
              }
              className="w-full"
              rows={2}
            />
          </div>
          <div>
            <label
              htmlFor="metric-field-6"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              公式
            </label>
            <textarea
              id="metric-field-6"
              value={editMetric?.formula || ""}
              onChange={(e) =>
                setEditMetric({ ...editMetric!, formula: e.target.value })
              }
              className="w-full font-mono text-xs"
              rows={2}
            />
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
          <div>
            <label
              htmlFor="metric-field-9"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              维度 (逗号分隔)
            </label>
            <input
              id="metric-field-9"
              value={dimensionText}
              onChange={(e) => setDimensionText(e.target.value)}
              className="w-full"
              placeholder="region, category"
            />
          </div>
          <div>
            <label
              htmlFor="metric-field-10"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              必要维度 (逗号分隔)
            </label>
            <input
              id="metric-field-10"
              value={requiredDimensionText}
              onChange={(e) => setRequiredDimensionText(e.target.value)}
              className="w-full"
            />
          </div>
          <div>
            <label
              htmlFor="metric-field-11"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              筛选提示
            </label>
            <input
              id="metric-field-11"
              value={editMetric?.filters_hint || ""}
              onChange={(e) =>
                setEditMetric({ ...editMetric!, filters_hint: e.target.value })
              }
              className="w-full"
            />
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
    </div>
  );
}
