"""
Schema Optimizer v4 - 核心优化器 (BM25 检索版)
=============================================
基于 v3.1 改进，核心变更：
  1. 使用 BM25 关键词检索替代 Embedding 向量检索
  2. 移除所有 Embedding 模型依赖，大幅降低资源消耗
  3. 保留所有其他改进（分批优化、全局校正、质量评估、智能重试等）
"""

import os
import json
import uuid
import asyncio
import hashlib
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime

from core.llm.chat_model import get_async_client, get_model_name
from core.db.db import get_db
from pydantic import ValidationError
from core.ontology.ontology_asset_validator import validate_schema_assets
from core.ontology.schema_context import load_schema_reference_context
from core.ontology.prompt import (
    build_global_correction_prompt,
    build_optimization_batch_prompt,
    build_quality_assessment_prompt,
    build_schema_optimization_retry_prompt,
)

from core.models.schema_model import (
    OptimizationBatchResult,
    GlobalCorrectionResult,
    OptimizationDiff,
    QualityAssessmentResult,
    ClassOptimization,
    MetricOptimization,
    DimensionGroupOptimization,
    RelationshipOptimization,
    ConceptOptimization,
)
from tools.document_indexer import DocumentIndex, parse_document


# ============================================================
# 配置常量
# ============================================================

BATCH_MAX_CLASSES = 6
BATCH_MAX_METRICS = 15
BATCH_MAX_RELATIONSHIPS = 10
BATCH_MAX_CONCEPTS = 10
BATCH_MAX_DIMENSION_GROUPS = 10
DOC_CONTEXT_LIMIT = 8000
LLM_RETRY_MAX = 2


# ============================================================
# 辅助函数（与 v3.1 相同）
# ============================================================

def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _json_obj(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()} if row else {}


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        import re
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            candidate = cleaned[start:end + 1]
            try:
                return json.loads(candidate)
            except:
                candidate = re.sub(r",\s*}", "}", candidate)
                candidate = re.sub(r",\s*]", "]", candidate)
                try:
                    return json.loads(candidate)
                except:
                    pass
    return {}


async def _emit_progress(callback: Optional[Callable], **status):
    if not callback:
        return
    result = callback(status)
    if asyncio.iscoroutine(result):
        await result


# ============================================================
# 主优化器（BM25 版本）
# ============================================================

class SchemaOptimizer:
    """Schema 优化器 - 使用 BM25 检索"""

    def __init__(self, scenario_id: str):
        self.scenario_id = scenario_id
        self.doc_index = DocumentIndex()  # 不再需要 embedding 初始化
        self.schema_reference_context = {}

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------

    async def optimize(
        self,
        document_paths: Optional[List[str]] = None,
        incremental: bool = True,
        target_class_ids: Optional[List[str]] = None,
        progress_callback: Optional[Callable] = None,
        enable_quality_assessment: bool = True,
    ) -> dict:
        run_id = str(uuid.uuid4())[:8]
        await _emit_progress(progress_callback, running=True, phase="init", progress=5, total=100, message="初始化优化任务")

        self._create_run_record(run_id)

        try:
            await _emit_progress(progress_callback, phase="indexing", progress=10, total=100, message="解析业务文档（BM25索引）")
            if document_paths:
                self._index_documents(document_paths, progress_callback)

            await _emit_progress(progress_callback, phase="loading", progress=20, total=100, message="加载当前 Schema 资产")
            # 人工已审核（批准或驳回）的资产属于受保护口径，任何优化模式都不可改写。
            classes, relationships, metrics, concepts, dimension_groups = self._load_schema_assets(
                incremental,
                target_class_ids,
                exclude_reviewed=True,
            )
            # 已批准维度组不参与改写，但必须作为 Metric 可绑定的既有语义目录。
            _, _, _, _, all_dimension_groups = self._load_schema_assets(
                incremental=False,
                target_class_ids=None,
            )
            self.schema_reference_context = load_schema_reference_context(self.scenario_id)

            if not classes and not relationships and not metrics and not concepts and not dimension_groups:
                await _emit_progress(progress_callback, running=False, phase="done", progress=100, total=100, message="无可优化资产")
                return {"status": "skipped", "run_id": run_id, "message": "无可优化资产"}

            await _emit_progress(progress_callback, phase="class_optimizing", progress=30, total=100, message="阶段一：优化并去重 Class")
            class_results = await self._run_batch_optimization(
                classes, [], [], [], [], progress_callback, stage="classes"
            )
            class_data = self._merge_results(class_results, GlobalCorrectionResult())
            class_data = self._deduplicate_optimization_assets(class_data)

            await _emit_progress(progress_callback, phase="dimension_optimizing", progress=45, total=100, message="阶段二：识别新增维度组并优化现有维度组")
            dimension_results = await self._run_batch_optimization(
                [], [], [], [], dimension_groups, progress_callback,
                stage="dimension_groups",
                stage_context={
                    "classes": class_data.get("classes", []),
                    "existing_dimension_groups": all_dimension_groups,
                },
            )
            dimension_data = self._merge_results(dimension_results, GlobalCorrectionResult())
            dimension_data = self._deduplicate_optimization_assets(dimension_data)
            available_dimension_groups = {
                item.get("id"): item for item in all_dimension_groups if item.get("id")
            }
            available_dimension_groups.update({
                item.get("id"): item for item in dimension_data.get("dimension_groups", []) if item.get("id")
            })

            await _emit_progress(progress_callback, phase="metric_optimizing", progress=55, total=100, message="阶段三：基于 Class 与维度组优化 Relationship 和结构化 Metric")
            semantic_results = await self._run_batch_optimization(
                [], relationships, metrics, [], [], progress_callback,
                stage="semantics",
                stage_context={
                    "classes": class_data.get("classes", []),
                    "dimension_groups": list(available_dimension_groups.values()),
                },
            )
            semantic_data = self._merge_results(semantic_results, GlobalCorrectionResult())
            semantic_data = self._deduplicate_optimization_assets(semantic_data)

            await _emit_progress(progress_callback, phase="concept_optimizing", progress=67, total=100, message="阶段四：基于已确定资产优化 Concept 层级")
            concept_results = await self._run_batch_optimization(
                [], [], [], concepts, [], progress_callback,
                stage="concepts",
                stage_context={
                    "classes": class_data.get("classes", []),
                    "dimension_groups": list(available_dimension_groups.values()),
                    "relationships": semantic_data.get("relationships", []),
                    "metrics": semantic_data.get("metrics", []),
                },
            )
            batch_results = [*class_results, *dimension_results, *semantic_results, *concept_results]

            await _emit_progress(progress_callback, phase="global_correcting", progress=65, total=100, message="阶段二：全局校正")
            global_result = await self._run_global_correction(batch_results, progress_callback)

            merged = self._merge_results(batch_results, global_result)
            merged = self._deduplicate_optimization_assets(merged)
            merged = self._filter_protected_assets(merged)
            merged = self._validate_optimization_assets(merged)

            await _emit_progress(progress_callback, phase="diffing", progress=78, total=100, message="生成差异报告")
            diff = self._generate_diff(classes, metrics, relationships, concepts, merged)

            quality_result = None
            if enable_quality_assessment:
                await _emit_progress(progress_callback, phase="quality", progress=85, total=100, message="质量评估")
                quality_result = await self._run_quality_assessment(merged, diff, progress_callback)

            await _emit_progress(progress_callback, phase="applying", progress=92, total=100, message="应用优化结果")
            applied = self._apply_optimization(merged)

            quality_data = quality_result.model_dump() if quality_result else {}
            self._update_run_success(run_id, diff, applied, quality_data)

            await _emit_progress(
                progress_callback, running=False, phase="done", progress=100, total=100,
                message="Schema 优化完成", run_id=run_id,
                result={"diff": diff.model_dump(), "applied": applied, "quality": quality_data}
            )
            return {
                "status": "success",
                "run_id": run_id,
                "diff": diff.model_dump(),
                "applied": applied,
                "quality": quality_data
            }

        except Exception as exc:
            self._update_run_failure(run_id, str(exc))
            await _emit_progress(progress_callback, running=False, phase="error", progress=100, total=100, message=f"优化失败: {exc}")
            raise

    # --------------------------------------------------------
    # 文档索引（使用 BM25）
    # --------------------------------------------------------

    def _index_documents(self, paths: List[str], progress_callback=None):
        total = len(paths)
        for idx, path_str in enumerate(paths):
            path = Path(path_str)
            if not path.exists():
                print(f"[Warning] 文件不存在，跳过: {path_str}")
                continue
            chunks = parse_document(path, chunk_size=800, chunk_overlap=150)
            if chunks:
                self.doc_index.add_chunks(chunks)
                print(f"  [Index] {path.name}: {len(chunks)} chunks (BM25)")
            else:
                print(f"  [Warning] {path.name}: 解析失败，无内容")

    # --------------------------------------------------------
    # 资产加载
    # --------------------------------------------------------

    def _load_schema_assets(
        self,
        incremental: bool,
        target_class_ids: Optional[List[str]],
        exclude_reviewed: bool = False,
    ):
        """从数据库加载当前 Schema 资产"""
        conn = get_db()
        sid = self.scenario_id

        class_sql = "SELECT * FROM schema_classes WHERE scenario_id=?"
        if incremental or exclude_reviewed:
            class_sql += " AND is_reviewed IS NOT TRUE AND COALESCE(review_status, 'pending')='pending'"
        if target_class_ids:
            placeholders = ",".join("?" * len(target_class_ids))
            class_sql += f" AND id IN ({placeholders})"
            class_rows = conn.execute(class_sql, (sid, *target_class_ids)).fetchall()
        else:
            class_rows = conn.execute(class_sql, (sid,)).fetchall()
        classes = [_row_to_dict(r) for r in class_rows]

        metric_sql = "SELECT * FROM metrics WHERE scenario_id=?"
        if incremental or exclude_reviewed:
            metric_sql += " AND is_reviewed IS NOT TRUE AND COALESCE(review_status, 'pending')='pending'"
        metric_rows = conn.execute(metric_sql, (sid,)).fetchall()
        metrics = [_row_to_dict(r) for r in metric_rows]
        metric_group_rows = conn.execute(
            "SELECT metric_id, group_id FROM metric_dimension_bindings WHERE scenario_id=?",
            (sid,),
        ).fetchall()
        metric_group_ids: dict[str, list[str]] = {}
        for row in metric_group_rows:
            metric_group_ids.setdefault(row["metric_id"], []).append(row["group_id"])
        for metric in metrics:
            metric["dimension_group_ids"] = metric_group_ids.get(metric["id"], [])

        relationship_sql = "SELECT * FROM schema_relationships WHERE scenario_id=?"
        if incremental or exclude_reviewed:
            relationship_sql += " AND is_reviewed IS NOT TRUE AND COALESCE(review_status, 'pending')='pending'"
        rel_rows = conn.execute(relationship_sql, (sid,)).fetchall()
        relationships = [_row_to_dict(r) for r in rel_rows]

        concept_sql = "SELECT * FROM concepts WHERE scenario_id=?"
        if incremental or exclude_reviewed:
            concept_sql += " AND is_reviewed IS NOT TRUE AND COALESCE(review_status, 'pending')='pending'"
        concept_rows = conn.execute(concept_sql, (sid,)).fetchall()
        concepts = [_row_to_dict(r) for r in concept_rows]

        group_sql = "SELECT * FROM dimension_groups WHERE scenario_id=?"
        if incremental or exclude_reviewed:
            group_sql += " AND status='draft'"
        group_rows = conn.execute(group_sql, (sid,)).fetchall()
        options = conn.execute("SELECT * FROM dimension_group_options WHERE scenario_id=? ORDER BY sort_order", (sid,)).fetchall()
        mappings = conn.execute("SELECT * FROM dimension_field_mappings WHERE scenario_id=? ORDER BY priority", (sid,)).fetchall()
        options_by_group: dict[str, list[dict]] = {}
        for row in options:
            item = _row_to_dict(row)
            options_by_group.setdefault(item["group_id"], []).append({
                **item, "aliases": _json_list(item.get("aliases"))
            })
        mappings_by_group: dict[str, list[dict]] = {}
        for row in mappings:
            item = _row_to_dict(row)
            mappings_by_group.setdefault(item["group_id"], []).append(item)
        dimension_groups = [
            {**_row_to_dict(row), "options": options_by_group.get(row["id"], []), "field_mappings": mappings_by_group.get(row["id"], [])}
            for row in group_rows
        ]

        conn.close()
        return classes, relationships, metrics, concepts, dimension_groups

    # --------------------------------------------------------
    # 阶段一：分批优化（修复孤立资产 Bug）
    # --------------------------------------------------------

    def _build_batches(self, classes, relationships, metrics, concepts, dimension_groups=None) -> List[Dict]:
        """
        按 Class 关联性分批。
        修复：孤立资产不再重复添加，各自独立成批。
        """
        batches = []

        # ---- 1. 构建关联索引 ----
        class_to_metrics = {}
        for m in metrics:
            tc = m.get("target_class", "")
            if tc:
                class_to_metrics.setdefault(tc, []).append(m)

        class_to_rels = {}
        for r in relationships:
            src = r.get("source", "")
            tgt = r.get("target", "")
            if src:
                class_to_rels.setdefault(src, []).append(r)
            if tgt and tgt != src:
                class_to_rels.setdefault(tgt, []).append(r)

        class_to_concepts = {}
        for c in concepts:
            rc = c.get("related_class", "")
            if rc:
                class_to_concepts.setdefault(rc, []).append(c)

        batched_metric_ids = set()
        batched_rel_ids = set()
        batched_concept_ids = set()

        # ---- 2. 按 Class 分批 ----
        current_batch = {"classes": [], "relationships": [], "metrics": [], "concepts": []}

        for cls in classes:
            cid = cls.get("id", "")
            current_batch["classes"].append(cls)

            for m in class_to_metrics.get(cid, []):
                if m["id"] not in batched_metric_ids:
                    current_batch["metrics"].append(m)
                    batched_metric_ids.add(m["id"])

            for r in class_to_rels.get(cid, []):
                rid = r.get("id") or f"rel_{r.get('source')}_{r.get('target')}"
                if rid not in batched_rel_ids:
                    current_batch["relationships"].append(r)
                    batched_rel_ids.add(rid)

            for c in class_to_concepts.get(cid, []):
                if c["id"] not in batched_concept_ids:
                    current_batch["concepts"].append(c)
                    batched_concept_ids.add(c["id"])

            if len(current_batch["classes"]) >= BATCH_MAX_CLASSES or len(current_batch["metrics"]) >= BATCH_MAX_METRICS:
                if any(current_batch.values()):
                    batches.append(current_batch)
                current_batch = {"classes": [], "relationships": [], "metrics": [], "concepts": []}

        if any(current_batch.values()):
            batches.append(current_batch)

        # ---- 3. 孤立资产独立成批（修复重复添加 Bug） ----
        leftover_metrics = [m for m in metrics if m["id"] not in batched_metric_ids]
        leftover_rels = [r for r in relationships if (r.get("id") or f"rel_{r.get('source')}_{r.get('target')}") not in batched_rel_ids]
        leftover_concepts = [c for c in concepts if c["id"] not in batched_concept_ids]

        # 3a. 孤立关系独立成批
        for i in range(0, len(leftover_rels), BATCH_MAX_RELATIONSHIPS):
            batches.append({
                "classes": [],
                "relationships": leftover_rels[i:i + BATCH_MAX_RELATIONSHIPS],
                "metrics": [],
                "concepts": []
            })

        # 3b. 孤立概念独立成批
        for i in range(0, len(leftover_concepts), BATCH_MAX_CONCEPTS):
            batches.append({
                "classes": [],
                "relationships": [],
                "metrics": [],
                "concepts": leftover_concepts[i:i + BATCH_MAX_CONCEPTS]
            })

        # 3c. 孤立指标独立成批
        for i in range(0, len(leftover_metrics), BATCH_MAX_METRICS):
            batches.append({
                "classes": [],
                "relationships": [],
                "metrics": leftover_metrics[i:i + BATCH_MAX_METRICS],
                "concepts": []
            })

        for index in range(0, len(dimension_groups or []), BATCH_MAX_DIMENSION_GROUPS):
            batches.append({
                "classes": [], "relationships": [], "metrics": [], "concepts": [],
                "dimension_groups": (dimension_groups or [])[index:index + BATCH_MAX_DIMENSION_GROUPS],
            })
        return [b for b in batches if any(b.values())]

    async def _run_batch_optimization(
        self,
        classes,
        relationships,
        metrics,
        concepts,
        dimension_groups=None,
        progress_callback=None,
        stage: str = "all",
        stage_context: Optional[Dict] = None,
    ) -> List[OptimizationBatchResult]:
        """执行分批优化"""
        batches = self._build_batches(classes, relationships, metrics, concepts, dimension_groups)
        # 即使当前尚未维护任何维度组，也必须执行一次发现批次：
        # 由业务文档和已确定的 Class 识别可复用的新分析维度组。
        if stage == "dimension_groups":
            if not batches:
                batches = [{
                    "classes": [], "relationships": [], "metrics": [], "concepts": [],
                    "dimension_groups": [], "dimension_discovery": True,
                }]
            else:
                batches[0]["dimension_discovery"] = True
        results = []
        total = len(batches)

        for i, batch in enumerate(batches):
            await _emit_progress(
                progress_callback, phase="batch_optimizing",
                progress=30 + int(35 * (i / max(total, 1))), total=100,
                message=f"阶段一：批次 {i+1}/{total}"
            )

            query = self._build_batch_query(batch, stage_context or {})
            doc_context = self.doc_index.build_context(query, top_k=5, max_chars=DOC_CONTEXT_LIMIT)

            result = await self._call_llm_batch(batch, doc_context, stage, stage_context or {})
            result = self._restrict_result_to_stage(result, stage)
            results.append(result)

        return results

    @staticmethod
    def _restrict_result_to_stage(result: OptimizationBatchResult, stage: str) -> OptimizationBatchResult:
        """忽略模型跨阶段输出，保证资产依赖顺序不能被绕过。"""
        allowed = {
            "classes": {"classes"},
            "dimension_groups": {"dimension_groups"},
            "semantics": {"relationships", "metrics"},
            "concepts": {"concepts"},
        }.get(stage)
        if not allowed:
            return result
        if "classes" not in allowed:
            result.classes = []
        if "relationships" not in allowed:
            result.relationships = []
        if "metrics" not in allowed:
            result.metrics = []
        if "dimension_groups" not in allowed:
            result.dimension_groups = []
        if "concepts" not in allowed:
            result.concepts = []
        return result

    def _build_batch_query(self, batch: Dict, stage_context: Optional[Dict] = None) -> str:
        """构建批次查询文本"""
        parts = []
        for c in batch.get("classes", []):
            parts.append(f"{c.get('name_cn', '')} {c.get('description', '')} {c.get('id', '')}")
        for m in batch.get("metrics", []):
            parts.append(f"{m.get('name', '')} {m.get('description', '')} {json.dumps(m.get('definition', {}), ensure_ascii=False)}")
        for group in batch.get("dimension_groups", []):
            parts.append(f"{group.get('name', '')} {group.get('description', '')} {group.get('id', '')}")
        # 维度发现批次自身没有输入资产，需通过已确定的 Class 形成 BM25 检索词。
        if batch.get("dimension_discovery"):
            for cls in (stage_context or {}).get("classes", []):
                parts.append(f"{cls.get('name_cn', '')} {cls.get('description', '')} {cls.get('id', '')}")
        return " ".join(parts)[:500]

    # --------------------------------------------------------
    # LLM 调用（智能重试：区分语法/语义错误）
    # --------------------------------------------------------

    async def _call_llm_batch(
        self,
        batch: Dict,
        doc_context: str,
        stage: str = "all",
        stage_context: Optional[Dict] = None,
    ) -> OptimizationBatchResult:
        """调用 LLM 进行单批次优化（带智能重试）"""
        prompt = self._build_batch_prompt(batch, doc_context, stage, stage_context or {})

        last_validation_error = None

        for attempt in range(LLM_RETRY_MAX + 1):
            try:
                response = await get_async_client().chat.completions.create(
                    model=get_model_name(),
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=8192,
                )
                raw = response.choices[0].message.content or ""
                data = _extract_json(raw)

                # Pydantic 验证
                result = OptimizationBatchResult(**data)
                return result

            except ValidationError as e:
                last_validation_error = e
                if attempt < LLM_RETRY_MAX:
                    # 智能重试：区分错误类型
                    error_str = str(e)
                    if "validation error" in error_str and "Field required" in error_str:
                        # 语义错误：字段缺失 → 提供更具体的指导
                        prompt = self._build_retry_prompt(prompt, error_str, retry_type="semantic")
                    else:
                        # 语法/类型错误
                        prompt = self._build_retry_prompt(prompt, error_str, retry_type="syntax")
                    continue
                else:
                    print(f"  [Warning] 批次验证失败（重试耗尽）: {e}")
                    return OptimizationBatchResult(summary=f"验证失败: {e}")

            except Exception as e:
                if attempt < LLM_RETRY_MAX:
                    # 网络等临时错误，简单重试
                    print(f"  [Retry] LLM 调用异常，重试 {attempt+1}/{LLM_RETRY_MAX}: {e}")
                    await asyncio.sleep(1)
                    continue
                print(f"  [Error] LLM 调用失败: {e}")
                return OptimizationBatchResult(summary=f"LLM调用失败: {e}")

        return OptimizationBatchResult(summary=f"验证失败: {last_validation_error}")

    def _build_batch_prompt(
        self,
        batch: Dict,
        doc_context: str,
        stage: str = "all",
        stage_context: Optional[Dict] = None,
    ) -> str:
        """构建批次优化提示词"""
        schema_reference = json.dumps(self.schema_reference_context, ensure_ascii=False, indent=2) if self.schema_reference_context else "{}"
        stage_context = stage_context or {}
        stage_rules = {
            "classes": "本阶段仅优化 Class。relationships、metrics、dimension_groups、concepts 必须输出空数组；不得创建或改写任何其它资产。",
            "dimension_groups": "本阶段仅识别、创建或优化分析维度组。Class 已在第一阶段确定；classes、relationships、metrics、concepts 必须输出空数组。若当前批次的 dimension_discovery 为 true，必须根据业务文档和 Class 判断是否缺少新的可复用分析维度组；缺少时新增输出，已有维度组使用原 ID，新维度组使用稳定、语义化的英文下划线 ID。不得为单一 Metric 的临时筛选条件创建维度组。每个字段映射必须引用已有 Class 的逻辑字段。",
            "semantics": "本阶段仅优化 Relationship 和 Metric。Class 与分析维度组均已确定，classes、dimension_groups、concepts 必须输出空数组。Metric 必须采用 V1 structured definition，target_class 必须等于 definition.anchor_class，并且 dimension_group_ids 只能引用前置阶段已确定的维度组。",
            "concepts": "本阶段仅优化 Concept 层级。Class、Relationship、Metric 已确定，classes、relationships、metrics 必须输出空数组；不得重新生成或改写它们。",
        }.get(stage, "仅修改当前批次内的资产。")
        return build_optimization_batch_prompt(
            doc_context=doc_context,
            schema_reference=schema_reference,
            stage_context=json.dumps(stage_context, ensure_ascii=False, indent=2),
            batch=json.dumps(batch, ensure_ascii=False, indent=2),
            stage_rules=stage_rules,
        )

    def _build_retry_prompt(self, original_prompt: str, error: str, retry_type: str = "syntax") -> str:
        """构建自校正重试提示词（区分错误类型）"""
        return build_schema_optimization_retry_prompt(original_prompt, error, retry_type)

    # --------------------------------------------------------
    # 阶段二：全局校正（扩展版）
    # --------------------------------------------------------

    async def _run_global_correction(self, batch_results: List[OptimizationBatchResult], progress_callback=None) -> GlobalCorrectionResult:
        """全局校正：检查命名一致性、关系悬空、概念树完整性、指标口径一致性"""
        # 汇总所有批次结果
        all_classes = []
        all_metrics = []
        all_rels = []
        all_concepts = []
        for r in batch_results:
            all_classes.extend(r.classes)
            all_metrics.extend(r.metrics)
            all_rels.extend(r.relationships)
            all_concepts.extend(r.concepts)

        if len(batch_results) <= 1:
            return GlobalCorrectionResult(
                summary="单批次无需全局校正",
                concept_tree_warnings=[],
                metric_consistency_warnings=[]
            )

        # 构建全局校正提示词（扩展版）
        compressed_classes = [{"id": c.id, "name_cn": c.name_cn} for c in all_classes]
        compressed_metrics = [{"id": m.id, "name": m.name, "target_class": m.target_class, "definition": m.definition} for m in all_metrics]
        compressed_concepts = [{"id": c.id, "name": c.name, "parent_id": c.parent_id, "level": c.level} for c in all_concepts]

        prompt = build_global_correction_prompt(
            classes=json.dumps(compressed_classes, ensure_ascii=False, indent=2),
            metrics=json.dumps(compressed_metrics, ensure_ascii=False, indent=2),
            relationships=json.dumps([r.model_dump() for r in all_rels], ensure_ascii=False, indent=2),
            concepts=json.dumps(compressed_concepts, ensure_ascii=False, indent=2),
        )

        try:
            response = await get_async_client().chat.completions.create(
                model=get_model_name(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4096,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            result = GlobalCorrectionResult(**data)
            # Class → semantic assets → Concept 的依赖关系已经在前置阶段确定；
            # 本阶段只报告全局风险，不能回写任何资产以绕过顺序或保护策略。
            result.class_renames = []
            result.relationship_corrections = []
            result.metric_corrections = []
            result.concept_corrections = []
            return result
        except Exception as e:
            print(f"  [Warning] 全局校正失败: {e}")
            return GlobalCorrectionResult(
                summary=f"全局校正失败: {e}",
                concept_tree_warnings=[],
                metric_consistency_warnings=[]
            )

    # --------------------------------------------------------
    # 质量评估（LLM-as-Judge）
    # --------------------------------------------------------

    async def _run_quality_assessment(self, merged: Dict, diff: OptimizationDiff, progress_callback=None) -> QualityAssessmentResult:
        """使用 LLM 对优化结果进行质量评估"""
        # 构建评估摘要
        class_summary = []
        for c in merged.get("classes", [])[:5]:  # 截断防止超长
            class_summary.append(f"{c.get('id')}: {c.get('name_cn')} - {c.get('description', '')[:50]}")
        metric_summary = []
        for m in merged.get("metrics", [])[:5]:
            metric_summary.append(f"{m.get('id')}: {m.get('name')} = {json.dumps(m.get('definition', {}), ensure_ascii=False)}")

        prompt = build_quality_assessment_prompt(
            diff_summary=diff.summary,
            classes=json.dumps(class_summary, ensure_ascii=False, indent=2),
            metrics=json.dumps(metric_summary, ensure_ascii=False, indent=2),
        )
        try:
            response = await get_async_client().chat.completions.create(
                model=get_model_name(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            return QualityAssessmentResult(**data)
        except Exception as e:
            print(f"[Warning] 质量评估失败: {e}")
            return QualityAssessmentResult(
                overall_score=5.0,
                confidence=0.3,
                summary=f"质量评估失败: {e}"
            )

    # --------------------------------------------------------
    # 结果合并
    # --------------------------------------------------------

    def _merge_results(self, batch_results: List[OptimizationBatchResult], global_result: GlobalCorrectionResult) -> Dict:
        """合并所有批次结果 + 全局校正"""
        rename_map = {r["from"]: r["to"] for r in global_result.class_renames}

        all_classes = {}
        all_metrics = {}
        all_dimension_groups = {}
        all_rels = {}
        all_concepts = {}

        for r in batch_results:
            for c in r.classes:
                cid = rename_map.get(c.id, c.id)
                c.id = cid
                all_classes[cid] = c.model_dump()
            for m in r.metrics:
                m.target_class = rename_map.get(m.target_class, m.target_class)
                all_metrics[m.id] = m.model_dump()
            for group in r.dimension_groups:
                all_dimension_groups[group.id] = group.model_dump()
            for rel in r.relationships:
                rel.source = rename_map.get(rel.source, rel.source)
                rel.target = rename_map.get(rel.target, rel.target)
                rid = f"rel_{rel.source}_{rel.target}"
                all_rels[rid] = rel.model_dump()
            for c in r.concepts:
                c.related_class = rename_map.get(c.related_class, c.related_class)
                all_concepts[c.id] = c.model_dump()

        # 合并全局校正
        for rel in global_result.relationship_corrections:
            rid = f"rel_{rel.source}_{rel.target}"
            all_rels[rid] = rel.model_dump()
        for m in global_result.metric_corrections:
            all_metrics[m.id] = m.model_dump()
        for c in global_result.concept_corrections:
            all_concepts[c.id] = c.model_dump()

        return {
            "classes": list(all_classes.values()),
            "metrics": list(all_metrics.values()),
            "dimension_groups": list(all_dimension_groups.values()),
            "relationships": list(all_rels.values()),
            "concepts": list(all_concepts.values()),
        }

    @staticmethod
    def _normalized_similarity_text(*values: Any) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", " ".join(str(value or "").lower() for value in values))

    @classmethod
    def _is_highly_similar(cls, left: str, right: str, threshold: float = 0.92) -> bool:
        if not left or not right:
            return False
        return left == right or SequenceMatcher(None, left, right).ratio() >= threshold

    def _deduplicate_optimization_assets(self, assets: Dict) -> Dict:
        """保留首个稳定 ID，剔除语义完全重复或措辞高度相似的未审核优化结果。"""
        retained = {"classes": [], "metrics": [], "relationships": [], "concepts": [], "dimension_groups": []}
        class_signatures: list[tuple[str, str]] = []
        metric_signatures: list[tuple[str, str]] = []
        relationship_signatures = set()
        concept_signatures: list[tuple[str, str]] = []
        dimension_group_ids = set()

        for item in assets.get("classes", []):
            source = str(item.get("table_name") or item.get("table_name") or "").strip().lower()
            signature = self._normalized_similarity_text(item.get("name_cn"), item.get("description"))
            duplicate = any(
                existing_source == source and self._is_highly_similar(existing_signature, signature)
                for existing_source, existing_signature in class_signatures
            )
            if not duplicate:
                retained["classes"].append(item)
                class_signatures.append((source, signature))

        for item in assets.get("metrics", []):
            definition = _json_obj(item.get("definition"))
            structural_signature = json.dumps(
                {"anchor_class": definition.get("anchor_class"), "expression_operator": definition.get("expression_operator"), "inputs": definition.get("inputs", [])},
                ensure_ascii=False,
                sort_keys=True,
            )
            name_signature = self._normalized_similarity_text(item.get("name"), item.get("description"))
            duplicate = any(
                existing_structural == structural_signature
                or (
                    existing_structural.split('"inputs"', 1)[0] == structural_signature.split('"inputs"', 1)[0]
                    and self._is_highly_similar(existing_name, name_signature)
                )
                for existing_structural, existing_name in metric_signatures
            )
            if not duplicate:
                retained["metrics"].append(item)
                metric_signatures.append((structural_signature, name_signature))

        for item in assets.get("dimension_groups", []):
            group_id = str(item.get("id") or "").strip()
            if group_id and group_id not in dimension_group_ids:
                retained["dimension_groups"].append(item)
                dimension_group_ids.add(group_id)

        for item in assets.get("relationships", []):
            signature = (
                str(item.get("source") or "").strip(),
                str(item.get("target") or "").strip(),
                str(item.get("source_key") or item.get("join_key") or "").strip(),
                str(item.get("target_key") or item.get("join_key") or "").strip(),
            )
            if signature not in relationship_signatures:
                retained["relationships"].append(item)
                relationship_signatures.add(signature)

        for item in assets.get("concepts", []):
            signature = self._normalized_similarity_text(item.get("name"), item.get("description"))
            scope = f"{item.get('parent_id', '')}|{item.get('related_class', '')}|{item.get('concept_type', '')}"
            duplicate = any(
                existing_scope == scope and self._is_highly_similar(existing_signature, signature)
                for existing_scope, existing_signature in concept_signatures
            )
            if not duplicate:
                retained["concepts"].append(item)
                concept_signatures.append((scope, signature))
        return retained

    def _filter_protected_assets(self, assets: Dict) -> Dict:
        """防御性过滤：即使模型错误返回人工审核资产，也绝不将其写回。"""
        all_classes, all_relationships, all_metrics, all_concepts, all_dimension_groups = self._load_schema_assets(
            incremental=False,
            target_class_ids=None,
        )
        # _load_schema_assets 未过滤时保留完整资产；仅 approved/rejected 视为不可修改。
        protected_class_ids = {
            item.get("id") for item in all_classes
            if bool(item.get("is_reviewed")) or item.get("review_status") in {"approved", "rejected"}
        }
        protected_metric_ids = {
            item.get("id") for item in all_metrics
            if bool(item.get("is_reviewed")) or item.get("review_status") in {"approved", "rejected"}
        }
        protected_concept_ids = {
            item.get("id") for item in all_concepts
            if bool(item.get("is_reviewed")) or item.get("review_status") in {"approved", "rejected"}
        }
        protected_relationships = {
            (item.get("source"), item.get("target")) for item in all_relationships
            if bool(item.get("is_reviewed")) or item.get("review_status") in {"approved", "rejected"}
        }
        protected_dimension_group_ids = {
            item.get("id") for item in all_dimension_groups
            if item.get("status") in {"approved", "deprecated"}
        }
        return {
            "classes": [item for item in assets.get("classes", []) if item.get("id") not in protected_class_ids],
            "metrics": [item for item in assets.get("metrics", []) if item.get("id") not in protected_metric_ids],
            "dimension_groups": [item for item in assets.get("dimension_groups", []) if item.get("id") not in protected_dimension_group_ids],
            "relationships": [
                item for item in assets.get("relationships", [])
                if (item.get("source"), item.get("target")) not in protected_relationships
            ],
            "concepts": [item for item in assets.get("concepts", []) if item.get("id") not in protected_concept_ids],
        }

    def _validate_optimization_assets(self, merged: Dict) -> Dict:
        """使用与提取阶段一致的物理字段/引用校验规则过滤优化结果。"""
        current_classes, _, _, current_concepts, current_dimension_groups = self._load_schema_assets(incremental=False, target_class_ids=None)
        class_context = {item.get("id", ""): self._normalize_class_for_validation(item) for item in current_classes if item.get("id")}
        summaries = self._build_validation_summaries(list(class_context.values()))
        for item in merged.get("classes", []):
            cid = item.get("id", "")
            if not cid:
                continue
            base = class_context.get(cid, {})
            merged_item = {**base, **item}
            if not item.get("fields") and base.get("fields"):
                merged_item["fields"] = base.get("fields")
            if not item.get("properties") and base.get("properties"):
                merged_item["properties"] = base.get("properties")
            if not item.get("table_name") and base.get("table_name"):
                merged_item["table_name"] = base.get("table_name")
            if not item.get("table_name") and base.get("table_name"):
                merged_item["table_name"] = base.get("table_name")
            class_context[cid] = self._normalize_class_for_validation(merged_item)

        concept_context = {item.get("id", ""): item for item in current_concepts if item.get("id")}
        for item in merged.get("concepts", []):
            cid = item.get("id", "")
            if cid:
                concept_context[cid] = {**concept_context.get(cid, {}), **item}

        dimension_group_context = {
            item.get("id", ""): item for item in current_dimension_groups if item.get("id")
        }
        dimension_group_context.update({
            item.get("id", ""): item for item in merged.get("dimension_groups", []) if item.get("id")
        })
        validation_schema = {
            "business_name": self.scenario_id,
            "classes": list(class_context.values()),
            "relationships": merged.get("relationships", []),
            "dimension_groups": list(dimension_group_context.values()),
            "metrics": merged.get("metrics", []),
            "concepts": list(concept_context.values()),
        }
        cleaned = validate_schema_assets(validation_schema, summaries)

        merged_class_ids = {item.get("id") for item in merged.get("classes", []) if item.get("id")}
        merged_concept_ids = {item.get("id") for item in merged.get("concepts", []) if item.get("id")}
        merged_dimension_group_ids = {item.get("id") for item in merged.get("dimension_groups", []) if item.get("id")}
        cleaned_metrics = cleaned.get("metrics", [])
        for metric in cleaned_metrics:
            metric["dimensions"] = _json_list(metric.get("dimensions"))
            metric["required_dimensions"] = _json_list(metric.get("required_dimensions"))
        return {
            "classes": [item for item in cleaned.get("classes", []) if item.get("id") in merged_class_ids],
            "relationships": cleaned.get("relationships", []),
            "metrics": cleaned_metrics,
            "dimension_groups": [item for item in cleaned.get("dimension_groups", []) if item.get("id") in merged_dimension_group_ids],
            "concepts": [item for item in cleaned.get("concepts", []) if item.get("id") in merged_concept_ids],
        }

    def _normalize_class_for_validation(self, item: dict) -> dict:
        fields = _json_list(item.get("fields"))
        properties = _json_list(item.get("properties"))
        if not fields and properties:
            fields = [{"name_cn": field, "name": field, "type": "text"} for field in properties]
        table_name = item.get("table_name", "")
        return {
            **item,
            "table_name": table_name,
            "table_name": item.get("table_name", "") or (table_name.replace(".csv", "") if table_name else item.get("id", "")),
            "fields": fields,
            "properties": properties or [field.get("name_cn") or field.get("name") for field in fields if isinstance(field, dict)],
            "primary_key": item.get("primary_key", ""),
        }

    def _build_validation_summaries(self, classes: list[dict]) -> list[dict]:
        summaries = []
        for item in classes:
            cid = item.get("id", "")
            table_name = item.get("table_name", "")
            table_name = item.get("table_name", "") or (table_name.replace(".csv", "") if table_name else cid)
            source = table_name or table_name
            fields = [field for field in item.get("fields", []) if isinstance(field, dict)]
            columns = [str(field.get("name") or "").strip() for field in fields]
            columns = [column for column in columns if column]
            summaries.append({
                "file": source,
                "columns": columns,
                "column_types": {
                    str(field.get("name") or "").strip(): field.get("type", "text")
                    for field in fields
                    if str(field.get("name") or "").strip()
                },
                "total_rows": -1,
            })
        return summaries

    # --------------------------------------------------------
    # Diff 报告
    # --------------------------------------------------------

    def _generate_diff(self, old_classes, old_metrics, old_rels, old_concepts, new_data) -> OptimizationDiff:
        """生成差异报告"""
        old_class_ids = {c["id"] for c in old_classes}
        old_metric_ids = {m["id"] for m in old_metrics}
        old_rel_ids = {f"rel_{r.get('source')}_{r.get('target')}" for r in old_rels}
        old_concept_ids = {c["id"] for c in old_concepts}

        added_classes = []
        modified_classes = []
        for c in new_data.get("classes", []):
            cid = c.get("id", "")
            if cid not in old_class_ids:
                added_classes.append(cid)
            else:
                modified_classes.append(cid)

        added_metrics = []
        modified_metrics = []
        for m in new_data.get("metrics", []):
            mid = m.get("id", "")
            if mid not in old_metric_ids:
                added_metrics.append(mid)
            else:
                modified_metrics.append(mid)

        added_rels = []
        for r in new_data.get("relationships", []):
            rid = f"rel_{r.get('source')}_{r.get('target')}"
            if rid not in old_rel_ids:
                added_rels.append(rid)

        added_concepts = []
        for c in new_data.get("concepts", []):
            cid = c.get("id", "")
            if cid not in old_concept_ids:
                added_concepts.append(cid)

        return OptimizationDiff(
            added_classes=added_classes,
            modified_classes=modified_classes,
            added_metrics=added_metrics,
            modified_metrics=modified_metrics,
            added_relationships=added_rels,
            added_concepts=added_concepts,
            summary=f"新增 {len(added_classes)} 个 Class, 修改 {len(modified_classes)} 个; "
                    f"新增 {len(added_metrics)} 个 Metric, 修改 {len(modified_metrics)} 个; "
                    f"新增 {len(added_rels)} 个 Relationship, 新增 {len(added_concepts)} 个 Concept"
        )

    # --------------------------------------------------------
    # 应用优化
    # --------------------------------------------------------

    def _apply_optimization(self, merged: Dict) -> Dict:
        """应用优化结果到数据库"""
        conn = get_db()
        counts = {"classes": 0, "relationships": 0, "dimension_groups": 0, "metrics": 0, "concepts": 0}

        try:
            for item in merged.get("classes", []):
                if self._upsert_class(conn, item):
                    counts["classes"] += 1
            for item in merged.get("relationships", []):
                if self._upsert_relationship(conn, item):
                    counts["relationships"] += 1
            for item in merged.get("dimension_groups", []):
                if self._upsert_dimension_group(conn, item):
                    counts["dimension_groups"] += 1
            for item in merged.get("metrics", []):
                if self._upsert_metric(conn, item):
                    counts["metrics"] += 1
            for item in merged.get("concepts", []):
                if self._upsert_concept(conn, item):
                    counts["concepts"] += 1
            conn.commit()
        finally:
            conn.close()

        return counts

    def _upsert_class(self, conn, item: dict) -> bool:
        cid = item.get("id", "").strip()
        if not cid:
            return False
        sid = self.scenario_id
        exists = conn.execute("SELECT id, is_reviewed, review_status FROM schema_classes WHERE id=? AND scenario_id=?", (cid, sid)).fetchone()
        if exists and (bool(exists["is_reviewed"]) or exists["review_status"] in {"approved", "rejected"}):
            return False
        values = (
            item.get("name_cn", ""), item.get("description", ""),
            item.get("primary_key", ""), item.get("table_name", ""),
        )
        if exists:
            conn.execute(
                "UPDATE schema_classes SET name_cn=?, description=?, primary_key=?, table_name=?, is_reviewed=FALSE, review_status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=? AND scenario_id=?",
                (*values, cid, sid),
            )
        else:
            conn.execute(
                "INSERT INTO schema_classes (id, scenario_id, name_cn, description, primary_key, table_name, is_reviewed, review_status, created_at, updated_at) VALUES (?,?,?,?,?,?,FALSE,'pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (cid, sid, *values),
            )
        return True

    def _upsert_relationship(self, conn, item: dict) -> bool:
        source = item.get("source", "").strip()
        target = item.get("target", "").strip()
        if not source or not target:
            return False
        sid = self.scenario_id
        exists = conn.execute(
            "SELECT id, is_reviewed, review_status FROM schema_relationships WHERE source=? AND target=? AND scenario_id=?",
            (source, target, sid),
        ).fetchone()
        if exists and (bool(exists["is_reviewed"]) or exists["review_status"] in {"approved", "rejected"}):
            return False
        values = (
            item.get("type", ""), item.get("join_key", ""),
            item.get("source_key", ""), item.get("target_key", ""),
        )
        if exists:
            conn.execute(
                "UPDATE schema_relationships SET type=?, join_key=?, source_key=?, target_key=?, is_reviewed=FALSE, review_status='pending', updated_at=CURRENT_TIMESTAMP WHERE source=? AND target=? AND scenario_id=?",
                (*values, source, target, sid),
            )
        else:
            conn.execute(
                "INSERT INTO schema_relationships (scenario_id, source, target, type, join_key, source_key, target_key) VALUES (?,?,?,?,?,?,?)",
                (sid, source, target, *values),
            )
        return True

    def _upsert_metric(self, conn, item: dict) -> bool:
        mid = item.get("id", "").strip()
        definition = item.get("definition")
        if not mid or not isinstance(definition, dict) or definition.get("version") != 1 or not definition.get("inputs"):
            return False
        sid = self.scenario_id
        exists = conn.execute("SELECT id, is_reviewed, review_status FROM metrics WHERE id=? AND scenario_id=?", (mid, sid)).fetchone()
        if exists and (bool(exists["is_reviewed"]) or exists["review_status"] in {"approved", "rejected"}):
            return False
        dims = json.dumps(item.get("dimensions", []), ensure_ascii=False)
        req_dims = json.dumps(item.get("required_dimensions", []), ensure_ascii=False)
        values = (
            item.get("name", ""), item.get("description", ""), item.get("category", ""),
            definition.get("anchor_class", ""), json.dumps(definition, ensure_ascii=False),
            dims, req_dims, item.get("chart_type", "bar"),
        )
        if exists:
            conn.execute(
                     """UPDATE metrics SET name=?, description=?, category=?, target_class=?, definition=?, dimensions=?, required_dimensions=?, chart_type=?, is_reviewed=FALSE, review_status='pending', updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=?""",
                (*values, mid, sid),
            )
        else:
            conn.execute(
                     """INSERT INTO metrics (id, scenario_id, name, description, category, target_class, definition, dimensions, required_dimensions, chart_type, is_reviewed, review_status, created_at, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,FALSE,'pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (mid, sid, *values),
            )
        conn.execute("DELETE FROM metric_dimension_bindings WHERE scenario_id=? AND metric_id=?", (sid, mid))
        valid_group_ids = {
            row["id"] for row in conn.execute(
                "SELECT id FROM dimension_groups WHERE scenario_id=?", (sid,)
            ).fetchall()
        }
        for group_id in dict.fromkeys(_json_list(item.get("dimension_group_ids", []))):
            if group_id in valid_group_ids:
                conn.execute(
                    "INSERT INTO metric_dimension_bindings (metric_id, scenario_id, group_id) VALUES (?,?,?)",
                    (mid, sid, group_id),
                )
        return True

    def _upsert_dimension_group(self, conn, item: dict) -> bool:
        group_id = str(item.get("id") or "").strip()
        if not group_id:
            return False
        sid = self.scenario_id
        existing = conn.execute(
            "SELECT status FROM dimension_groups WHERE id=? AND scenario_id=?", (group_id, sid)
        ).fetchone()
        if existing and existing["status"] in {"approved", "deprecated"}:
            return False
        values = (
            str(item.get("name") or group_id), str(item.get("description") or ""),
            str(item.get("group_type") or "categorical"), int(bool(item.get("is_required", False))),
            str(item.get("default_option") or ""),
            str(item.get("clarification_policy") or "ask_when_ambiguous"),
        )
        if existing:
            conn.execute(
                """UPDATE dimension_groups SET name=?, description=?, group_type=?, is_required=?, default_option=?, clarification_policy=?, status='draft', updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=?""",
                (*values, group_id, sid),
            )
        else:
            conn.execute(
                """INSERT INTO dimension_groups
                   (id, scenario_id, name, description, group_type, is_required, default_option, clarification_policy, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,'draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (group_id, sid, *values),
            )
        conn.execute("DELETE FROM dimension_group_options WHERE scenario_id=? AND group_id=?", (sid, group_id))
        conn.execute("DELETE FROM dimension_field_mappings WHERE scenario_id=? AND group_id=?", (sid, group_id))
        for index, option in enumerate(item.get("options", [])):
            if not isinstance(option, dict) or not str(option.get("value") or "").strip():
                continue
            conn.execute(
                """INSERT INTO dimension_group_options
                   (group_id, scenario_id, value, label, aliases, is_default, sort_order, status)
                   VALUES (?,?,?,?,?,?,?,'draft')""",
                (group_id, sid, option["value"], str(option.get("label") or option["value"]),
                 json.dumps(option.get("aliases", []), ensure_ascii=False), int(bool(option.get("is_default", False))), option.get("sort_order", index)),
            )
        for index, mapping in enumerate(item.get("field_mappings", [])):
            if not isinstance(mapping, dict):
                continue
            conn.execute(
                """INSERT INTO dimension_field_mappings
                   (group_id, scenario_id, option_value, class_id, field_name, display_name, priority)
                   VALUES (?,?,?,?,?,?,?)""",
                (group_id, sid, mapping.get("option_value", ""), mapping.get("class_id", ""),
                 mapping.get("field_name", ""), mapping.get("display_name", ""), mapping.get("priority", index)),
            )
        return True

    def _upsert_concept(self, conn, item: dict) -> bool:
        cid = item.get("id", "").strip()
        if not cid:
            return False
        sid = self.scenario_id
        exists = conn.execute("SELECT id, is_reviewed, review_status FROM concepts WHERE id=? AND scenario_id=?", (cid, sid)).fetchone()
        if exists and (bool(exists["is_reviewed"]) or exists["review_status"] in {"approved", "rejected"}):
            return False
        values = (
            item.get("name", ""), item.get("description", ""), item.get("parent_id", ""),
            int(item.get("level", 0) or 0), item.get("concept_type", "entity"),
            item.get("related_class", ""), int(item.get("sort_order", 0) or 0),
        )
        if exists:
            conn.execute(
                "UPDATE concepts SET name=?, description=?, parent_id=?, level=?, concept_type=?, related_class=?, sort_order=?, is_reviewed=FALSE, review_status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=? AND scenario_id=?",
                (*values, cid, sid),
            )
        else:
            conn.execute(
                "INSERT INTO concepts (id, scenario_id, name, description, parent_id, level, concept_type, related_class, sort_order, is_reviewed, review_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,FALSE,'pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (cid, sid, *values),
            )
        return True

    # --------------------------------------------------------
    # 运行记录管理
    # --------------------------------------------------------

    def _create_run_record(self, run_id: str):
        conn = get_db()
        conn.execute(
            """INSERT INTO schema_optimization_runs (id, scenario_id, status, started_at)
               VALUES (?,?, 'running', CURRENT_TIMESTAMP)""",
            (run_id, self.scenario_id),
        )
        conn.commit()
        conn.close()

    def _update_run_success(self, run_id: str, diff: OptimizationDiff, applied: dict, quality: dict):
        conn = get_db()
        conn.execute(
            """UPDATE schema_optimization_runs
               SET status='success', summary=?, changes_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=?""",
            (diff.summary, json.dumps({"diff": diff.model_dump(), "applied": applied, "quality": quality}, ensure_ascii=False), run_id, self.scenario_id),
        )
        conn.commit()
        conn.close()

    def _update_run_failure(self, run_id: str, error: str):
        conn = get_db()
        conn.execute(
            """UPDATE schema_optimization_runs
               SET status='failed', error=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=?""",
            (error, run_id, self.scenario_id),
        )
        conn.commit()
        conn.close()