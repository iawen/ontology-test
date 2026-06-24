"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/contexts/AppContext";
import { useApi } from "@/hooks/useApi";
import { getCacheData, setCacheData, invalidateCache } from "@/lib/cache";
import Modal from "@/components/ui/Modal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ScenarioSelector from "@/components/ScenarioSelector";
import type { Skill } from "@/lib/types";

export default function SkillsManager() {
  const { token, activeScenario, addToast } = useApp();
  const api = useApi(token);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editSkill, setEditSkill] = useState<Partial<Skill> | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const cacheKey = `skills:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) {
      const cached = getCacheData<Skill[]>(cacheKey);
      if (cached) {
        setSkills(cached);
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    try {
      const d = await api(`/api/admin/scenarios/${activeScenario}/skills`);
      const data = d || [];
      setSkills(data);
      setCacheData(cacheKey, data);
    } catch {
      addToast("error", "加载技能包失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeScenario) load();
  }, [activeScenario]);

  const save = async () => {
    if (!editSkill?.name) {
      addToast("warning", "名称必填");
      return;
    }
    try {
      await api(
        `/api/admin/scenarios/${activeScenario}/skills${isEditing ? `/${editSkill.id}` : ""}`,
        { method: isEditing ? "PUT" : "POST", body: JSON.stringify(editSkill) },
      );

      addToast("success", isEditing ? "技能已更新" : "技能已创建");
      setIsModalOpen(false);
      setEditSkill(null);
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const remove = async (id: string) => {
    try {
      await api(`/api/admin/scenarios/${activeScenario}/skills/${id}`, {
        method: "DELETE",
      });
      addToast("success", "技能已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "删除失败");
    }
  };

  const toggleSkill = async (s: Skill) => {
    await api(`/api/admin/scenarios/${activeScenario}/swicth/${s.id}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: !s.is_active }),
    });
    load(true);
  };

  const filtered = skills.filter(
    (s) => !search || s.name.toLowerCase().includes(search.toLowerCase()),
  );

  if (!activeScenario)
    return (
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-4">技能包</h2>
        <ScenarioSelector />
      </div>
    );

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">技能包</h2>
        <button
          onClick={() => {
            setEditSkill({
              scenario_id: activeScenario,
              name: "",
              description: "",
              trigger_condition: "",
              content: "",
              is_active: 1,
              sort_order: 0,
            });
            setIsEditing(false);
            setIsModalOpen(true);
          }}
          className="btn-primary"
        >
          + 新增技能
        </button>
      </div>
      <ScenarioSelector />
      <div className="mb-4">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索技能..."
        />
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="⚡"
          title="暂无技能包"
          description="创建技能包来增强AI的分析能力"
        />
      ) : (
        <div className="grid gap-3">
          {filtered
            .sort((a, b) => a.sort_order - b.sort_order)
            .map((s) => (
              <div key={s.id} className="card p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium text-slate-700">
                        {s.name}
                      </span>
                      {s.is_active ? (
                        <span className="text-[10px] bg-emerald-50 text-emerald-600 px-1.5 py-0.5 rounded">
                          启用
                        </span>
                      ) : (
                        <span className="text-[10px] bg-slate-100 text-slate-400 px-1.5 py-0.5 rounded">
                          禁用
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-500">{s.description}</p>
                    {s.trigger_condition && (
                      <p className="text-xs text-slate-400 mt-1">
                        触发: {s.trigger_condition}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => toggleSkill(s)}
                      className={s.is_active ? "btn-outline text-xs text-green-500" : "btn-outline text-xs text-slate-500"} 
                    >
                      {s.is_active ? "停用" : "启用"}
                    </button>
                    <button
                      onClick={() => {
                        setEditSkill(s);
                        setIsEditing(true);
                        setIsModalOpen(true);
                      }}
                      className="btn-ghost text-xs"
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => setDeleteTarget(s.id)}
                      className="btn-ghost text-xs text-red-500"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </div>
            ))}
        </div>
      )}

      <Modal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setEditSkill(null);
        }}
        title={editSkill?.id ? "编辑技能" : "新增技能"}
        footer={
          <>
            <button
              onClick={() => {
                setIsModalOpen(false);
                setEditSkill(null);
              }}
              className="btn-outline"
            >
              取消
            </button>
            <button
              onClick={save}
              className="btn-primary"
            >
              保存
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">
                ID
              </label>
              <input
                value={editSkill?.id || ""}
                onChange={(e) =>
                  setEditSkill({ ...editSkill!, id: e.target.value })
                }
                className="w-full"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">
                名称
              </label>
              <input
                value={editSkill?.name || ""}
                onChange={(e) =>
                  setEditSkill({ ...editSkill!, name: e.target.value })
                }
                className="w-full"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">
                排序
              </label>
              <input
                type="number"
                value={editSkill?.sort_order || 0}
                onChange={(e) =>
                  setEditSkill({
                    ...editSkill!,
                    sort_order: Number(e.target.value),
                  })
                }
                className="w-full"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">
              描述
            </label>
            <input
              value={editSkill?.description || ""}
              onChange={(e) =>
                setEditSkill({ ...editSkill!, description: e.target.value })
              }
              className="w-full"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">
              触发条件
            </label>
            <textarea
              value={editSkill?.trigger_condition || ""}
              onChange={(e) =>
                setEditSkill({
                  ...editSkill!,
                  trigger_condition: e.target.value,
                })
              }
              className="w-full"
              placeholder="如：用户询问销售趋势时"
              rows={2}
            />
          </div>
          <div>
            <label className="text-xs text-slate-500 font-medium block mb-1.5">
              技能内容 (Markdown)
            </label>
            <textarea
              value={editSkill?.content || ""}
              onChange={(e) =>
                setEditSkill({ ...editSkill!, content: e.target.value })
              }
              className="w-full font-mono"
              rows={10}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title="删除技能"
        message="确定要删除此技能包吗？"
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
