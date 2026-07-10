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
import type { GlossaryTerm } from "@/lib/types";

const normalizeAliases = (aliases: unknown): string[] => {
  const values = Array.isArray(aliases) ? aliases : [aliases];
  return values
    .flatMap((value) => String(value || "").split(/[,，、;；\r\n]+/))
    .map((value) => value.trim())
    .filter(Boolean);
};

export default function GlossaryManager() {
  const { activeScenario, addToast } = useApp();
  const api = useApi();
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editTerm, setEditTerm] = useState<Partial<GlossaryTerm> | null>(null);
  const [aliasText, setAliasText] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const cacheKey = `glossary:${activeScenario}`;

  const load = async (force = false) => {
    if (!activeScenario) return;
    if (!force) {
      const cached = getCacheData<GlossaryTerm[]>(cacheKey);
      if (cached) {
        setTerms(cached);
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    try {
      const d = await api(`/api/scenarios/${activeScenario}/glossary`);
      const data = d || [];
      setTerms(data);
      setCacheData(cacheKey, data);
    } catch {
      addToast("error", "加载专用名称失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeScenario) load();
  }, [activeScenario]);

  const save = async () => {
    if (!editTerm?.term) {
      addToast("warning", "术语必填");
      return;
    }
    const isEdit = !!editTerm.id;
    const payload = {
      ...editTerm,
      aliases: normalizeAliases(aliasText),
    };
    try {
      const idSuffix = isEdit ? `/${editTerm.id}` : "";
      await api(`/api/scenarios/${activeScenario}/glossary${idSuffix}`, {
        method: isEdit ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      addToast("success", isEdit ? "术语已更新" : "术语已创建");
      setIsModalOpen(false);
      setEditTerm(null);
      setAliasText("");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "保存失败");
    }
  };

  const remove = async (id: string) => {
    try {
      await api(`/api/scenarios/${activeScenario}/glossary/${id}`, {
        method: "DELETE",
      });
      addToast("success", "术语已删除");
      invalidateCache(cacheKey);
      load(true);
    } catch (e: any) {
      addToast("error", e.message || "删除失败");
    }
  };

  const filtered = terms.filter(
    (t) =>
      !search ||
      t.term.toLowerCase().includes(search.toLowerCase()) ||
      normalizeAliases(t.aliases).some((a) =>
        a.toLowerCase().includes(search.toLowerCase()),
      ),
  );

  if (!activeScenario)
    return (
      <div>
        <h2 className="mb-4 text-lg font-semibold text-slate-800">专用名称</h2>
        <ScenarioSelector />
      </div>
    );

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">专用名称</h2>
        <button
          onClick={() => {
            setEditTerm({
              scenario_id: activeScenario,
              term: "",
              aliases: [],
              description: "",
            });
            setAliasText("");
            setIsModalOpen(true);
          }}
          className="btn-primary"
        >
          + 新增术语
        </button>
      </div>
      <ScenarioSelector />
      <div className="mb-4">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索术语或别名..."
        />
      </div>

      {loading && <LoadingSpinner />}
      {!loading && filtered.length === 0 && (
        <EmptyState
          icon="📖"
          title="暂无专用名称"
          description="添加业务术语和别名，提升AI理解能力"
        />
      )}
      {!loading && filtered.length > 0 && (
        <div className="card overflow-hidden">
          <table className="data-table">
            <thead>
              <tr>
                <th>术语</th>
                <th>别名</th>
                <th>描述</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.id}>
                  <td className="font-medium text-slate-700">{t.term}</td>
                  <td className="text-xs text-slate-500">
                    {normalizeAliases(t.aliases).join(", ")}
                  </td>
                  <td className="max-w-xs truncate text-slate-500">
                    {t.description}
                  </td>
                  <td className="text-right">
                    <button
                      onClick={() => {
                        setEditTerm({
                          ...t,
                          aliases: normalizeAliases(t.aliases),
                        });
                        setAliasText(normalizeAliases(t.aliases).join(", "));
                        setIsModalOpen(true);
                      }}
                      className="btn-ghost text-xs"
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => setDeleteTarget(t.id)}
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

      <Modal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setEditTerm(null);
          setAliasText("");
        }}
        title={editTerm?.id ? "编辑术语" : "新增术语"}
        footer={
          <>
            <button
              onClick={() => {
                setIsModalOpen(false);
                setEditTerm(null);
                setAliasText("");
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
          <div>
            <label
              htmlFor="glossary-field-1"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              术语
            </label>
            <input
              id="glossary-field-1"
              value={editTerm?.term || ""}
              onChange={(e) =>
                setEditTerm({ ...editTerm!, term: e.target.value })
              }
              className="w-full"
              placeholder="如：GMV"
            />
          </div>
          <div>
            <label
              htmlFor="glossary-field-2"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              别名 (逗号分隔)
            </label>
            <input
              id="glossary-field-2"
              value={aliasText}
              onChange={(e) => setAliasText(e.target.value)}
              className="w-full"
              placeholder="成交总额, 总交易额"
            />
          </div>
          <div>
            <label
              htmlFor="glossary-field-3"
              className="mb-1.5 block text-xs font-medium text-slate-500"
            >
              描述
            </label>
            <textarea
              id="glossary-field-3"
              value={editTerm?.description || ""}
              onChange={(e) =>
                setEditTerm({ ...editTerm!, description: e.target.value })
              }
              className="w-full"
              rows={3}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        isOpen={!!deleteTarget}
        title="删除术语"
        message="确定要删除此术语吗？"
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
