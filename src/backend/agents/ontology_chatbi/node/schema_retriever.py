import re

from agents.ontology_chatbi.constants import (
    PROGRESS_METRIC_KEYWORDS,
    PROGRESS_QUERY_KEYWORDS,
)
from agents.ontology_chatbi.helper import metric_component_names, metric_context_summary, metric_target_classes

# ============================================================
# Schema 检索 Agent
# ============================================================


class SchemaRetrieverAgent:
    """
    动态检索与用户问题相关的 Schema。
    解决问题1：Schema 信息爆炸。

    契约：
      输入: user_message, ontology_engine
            输出: {
                "schema_context": str,
                "metric_context": str,
                "relevant_classes": list[str],
                "relevant_metrics": list[str],
            }
    """

    async def retrieve(self, user_message: str, ontology_engine) -> dict:
        keywords = self._extract_keywords(user_message)
        relevant_classes = self._find_relevant_classes(keywords, ontology_engine)
        relevant_metrics = self._find_relevant_metrics(keywords, ontology_engine, relevant_classes)
        relevant_rels = self._find_relevant_relationships(relevant_classes, ontology_engine)

        schema_context = self._build_schema_context(ontology_engine, relevant_classes, relevant_rels)
        metric_context = self._build_metric_context(ontology_engine, relevant_metrics)

        return {
            "schema_context": schema_context,
            "metric_context": metric_context,
            "relevant_classes": relevant_classes,
            "relevant_metrics": relevant_metrics,
        }

    def _extract_keywords(self, text: str) -> list[str]:
        """提取关键词（中文分词简化版）"""
        keywords = []
        for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]{3,}|\d+", text):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                # 用户问题通常会将意图词与业务术语连写，例如“查询销售额”。
                # 补充连续中文子串，使“销售额”等 schema/metric 名称能被检索到。
                keywords.extend(
                    token[start:end] for start in range(len(token)) for end in range(start + 2, len(token) + 1)
                )
            else:
                keywords.append(token)
        return list(dict.fromkeys(keyword.lower() for keyword in keywords))

    def _find_relevant_classes(self, keywords: list[str], oe) -> list[str]:
        relevant = set()
        for c in oe.list_classes():
            class_text = f"{c.get('id', '')} {c.get('name_cn', '')} {c.get('description', '')}".lower()
            for kw in keywords:
                if kw in class_text:
                    relevant.add(c["id"])
                    break
            # 字段名匹配
            for prop in c.get("properties", []):
                prop_lower = prop.lower()
                for kw in keywords:
                    if kw in prop_lower:
                        relevant.add(c["id"])
                        break
        return list(relevant)

    def _find_relevant_metrics(self, keywords: list[str], oe, preferred_classes: list[str] | None = None) -> list[str]:
        relevant = []
        for m in oe.list_metrics():
            metric_text = self._metric_search_text(m)
            for kw in keywords:
                if kw in metric_text:
                    metric_id = m.get("id")
                    if metric_id:
                        relevant.append(metric_id)
                    break
        if self._is_progress_query(keywords):
            relevant.extend(self._find_progress_metrics(oe, preferred_classes or []))
        return self._rank_metric_ids_by_class(self._dedupe(relevant), oe, preferred_classes or [])

    @staticmethod
    def _metric_search_text(metric: dict) -> str:
        return " ".join(
            str(value or "")
            for value in (
                metric.get("id"),
                metric.get("name"),
                metric.get("name_cn"),
                metric.get("description"),
                metric.get("category"),
                *metric_component_names(metric),
                metric_context_summary(metric),
            )
        ).lower()

    @staticmethod
    def _is_progress_query(keywords: list[str]) -> bool:
        keyword_text = " ".join(keywords).lower()
        return any(item in keyword_text for item in PROGRESS_QUERY_KEYWORDS)

    def _find_progress_metrics(self, oe, preferred_classes: list[str] | None = None) -> list[str]:
        metric_ids = []
        for metric in oe.list_metrics():
            metric_text = self._metric_search_text(metric)
            if any(keyword in metric_text for keyword in PROGRESS_METRIC_KEYWORDS):
                metric_id = metric.get("id")
                if metric_id:
                    metric_ids.append(metric_id)
        return self._rank_metric_ids_by_class(metric_ids, oe, preferred_classes or [])

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen = set()
        deduped = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _metric_class(metric: dict) -> str:
        target_classes = metric_target_classes(metric)
        if target_classes:
            return target_classes[0]
        return str(metric.get("class_id") or metric.get("target_class") or "")

    def _rank_metric_ids_by_class(self, metric_ids: list[str], oe, preferred_classes: list[str]) -> list[str]:
        if not metric_ids or not preferred_classes:
            return self._dedupe(metric_ids)
        preferred = {class_id for class_id in preferred_classes if class_id}
        metric_by_id = {metric.get("id"): metric for metric in oe.list_metrics()}
        return sorted(
            self._dedupe(metric_ids),
            key=lambda metric_id: 0 if self._metric_matches_classes(metric_by_id.get(metric_id, {}), preferred) else 1,
        )

    @staticmethod
    def _metric_matches_classes(metric: dict, preferred: set[str]) -> bool:
        target_classes = metric_target_classes(metric)
        if any(class_id in preferred for class_id in target_classes):
            return True
        return str(metric.get("class_id") or metric.get("target_class") or "") in preferred

    def _find_relevant_relationships(self, class_ids: list[str], oe) -> list[dict]:
        relevant = []
        for rel in oe.relationships:
            if rel.get("source") in class_ids or rel.get("target") in class_ids:
                relevant.append(rel)
        return relevant

    def _build_schema_context(self, oe, class_ids: list[str], rels: list[dict]) -> str:
        """构建第一阶段使用的 Schema 上下文，不包含任何 Metric。"""
        parts = []

        # 1. 全局摘要（始终包含）
        all_classes = [f"{c['id']}({c.get('name_cn', '')})" for c in oe.list_classes()]
        parts.append(f"## 全局实体类清单\n{', '.join(all_classes)}")

        # 2. 相关 Class 详情
        if class_ids:
            parts.append("## 相关实体类详情")
            for c in oe.list_classes():
                if c["id"] in class_ids:
                    props = c.get("properties", [])[:10]
                    parts.append(
                        f"- **{c['id']}**({c.get('name_cn', '')}): {c.get('description', '')}\n"
                        f"  字段: {', '.join(props)}"
                    )

        # 3. 相关 Relationship
        if rels:
            parts.append("## 相关关系")
            for r in rels:
                parts.append(
                    f"- {r['source']} --[{r.get('type', '')}]--> {r['target']} "
                    f"(JOIN: {r.get('source_key', '')} -> {r.get('target_key', '')})"
                )

        return "\n\n".join(parts)

    def _build_metric_context(self, oe, metric_ids: list[str]) -> str:
        """构建第二阶段使用的 Metric 候选上下文。"""
        if not metric_ids:
            return ""

        parts = ["## 候选指标"]
        metric_by_id = {metric.get("id"): metric for metric in oe.list_metrics()}
        current_class = ""
        for metric_id in metric_ids:
            metric = metric_by_id.get(metric_id)
            if not metric:
                continue
            metric_class = self._metric_class(metric) or "未知实体"
            if metric_class != current_class:
                current_class = metric_class
                parts.append(f"### 指标所属实体：{metric_class}")
            parts.append(f"- {metric_context_summary(metric)}")
        return "\n".join(parts)
