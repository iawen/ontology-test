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
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime

from configs.global_config import Cfg, client
from tools.db import get_db
from pydantic import ValidationError

from core.models.schema_model import (
    OptimizationBatchResult,
    GlobalCorrectionResult,
    OptimizationDiff,
    QualityAssessmentResult,
    ClassOptimization,
    MetricOptimization,
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

class SchemaOptimizerV4:
    """Schema 优化器 v4 - 使用 BM25 检索"""

    def __init__(self, scenario_id: str):
        self.scenario_id = scenario_id
        self.doc_index = DocumentIndex()  # 不再需要 embedding 初始化

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
            classes, relationships, metrics, concepts = self._load_schema_assets(incremental, target_class_ids)

            if not classes and not metrics:
                await _emit_progress(progress_callback, running=False, phase="done", progress=100, total=100, message="无可优化资产")
                return {"status": "skipped", "run_id": run_id, "message": "无可优化资产"}

            await _emit_progress(progress_callback, phase="batch_optimizing", progress=30, total=100, message="阶段一：分批优化")
            batch_results = await self._run_batch_optimization(
                classes, relationships, metrics, concepts, progress_callback
            )

            await _emit_progress(progress_callback, phase="global_correcting", progress=65, total=100, message="阶段二：全局校正")
            global_result = await self._run_global_correction(batch_results, progress_callback)

            merged = self._merge_results(batch_results, global_result)

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

    def _load_schema_assets(self, incremental: bool, target_class_ids: Optional[List[str]]):
        """从数据库加载当前 Schema 资产"""
        conn = get_db()
        sid = self.scenario_id

        class_sql = "SELECT * FROM schema_classes WHERE scenario_id=?"
        if incremental:
            class_sql += " AND COALESCE(is_reviewed, 0)=0"
        if target_class_ids:
            placeholders = ",".join("?" * len(target_class_ids))
            class_sql += f" AND id IN ({placeholders})"
            class_rows = conn.execute(class_sql, (sid, *target_class_ids)).fetchall()
        else:
            class_rows = conn.execute(class_sql, (sid,)).fetchall()
        classes = [_row_to_dict(r) for r in class_rows]

        metric_sql = "SELECT * FROM metrics WHERE scenario_id=?"
        if incremental:
            metric_sql += " AND COALESCE(is_reviewed, 0)=0"
        metric_rows = conn.execute(metric_sql, (sid,)).fetchall()
        metrics = [_row_to_dict(r) for r in metric_rows]

        rel_rows = conn.execute(
            "SELECT * FROM schema_relationships WHERE scenario_id=?", (sid,)
        ).fetchall()
        relationships = [_row_to_dict(r) for r in rel_rows]

        concept_sql = "SELECT * FROM concepts WHERE scenario_id=?"
        if incremental:
            concept_sql += " AND COALESCE(is_reviewed, 0)=0"
        concept_rows = conn.execute(concept_sql, (sid,)).fetchall()
        concepts = [_row_to_dict(r) for r in concept_rows]

        conn.close()
        return classes, relationships, metrics, concepts

    # --------------------------------------------------------
    # 阶段一：分批优化（修复孤立资产 Bug）
    # --------------------------------------------------------

    def _build_batches(self, classes, relationships, metrics, concepts) -> List[Dict]:
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

        return [b for b in batches if any(b.values())]

    async def _run_batch_optimization(self, classes, relationships, metrics, concepts, progress_callback=None) -> List[OptimizationBatchResult]:
        """执行分批优化"""
        batches = self._build_batches(classes, relationships, metrics, concepts)
        results = []
        total = len(batches)

        for i, batch in enumerate(batches):
            await _emit_progress(
                progress_callback, phase="batch_optimizing",
                progress=30 + int(35 * (i / max(total, 1))), total=100,
                message=f"阶段一：批次 {i+1}/{total}"
            )

            query = self._build_batch_query(batch)
            doc_context = self.doc_index.build_context(query, top_k=5, max_chars=DOC_CONTEXT_LIMIT)

            result = await self._call_llm_batch(batch, doc_context)
            results.append(result)

        return results

    def _build_batch_query(self, batch: Dict) -> str:
        """构建批次查询文本"""
        parts = []
        for c in batch.get("classes", []):
            parts.append(f"{c.get('name_cn', '')} {c.get('description', '')} {c.get('id', '')}")
        for m in batch.get("metrics", []):
            parts.append(f"{m.get('name', '')} {m.get('description', '')} {m.get('formula', '')}")
        return " ".join(parts)[:500]

    # --------------------------------------------------------
    # LLM 调用（智能重试：区分语法/语义错误）
    # --------------------------------------------------------

    async def _call_llm_batch(self, batch: Dict, doc_context: str) -> OptimizationBatchResult:
        """调用 LLM 进行单批次优化（带智能重试）"""
        prompt = self._build_batch_prompt(batch, doc_context)

        last_validation_error = None

        for attempt in range(LLM_RETRY_MAX + 1):
            try:
                response = await client.chat.completions.create(
                    model=Cfg.model_name,
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

    def _build_batch_prompt(self, batch: Dict, doc_context: str) -> str:
        """构建批次优化提示词"""
        return f"""你是数据仓库建模与本体论专家。请根据以下业务文档和当前 Schema 资产，优化实体类、指标、关系和概念。

## 业务文档片段（RAG 检索）
{doc_context}

## 当前批次 Schema 资产
{json.dumps(batch, ensure_ascii=False, indent=2)}

## 优化要求
1. **Class 优化**：根据文档修正 name_cn、description，fields 只输出需优化的字段（排除已正确的）
2. **Metric 优化**：根据文档修正 name、description、formula、dimensions、required_dimensions
3. **Relationship 优化**：根据文档补充或修正 source_key/target_key
4. **Concept 优化**：根据文档补充概念层级

## 输出要求
输出标准 JSON，结构如下：
{{
  "classes": [
    {{"id": "原ID", "name_cn": "优化后中文名", "description": "优化后描述", "primary_key": "", "csv_file": "", "fields": []}}
  ],
  "relationships": [
    {{"source": "类ID", "target": "类ID", "type": "belongs_to", "source_key": "源键", "target_key": "目标键", "join_key": ""}}
  ],
  "metrics": [
    {{"id": "原ID", "name": "优化后名称", "description": "优化后描述", "category": "", "target_class": "类ID", "calculation": "", "formula": "SUM(col)", "dimensions": ["col1"], "required_dimensions": ["col1"], "filters_hint": "", "chart_type": "bar"}}
  ],
  "concepts": [
    {{"id": "原ID", "name": "", "description": "", "parent_id": "", "level": 0, "concept_type": "entity", "related_class": ""}}
  ],
  "summary": "本批次优化摘要"
}}

严禁输出 JSON 以外的内容。"""

    def _build_retry_prompt(self, original_prompt: str, error: str, retry_type: str = "syntax") -> str:
        """构建自校正重试提示词（区分错误类型）"""
        base = f"""{original_prompt}

## 上次输出验证失败
你的上一次输出存在以下错误：
{error}
"""
        if retry_type == "semantic":
            base += "\n## 特别提醒（语义错误）\n检测到字段缺失，请确保所有必填字段（如 id、source、target 等）都已完整输出，且值不为空。"
        else:
            base += "\n## 特别提醒（格式错误）\n请确保输出是合法的 JSON 格式，注意引号、逗号、括号匹配。"
        base += "\n请修正错误并重新输出符合要求的 JSON。"
        return base

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
        compressed_metrics = [{"id": m.id, "name": m.name, "target_class": m.target_class, "formula": m.formula} for m in all_metrics]
        compressed_concepts = [{"id": c.id, "name": c.name, "parent_id": c.parent_id, "level": c.level} for c in all_concepts]

        prompt = f"""你是数据仓库建模专家。请对以下跨批次优化结果进行全局一致性校正。

## 所有 Class 摘要
{json.dumps(compressed_classes, ensure_ascii=False, indent=2)}

## 所有 Metric 摘要
{json.dumps(compressed_metrics, ensure_ascii=False, indent=2)}

## 所有 Relationship
{json.dumps([r.model_dump() for r in all_rels], ensure_ascii=False, indent=2)}

## 所有 Concept
{json.dumps(compressed_concepts, ensure_ascii=False, indent=2)}

## 检查项
1. **类重命名**：是否有重复或近似命名的 Class 需要统一？
2. **关系悬空**：Relationship 的 source/target 是否引用了不存在的 Class？
3. **指标悬空**：Metric 的 target_class 是否引用了不存在的 Class？
4. **概念树完整性**：Concept 的 parent_id 是否引用了不存在的概念？是否存在循环引用？
5. **指标口径一致性**：同名的 Metric 是否使用了相同的 formula？不一致的需要标记警告。

## 输出 JSON
{{
  "class_renames": [{{"from": "旧ID", "to": "新ID"}}],
  "relationship_corrections": [{{"source": "...", "target": "...", "type": "...", "source_key": "...", "target_key": "...", "join_key": "..."}}],
  "metric_corrections": [{{"id": "...", "name": "...", "target_class": "...", ...}}],
  "concept_corrections": [{{"id": "...", "parent_id": "...", "level": ...}}],
  "metric_consistency_warnings": ["指标 '销售额' 在不同批次中 formula 不一致"],
  "concept_tree_warnings": ["概念 '订单' 的父级 '交易' 不存在"],
  "summary": "全局校正摘要"
}}"""

        try:
            response = await client.chat.completions.create(
                model=Cfg.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4096,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            return GlobalCorrectionResult(**data)
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
            metric_summary.append(f"{m.get('id')}: {m.get('name')} = {m.get('formula', '')}")

        prompt = f"""你是一名数据建模质量评审专家。请评估以下 Schema 优化结果的质量。

## 优化摘要
{diff.summary}

## 新增/修改的 Class（前5个）
{json.dumps(class_summary, ensure_ascii=False, indent=2)}

## 新增/修改的 Metric（前5个）
{json.dumps(metric_summary, ensure_ascii=False, indent=2)}

## 评估维度
1. **业务准确性**：优化建议是否贴合业务文档语义？
2. **命名规范性**：名称是否符合业务术语习惯？
3. **结构完整性**：Class-Metric-Relationship 引用关系是否完整？
4. **可执行性**：formula、dimensions 是否合理可执行？

## 输出 JSON
{{
  "overall_score": 8.5,
  "confidence": 0.85,
  "strengths": ["维度划分清晰", "指标口径一致"],
  "weaknesses": ["部分 Class 描述仍偏技术化"],
  "high_risk_items": ["指标 'gmv' 缺少 required_dimensions"],
  "summary": "整体质量较高，建议重点审核 high_risk_items"
}}
"""
        try:
            response = await client.chat.completions.create(
                model=Cfg.model_name,
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
            "relationships": list(all_rels.values()),
            "concepts": list(all_concepts.values()),
        }

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
        counts = {"classes": 0, "relationships": 0, "metrics": 0, "concepts": 0}

        try:
            for item in merged.get("classes", []):
                if self._upsert_class(conn, item):
                    counts["classes"] += 1
            for item in merged.get("relationships", []):
                if self._upsert_relationship(conn, item):
                    counts["relationships"] += 1
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
        exists = conn.execute("SELECT id FROM schema_classes WHERE id=? AND scenario_id=?", (cid, sid)).fetchone()
        values = (
            item.get("name_cn", ""), item.get("description", ""),
            item.get("primary_key", ""), item.get("csv_file", ""),
        )
        if exists:
            conn.execute(
                "UPDATE schema_classes SET name_cn=?, description=?, primary_key=?, csv_file=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP WHERE id=? AND scenario_id=?",
                (*values, cid, sid),
            )
        else:
            conn.execute(
                "INSERT INTO schema_classes (id, scenario_id, name_cn, description, primary_key, csv_file, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
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
            "SELECT id FROM schema_relationships WHERE source=? AND target=? AND scenario_id=?",
            (source, target, sid),
        ).fetchone()
        values = (
            item.get("type", ""), item.get("join_key", ""),
            item.get("source_key", ""), item.get("target_key", ""),
        )
        if exists:
            conn.execute(
                "UPDATE schema_relationships SET type=?, join_key=?, source_key=?, target_key=? WHERE source=? AND target=? AND scenario_id=?",
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
        if not mid:
            return False
        sid = self.scenario_id
        exists = conn.execute("SELECT id FROM metrics WHERE id=? AND scenario_id=?", (mid, sid)).fetchone()
        dims = json.dumps(item.get("dimensions", []), ensure_ascii=False)
        req_dims = json.dumps(item.get("required_dimensions", []), ensure_ascii=False)
        values = (
            item.get("name", ""), item.get("description", ""), item.get("category", ""),
            item.get("target_class", ""), item.get("calculation", ""), item.get("formula", ""),
            dims, req_dims, item.get("filters_hint", ""), item.get("chart_type", "bar"),
        )
        if exists:
            conn.execute(
                """UPDATE metrics SET name=?, description=?, category=?, target_class=?, calculation=?, formula=?, dimensions=?, required_dimensions=?, filters_hint=?, chart_type=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND scenario_id=?""",
                (*values, mid, sid),
            )
        else:
            conn.execute(
                """INSERT INTO metrics (id, scenario_id, name, description, category, target_class, calculation, formula, dimensions, required_dimensions, filters_hint, chart_type, is_reviewed, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (mid, sid, *values),
            )
        return True

    def _upsert_concept(self, conn, item: dict) -> bool:
        cid = item.get("id", "").strip()
        if not cid:
            return False
        sid = self.scenario_id
        exists = conn.execute("SELECT id FROM concepts WHERE id=? AND scenario_id=?", (cid, sid)).fetchone()
        values = (
            item.get("name", ""), item.get("description", ""), item.get("parent_id", ""),
            int(item.get("level", 0) or 0), item.get("concept_type", "entity"),
            item.get("related_class", ""), int(item.get("sort_order", 0) or 0),
        )
        if exists:
            conn.execute(
                "UPDATE concepts SET name=?, description=?, parent_id=?, level=?, concept_type=?, related_class=?, sort_order=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP WHERE id=? AND scenario_id=?",
                (*values, cid, sid),
            )
        else:
            conn.execute(
                "INSERT INTO concepts (id, scenario_id, name, description, parent_id, level, concept_type, related_class, sort_order, is_reviewed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
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