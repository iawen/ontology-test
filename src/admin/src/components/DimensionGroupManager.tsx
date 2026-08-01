"use client";

import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, invalidateCache, setCacheData } from "@/lib/cache";
import type {
  Concept,
  DimensionFieldMapping,
  DimensionGroup,
  DimensionOption,
  Metric,
  SchemaClass,
} from "@/lib/types";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";

const emptyOption = (index: number): DimensionOption => ({
  value: "",
  label: "",
  aliases: [],
  is_default: index === 0,
  sort_order: index,
  status: "approved",
});

const emptyGroup = (scenario_id: string): Partial<DimensionGroup> => ({
  scenario_id,
  id: "",
  name: "",
  description: "",
  group_type: "categorical",
  concept_id: "",
  is_required: false,
  default_option: "",
  clarification_policy: "ask_when_ambiguous",
  status: "draft",
  options: [emptyOption(0)],
  field_mappings: [],
  metric_ids: [],
});

const splitAliases = (value: string) =>
  value
    .split(/[，,、;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean);

const normalizeClarificationPolicy = (
  policy: string | undefined,
): DimensionGroup["clarification_policy"] => {
  if (policy === "ask_user" || policy === "ask") return "always_ask";
  return policy === "auto_fill" || policy === "always_ask"
    ? policy
    : "ask_when_ambiguous";
};

export default function DimensionGroupManager() {
  const { activeScenario, addToast, token } = useApp();
  const api = useApi(token);
  const [groups, setGroups] = useState<DimensionGroup[]>([]);
  const [classes, setClasses] = useState<SchemaClass[]>([]);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Partial<DimensionGroup> | null>(null);
  const [viewingAffectedMetrics, setViewingAffectedMetrics] =
    useState<DimensionGroup | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const cacheKey = `dimension-groups:${activeScenario}`;
  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) {
      const cached = getCacheData<DimensionGroup[]>(cacheKey);
      if (cached) {
        setGroups(cached);
        return;
      }
    }
    setLoading(true);
    try {
      const [dimensionGroups, schemaClasses, conceptItems, metricItems] =
        await Promise.all([
          api(`/api/admin/scenarios/${activeScenario}/dimension-groups`),
          api(`/api/scenarios/${activeScenario}/schema/classes`),
          api(`/api/admin/scenarios/${activeScenario}/concepts`),
          api(`/api/scenarios/${activeScenario}/metrics`),
        ]);
      setGroups(dimensionGroups || []);
      setCacheData(cacheKey, dimensionGroups || []);
      setClasses(schemaClasses || []);
      setConcepts(conceptItems || []);
      setMetrics(metricItems || []);
    } catch (error: any) {
      addToast("error", error.message || "加载分析维度组失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeScenario) load();
  }, [activeScenario]);

  const save = async () => {
    if (!editing?.id?.trim() || !editing.name?.trim()) {
      addToast("warning", "维度组 ID 和名称必填");
      return;
    }
    const options = editing.options || [];
    if (
      !options.length ||
      options.some((option) => !option.value.trim() || !option.label.trim())
    ) {
      addToast("warning", "请完整填写至少一个业务选项");
      return;
    }
    const selectedDefault =
      options.find((option) => option.is_default)?.value || "";
    const payload = {
      ...editing,
      default_option: selectedDefault,
      clarification_policy: normalizeClarificationPolicy(
        editing.clarification_policy,
      ),
    };
    const isEdit = groups.some((group) => group.id === editing.id);
    try {
      await api(
        `/api/admin/scenarios/${activeScenario}/dimension-groups${isEdit ? `/${editing.id}` : ""}`,
        {
          method: isEdit ? "PUT" : "POST",
          body: JSON.stringify(payload),
        },
      );
      addToast("success", isEdit ? "维度组已更新" : "维度组已创建");
      setEditing(null);
      invalidateCache(cacheKey);
      load(true);
    } catch (error: any) {
      addToast("error", error.message || "保存失败");
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    try {
      await api(
        `/api/admin/scenarios/${activeScenario}/dimension-groups/${deleteTarget}`,
        { method: "DELETE" },
      );
      addToast("success", "维度组已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (error: any) {
      addToast("error", error.message || "删除失败");
    } finally {
      setDeleteTarget(null);
    }
  };

  const updateOptions = (options: DimensionOption[]) =>
    setEditing({ ...editing!, options });
  const updateMappings = (field_mappings: DimensionFieldMapping[]) =>
    setEditing({ ...editing!, field_mappings });
  const availableFields = useMemo(
    () =>
      new Map(
        classes.map((schemaClass) => [
          schemaClass.id,
          schemaClass.fields || [],
        ]),
      ),
    [classes],
  );
  const metricGroups = useMemo(() => {
    const classById = new Map(
      classes.map((schemaClass) => [schemaClass.id, schemaClass]),
    );
    const groups = new Map<string, Metric[]>();
    for (const metric of metrics) {
      const classId = metric.target_class || "__unassigned__";
      groups.set(classId, [...(groups.get(classId) || []), metric]);
    }
    return [...groups.entries()]
      .sort(([left], [right]) => left.localeCompare(right, "zh-Hans-CN"))
      .map(([classId, groupMetrics]) => ({
        classId,
        classLabel:
          classId === "__unassigned__"
            ? "未指定来源类"
            : classById.get(classId)?.name_cn || classId,
        metrics: groupMetrics,
      }));
  }, [classes, metrics]);
  const affectedMetricGroups = useMemo(() => {
    const knownMetricIds = new Set(metrics.map((metric) => metric.id));
    return new Map(
      groups.map((dimensionGroup) => {
        const selectedIds = new Set(dimensionGroup.metric_ids || []);
        const grouped = metricGroups
          .map((metricGroup) => ({
            classId: metricGroup.classId,
            classLabel: metricGroup.classLabel,
            metricNames: metricGroup.metrics
              .filter((metric) => selectedIds.has(metric.id))
              .map((metric) => metric.name),
          }))
          .filter((metricGroup) => metricGroup.metricNames.length > 0);
        const missingMetricIds = [...selectedIds].filter(
          (metricId) => !knownMetricIds.has(metricId),
        );
        if (missingMetricIds.length) {
          grouped.push({
            classId: "__missing__",
            classLabel: "未找到的 Metric",
            metricNames: missingMetricIds,
          });
        }
        return [dimensionGroup.id, grouped] as const;
      }),
    );
  }, [groups, metricGroups, metrics]);
  const filtered = groups.filter((group) =>
    [group.id, group.name, group.description]
      .join(" ")
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  if (!activeScenario)
    return (
      <>
        <h2 className="mb-4 text-lg font-semibold text-slate-800">
          分析维度组
        </h2>
        <ScenarioSelector />
      </>
    );

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">分析维度组</h2>
          <p className="mt-1 text-xs text-slate-500">
            统一维护业务维度、选项、字段映射及受影响指标。
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={() => setEditing(emptyGroup(activeScenario))}
        >
          <Plus className="h-4 w-4" /> 新增维度组
        </button>
      </div>
      <ScenarioSelector />
      <input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="搜索维度组..."
        className="mb-4 w-full"
      />
      {loading ? (
        <LoadingSpinner />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="🧩"
          title="暂无分析维度组"
          description="创建业务维度组，统一维护选项和字段映射。"
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table min-w-[820px]">
              <colgroup>
                <col className="w-40" />
                <col className="w-48" />
                <col className="w-48" />
                <col className="w-24" />
                <col className="w-24" />
                <col className="w-24" />
              </colgroup>
              <thead>
                <tr>
                  <th>维度组</th>
                  <th>类型 / 策略</th>
                  <th>选项</th>
                  <th className="whitespace-nowrap">映射</th>
                  <th className="whitespace-nowrap">状态</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((group) => (
                  <tr key={group.id}>
                  <td>
                    <div className="font-medium text-slate-700">
                      {group.name}
                    </div>
                    <div className="text-xs text-slate-400">{group.id}</div>
                  </td>
                  <td className="text-xs">
                    {group.group_type}
                    <br />
                    {group.clarification_policy}
                  </td>
                  <td>
                    {group.options.map((option) => (
                      <span
                        key={option.value}
                        className="mr-1 inline-block rounded bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-700"
                      >
                        {option.label}
                        {option.is_default ? " · 默认" : ""}
                      </span>
                    ))}
                  </td>
                  <td className="text-xs text-slate-500">
                    {group.field_mappings.length} 条
                  </td>
                  <td>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${group.status === "approved" ? "bg-emerald-50 text-emerald-700" : group.status === "deprecated" ? "bg-slate-100 text-slate-500" : "bg-amber-50 text-amber-700"}`}
                    >
                      {group.status === "approved"
                        ? "已通过"
                        : group.status === "deprecated"
                          ? "已废弃"
                          : "草稿"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap text-right">
                    {(affectedMetricGroups.get(group.id) || []).length > 0 && (
                      <button
                        className="btn-ghost text-xs"
                        onClick={() => setViewingAffectedMetrics(group)}
                      >
                        查看受影响指标
                      </button>
                    )}
                    <button
                      className="btn-ghost text-xs"
                      onClick={() => setEditing(group)}
                    >
                      编辑
                    </button>
                    <button
                      className="btn-ghost text-xs text-rose-500"
                      onClick={() => setDeleteTarget(group.id)}
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
        isOpen={!!viewingAffectedMetrics}
        onClose={() => setViewingAffectedMetrics(null)}
        width="max-w-[calc(100vw-2rem)] md:max-w-[42rem]"
        title={`受影响指标 · ${viewingAffectedMetrics?.name || ""}`}
        footer={
          <button
            className="btn-outline"
            onClick={() => setViewingAffectedMetrics(null)}
          >
            关闭
          </button>
        }
      >
        <div className="space-y-3">
          {(affectedMetricGroups.get(viewingAffectedMetrics?.id || "") || []).map(
            (metricGroup) => (
              <section
                key={metricGroup.classId}
                className="rounded-lg border border-slate-200 p-3"
              >
                <h3 className="text-sm font-medium text-slate-700">
                  {metricGroup.classLabel}
                  {metricGroup.classId !== "__unassigned__" &&
                    metricGroup.classId !== "__missing__" && (
                      <span className="ml-1.5 text-xs font-normal text-slate-400">
                        {metricGroup.classId}
                      </span>
                    )}
                </h3>
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-slate-600">
                  {metricGroup.metricNames.map((metricName) => (
                    <li key={metricName}>{metricName}</li>
                  ))}
                </ul>
              </section>
            ),
          )}
        </div>
      </Modal>

      <Modal
        isOpen={!!editing}
        onClose={() => setEditing(null)}
        width="max-w-[calc(100vw-2rem)] md:max-w-[60vw]"
        title={
          groups.some((group) => group.id === editing?.id)
            ? "编辑分析维度组"
            : "新增分析维度组"
        }
        footer={
          <>
            <button className="btn-outline" onClick={() => setEditing(null)}>
              取消
            </button>
            <button className="btn-primary" onClick={save}>
              保存
            </button>
          </>
        }
      >
        {editing && (
          <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
            <div className="grid grid-cols-2 gap-4">
              <label className="text-xs text-slate-600">
                ID
                <input
                  disabled={groups.some((group) => group.id === editing.id)}
                  value={editing.id || ""}
                  onChange={(event) =>
                    setEditing({ ...editing, id: event.target.value })
                  }
                  className="mt-1 w-full"
                  placeholder="time_granularity"
                />
              </label>
              <label className="text-xs text-slate-600">
                名称
                <input
                  value={editing.name || ""}
                  onChange={(event) =>
                    setEditing({ ...editing, name: event.target.value })
                  }
                  className="mt-1 w-full"
                  placeholder="时间粒度"
                />
              </label>
            </div>
            <label className="block text-xs text-slate-600">
              说明
              <textarea
                value={editing.description || ""}
                onChange={(event) =>
                  setEditing({ ...editing, description: event.target.value })
                }
                className="mt-1 w-full"
                rows={2}
              />
            </label>
            <div className="grid grid-cols-2 gap-4">
              <label className="text-xs text-slate-600">
                类型
                <select
                  value={editing.group_type || "categorical"}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      group_type: event.target
                        .value as DimensionGroup["group_type"],
                    })
                  }
                  className="mt-1 w-full"
                >
                  <option value="time">时间</option>
                  <option value="categorical">分类</option>
                  <option value="hierarchy">层级</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">
                关联 Concept
                <select
                  value={editing.concept_id || ""}
                  onChange={(event) =>
                    setEditing({ ...editing, concept_id: event.target.value })
                  }
                  className="mt-1 w-full"
                >
                  <option value="">不关联</option>
                  {concepts.map((concept) => (
                    <option key={concept.id} value={concept.id}>
                      {concept.name} ({concept.id})
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-600">
                澄清策略
                <select
                  value={normalizeClarificationPolicy(editing.clarification_policy)}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      clarification_policy: event.target
                        .value as DimensionGroup["clarification_policy"],
                    })
                  }
                  className="mt-1 w-full"
                >
                  <option value="auto_fill">优先自动填充</option>
                  <option value="ask_when_ambiguous">仅歧义时询问</option>
                  <option value="always_ask">始终询问</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">
                状态
                <select
                  value={editing.status || "draft"}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      status: event.target.value as DimensionGroup["status"],
                    })
                  }
                  className="mt-1 w-full"
                >
                  <option value="draft">草稿</option>
                  <option value="approved">已通过</option>
                  <option value="deprecated">已废弃</option>
                </select>
              </label>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={!!editing.is_required}
                onChange={(event) =>
                  setEditing({ ...editing, is_required: event.target.checked })
                }
              />{" "}
              这是查询所需的必选分析维度组
            </label>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-700">
                  业务选项
                </h3>
                <button
                  className="btn-outline text-xs"
                  onClick={() =>
                    updateOptions([
                      ...(editing.options || []),
                      emptyOption((editing.options || []).length),
                    ])
                  }
                >
                  <Plus className="h-3 w-3" /> 添加
                </button>
              </div>
              {(editing.options || []).map((option, index) => (
                <div
                  key={index}
                  className="mb-2 grid grid-cols-[1fr_1.4fr_1.4fr_auto_auto] items-center gap-2 rounded border border-slate-100 p-2"
                >
                  <input
                    value={option.value}
                    onChange={(event) =>
                      updateOptions(
                        (editing.options || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, value: event.target.value }
                            : item,
                        ),
                      )
                    }
                    placeholder="稳定值，如 ap_month"
                  />
                  <input
                    value={option.label}
                    onChange={(event) =>
                      updateOptions(
                        (editing.options || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, label: event.target.value }
                            : item,
                        ),
                      )
                    }
                    placeholder="业务展示名称"
                  />
                  <input
                    value={option.aliases.join("，")}
                    onChange={(event) =>
                      updateOptions(
                        (editing.options || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                aliases: splitAliases(event.target.value),
                              }
                            : item,
                        ),
                      )
                    }
                    placeholder="同义词，逗号分隔"
                  />
                  <label className="whitespace-nowrap text-xs">
                    <input
                      type="radio"
                      name="default-option"
                      checked={option.is_default}
                      onChange={() =>
                        updateOptions(
                          (editing.options || []).map((item, itemIndex) => ({
                            ...item,
                            is_default: itemIndex === index,
                          })),
                        )
                      }
                    />{" "}
                    默认
                  </label>
                  <button
                    className="text-rose-500"
                    onClick={() =>
                      updateOptions(
                        (editing.options || []).filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      )
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-700">
                  字段映射
                </h3>
                <button
                  className="btn-outline text-xs"
                  onClick={() =>
                    updateMappings([
                      ...(editing.field_mappings || []),
                      {
                        option_value: editing.options?.[0]?.value || "",
                        class_id: "",
                        field_name: "",
                        display_name: "",
                        priority: (editing.field_mappings || []).length,
                      },
                    ])
                  }
                >
                  <Plus className="h-3 w-3" /> 添加
                </button>
              </div>
              {(editing.field_mappings || []).map((mapping, index) => (
                <div
                  key={index}
                  className="mb-2 grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 rounded border border-slate-100 p-2"
                >
                  <select
                    value={mapping.option_value}
                    onChange={(event) =>
                      updateMappings(
                        (editing.field_mappings || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, option_value: event.target.value }
                            : item,
                        ),
                      )
                    }
                  >
                    <option value="">选择业务选项</option>
                    {(editing.options || []).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={mapping.class_id}
                    onChange={(event) =>
                      updateMappings(
                        (editing.field_mappings || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                class_id: event.target.value,
                                field_name: "",
                              }
                            : item,
                        ),
                      )
                    }
                  >
                    <option value="">选择类</option>
                    {classes.map((schemaClass) => (
                      <option key={schemaClass.id} value={schemaClass.id}>
                        {schemaClass.name_cn || schemaClass.id} (
                        {schemaClass.id})
                      </option>
                    ))}
                  </select>
                  <select
                    value={mapping.field_name}
                    onChange={(event) =>
                      updateMappings(
                        (editing.field_mappings || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, field_name: event.target.value }
                            : item,
                        ),
                      )
                    }
                  >
                    <option value="">选择字段</option>
                    {(availableFields.get(mapping.class_id) || []).map(
                      (field) => (
                        <option key={field.name} value={field.name}>
                          {field.name_cn || field.name} ({field.name})
                        </option>
                      ),
                    )}
                  </select>
                  <input
                    value={mapping.display_name}
                    onChange={(event) =>
                      updateMappings(
                        (editing.field_mappings || []).map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, display_name: event.target.value }
                            : item,
                        ),
                      )
                    }
                    placeholder="映射展示名（可选）"
                  />
                  <button
                    className="text-rose-500"
                    onClick={() =>
                      updateMappings(
                        (editing.field_mappings || []).filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      )
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-slate-700">
                  受影响指标
                  <span className="ml-1.5 text-xs font-normal text-slate-400">
                    已选 {(editing.metric_ids || []).length} / {metrics.length}
                  </span>
                </h3>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-outline text-xs"
                    disabled={!metrics.length || (editing.metric_ids || []).length === metrics.length}
                    onClick={() =>
                      setEditing({
                        ...editing,
                        metric_ids: metrics.map((metric) => metric.id),
                      })
                    }
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    className="btn-outline text-xs"
                    disabled={!(editing.metric_ids || []).length}
                    onClick={() =>
                      setEditing({ ...editing, metric_ids: [] })
                    }
                  >
                    取消全选
                  </button>
                </div>
              </div>
              <div className="space-y-3">
                {metricGroups.map((metricGroup) => {
                  const groupMetricIds = metricGroup.metrics.map(
                    (metric) => metric.id,
                  );
                  const selectedMetricIds = editing.metric_ids || [];
                  const selectedCount = groupMetricIds.filter((id) =>
                    selectedMetricIds.includes(id),
                  ).length;
                  return (
                    <div
                      key={metricGroup.classId}
                      className="rounded border border-slate-200 bg-slate-50/60 p-3"
                    >
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-xs font-medium text-slate-700">
                            {metricGroup.classLabel}
                            {metricGroup.classId !== "__unassigned__" && (
                              <span className="ml-1 text-slate-400">
                                ({metricGroup.classId})
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-slate-400">
                            已选 {selectedCount} / {groupMetricIds.length}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <button
                            type="button"
                            className="btn-outline text-xs"
                            disabled={selectedCount === groupMetricIds.length}
                            onClick={() =>
                              setEditing({
                                ...editing,
                                metric_ids: Array.from(
                                  new Set([
                                    ...selectedMetricIds,
                                    ...groupMetricIds,
                                  ]),
                                ),
                              })
                            }
                          >
                            全选本组
                          </button>
                          <button
                            type="button"
                            className="btn-outline text-xs"
                            disabled={selectedCount === 0}
                            onClick={() =>
                              setEditing({
                                ...editing,
                                metric_ids: selectedMetricIds.filter(
                                  (id) => !groupMetricIds.includes(id),
                                ),
                              })
                            }
                          >
                            取消本组
                          </button>
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        {metricGroup.metrics.map((metric) => (
                          <label
                            key={metric.id}
                            className="flex items-center gap-2 rounded border border-slate-100 bg-white px-2 py-1.5 text-xs"
                          >
                            <input
                              type="checkbox"
                              checked={selectedMetricIds.includes(metric.id)}
                              onChange={(event) =>
                                setEditing({
                                  ...editing,
                                  metric_ids: event.target.checked
                                    ? [
                                        ...selectedMetricIds,
                                        metric.id,
                                      ]
                                    : selectedMetricIds.filter(
                                        (id) => id !== metric.id,
                                      ),
                                })
                              }
                            />
                            {metric.name}{" "}
                            <span className="text-slate-400">({metric.id})</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        )}
      </Modal>
      <ConfirmDialog
        isOpen={!!deleteTarget}
        title="删除维度组"
        message="将同时移除其选项、字段映射和指标绑定，确定继续？"
        onConfirm={remove}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
