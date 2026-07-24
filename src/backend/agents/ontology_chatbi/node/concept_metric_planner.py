"""Concept-scoped, Metric-compatible planning primitives for complex ChatBI questions."""

import json
import re
from collections import defaultdict


_ANALYSIS_TYPE_KEYWORDS = {
    "attribution": ("为什么", "原因", "归因", "驱动", "影响因素", "拆解", "贡献", "拖累"),
    "trend": ("趋势", "走势", "变化", "环比", "同比", "上月", "去年", "增长", "下降"),
    "health": ("健康度", "健康", "风险", "效率", "周转"),
    "comparison": ("相比", "对比", "比较", "差异"),
}


class ConceptMetricPlanner:
    """Build a bounded analysis plan from approved Concepts, metrics, and DimensionGroups.

    The planner deliberately does not create query parameters. It only identifies a
    business domain, executable analysis axes, and groups metrics that share a
    deterministic compatibility signature. Query planning remains governed by the
    existing Schema Scope and Query Detail validators.
    """

    def build_retrieval_context(self, user_message: str, ontology_engine) -> dict:
        """Return Concept candidates and executable DimensionGroups for schema retrieval."""
        keywords = self._keywords(user_message)
        concepts = self._available_concepts(ontology_engine)
        matched = self._rank_concepts(concepts, keywords)
        matched_ids = {item["id"] for item in matched}
        ancestor_ids = self._ancestor_ids(matched_ids, concepts)
        domain_ids = [
            concept["id"]
            for concept in concepts
            if concept["id"] in ancestor_ids and concept.get("concept_type") == "subject_domain"
        ]
        groups = [
            group
            for group in ontology_engine.list_dimension_groups()
            if group.get("concept_id") in matched_ids | ancestor_ids
        ]
        return {
            "relevant_concepts": matched,
            "relevant_subject_domains": domain_ids,
            "relevant_dimension_group_ids": [group["id"] for group in groups],
            "concept_context": self._concept_context(matched, groups),
        }

    def build_analysis_plan(
        self,
        user_message: str,
        ontology_engine,
        relevant_metric_ids: list[str],
        concept_context: dict,
    ) -> dict:
        """Create a deterministic, safe analysis plan when Concept metadata is usable."""
        analysis_type = self._analysis_type(user_message)
        concepts = self._available_concepts(ontology_engine)
        concept_by_id = {concept["id"]: concept for concept in concepts}
        selected_concepts = concept_context.get("relevant_concepts") or []
        selected_ids = {item.get("id") for item in selected_concepts if item.get("id")}
        selected_ids.update(concept_context.get("relevant_subject_domains") or [])
        if not selected_ids:
            return {}

        metrics = [
            metric
            for metric in ontology_engine.list_metrics()
            if metric.get("id") in set(relevant_metric_ids)
        ]
        scoped_metrics = self._metrics_in_selected_concepts(metrics, selected_ids, concepts)
        if not scoped_metrics:
            # Concept metadata is often incomplete during migration. Retain only
            # candidate metrics that are related to a selected Concept class.
            selected_classes = {
                concept.get("related_class")
                for concept in concepts
                if concept.get("id") in selected_ids and concept.get("related_class")
            }
            scoped_metrics = [
                metric for metric in metrics if metric.get("target_class") in selected_classes
            ]
        bundles = self._build_metric_bundles(scoped_metrics, ontology_engine)
        if not bundles:
            return {}

        fact_group_ids = [
            concept_id
            for concept_id in selected_ids
            if concept_by_id.get(concept_id, {}).get("concept_type") in {"fact_group", "metric_topic"}
        ]
        axis_ids = self._axis_ids(selected_ids, concepts, ontology_engine, bundles)
        return {
            "analysis_type": analysis_type,
            "domain_ids": list(dict.fromkeys(concept_context.get("relevant_subject_domains") or [])),
            "fact_group_ids": fact_group_ids,
            "selected_axis_concept_ids": axis_ids[:2],
            "deferred_axis_concept_ids": axis_ids[2:],
            "metric_bundles": bundles[:3],
            "required_evidence_roles": self._evidence_roles(analysis_type),
            "selection_reason": "已根据已审核 Concept、DimensionGroup 和 Metric 绑定生成受控分析候选。",
        }

    @staticmethod
    def _keywords(text: str) -> list[str]:
        keywords = []
        for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]{3,}", text or ""):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                keywords.extend(
                    token[start:end]
                    for start in range(len(token))
                    for end in range(start + 2, len(token) + 1)
                )
            else:
                keywords.append(token.lower())
        return list(dict.fromkeys(keywords))

    @staticmethod
    def _available_concepts(ontology_engine) -> list[dict]:
        return [
            concept
            for concept in ontology_engine.list_concepts()
            if str(concept.get("review_status") or "approved") != "rejected"
        ]

    def _rank_concepts(self, concepts: list[dict], keywords: list[str]) -> list[dict]:
        ranked = []
        for concept in concepts:
            text = " ".join(
                str(concept.get(field) or "")
                for field in ("id", "name", "description", "concept_type", "related_class")
            ).lower()
            score = sum(1 for keyword in keywords if keyword in text)
            if score:
                ranked.append((score, concept))
        return [concept for _, concept in sorted(ranked, key=lambda item: (-item[0], item[1]["id"]))[:12]]

    @staticmethod
    def _ancestor_ids(seed_ids: set[str], concepts: list[dict]) -> set[str]:
        parents = {str(concept.get("id") or ""): str(concept.get("parent_id") or "") for concept in concepts}
        ancestors = set(seed_ids)
        for concept_id in list(seed_ids):
            current = concept_id
            visited = set()
            while current and current not in visited:
                visited.add(current)
                current = parents.get(current, "")
                if current:
                    ancestors.add(current)
        return ancestors

    def _metrics_in_selected_concepts(
        self, metrics: list[dict], selected_ids: set[str], concepts: list[dict]
    ) -> list[dict]:
        selected_with_children = set(selected_ids)
        children = defaultdict(list)
        for concept in concepts:
            children[str(concept.get("parent_id") or "")].append(str(concept.get("id") or ""))
        stack = list(selected_ids)
        while stack:
            parent_id = stack.pop()
            for child_id in children.get(parent_id, []):
                if child_id and child_id not in selected_with_children:
                    selected_with_children.add(child_id)
                    stack.append(child_id)
        return [
            metric
            for metric in metrics
            if any(binding.get("concept_id") in selected_with_children for binding in metric.get("concept_bindings", []))
        ]

    def _build_metric_bundles(self, metrics: list[dict], ontology_engine) -> list[dict]:
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for metric in metrics:
            signature = self._compatibility_signature(metric, ontology_engine)
            grouped[signature].append(metric)
        bundles = []
        for index, (signature, grouped_metrics) in enumerate(sorted(grouped.items(), key=lambda item: str(item[0])), start=1):
            anchor_class, fixed_filters, shared_groups, source = signature
            metric_ids = [str(metric["id"]) for metric in grouped_metrics]
            roles = [
                binding.get("role")
                for metric in grouped_metrics
                for binding in metric.get("concept_bindings", [])
                if binding.get("role")
            ]
            bundles.append(
                {
                    "id": f"bundle-{index}",
                    "anchor_class": anchor_class,
                    "metric_ids": metric_ids,
                    "compatible_dimension_group_ids": list(shared_groups),
                    "fixed_filter_signature": fixed_filters,
                    "data_source": source,
                    "roles": list(dict.fromkeys(roles)),
                    "merge_policy": "single_query" if len(metric_ids) > 1 else "single_metric",
                }
            )
        return bundles

    def _compatibility_signature(self, metric: dict, ontology_engine) -> tuple:
        definition = metric.get("definition") or {}
        if not isinstance(definition, dict):
            definition = {}
        filters = []
        input_groups = [definition.get("inputs") or []]
        input_groups.extend(
            output.get("inputs") or []
            for output in definition.get("outputs") or []
            if isinstance(output, dict)
        )
        for inputs in input_groups:
            for item in inputs:
                if isinstance(item, dict):
                    filters.extend(item.get("filters") or [])
        normalized_filters = json.dumps(filters, ensure_ascii=False, sort_keys=True, default=str)
        group_ids = tuple(sorted(str(group_id) for group_id in metric.get("dimension_group_ids") or []))
        anchor_class = str(metric.get("target_class") or definition.get("anchor_class") or "")
        return anchor_class, normalized_filters, group_ids, ontology_engine.get_data_source(anchor_class)

    def _axis_ids(self, selected_ids: set[str], concepts: list[dict], ontology_engine, bundles: list[dict]) -> list[str]:
        bundle_group_ids = {group_id for bundle in bundles for group_id in bundle["compatible_dimension_group_ids"]}
        available_groups = {
            group.get("id"): group
            for group in ontology_engine.list_dimension_groups()
            if group.get("id") in bundle_group_ids
        }
        axes = []
        for concept in concepts:
            if concept.get("concept_type") not in {"analysis_axis", "dimension_group"}:
                continue
            if concept.get("id") not in selected_ids and not self._is_descendant_of_selected(concept, selected_ids, concepts):
                continue
            if any(group.get("concept_id") == concept.get("id") for group in available_groups.values()):
                axes.append(str(concept["id"]))
        return list(dict.fromkeys(axes))

    @staticmethod
    def _is_descendant_of_selected(concept: dict, selected_ids: set[str], concepts: list[dict]) -> bool:
        parents = {str(item.get("id") or ""): str(item.get("parent_id") or "") for item in concepts}
        current = str(concept.get("parent_id") or "")
        visited = set()
        while current and current not in visited:
            if current in selected_ids:
                return True
            visited.add(current)
            current = parents.get(current, "")
        return False

    @staticmethod
    def _analysis_type(user_message: str) -> str:
        question = (user_message or "").lower()
        for analysis_type, keywords in _ANALYSIS_TYPE_KEYWORDS.items():
            if any(keyword in question for keyword in keywords):
                return analysis_type
        return "overview"

    @staticmethod
    def _evidence_roles(analysis_type: str) -> list[str]:
        return {
            "attribution": ["baseline", "comparison", "decomposition"],
            "trend": ["baseline", "comparison"],
            "health": ["baseline", "risk_or_efficiency"],
            "comparison": ["baseline", "comparison"],
        }.get(analysis_type, ["baseline"])

    @staticmethod
    def _concept_context(concepts: list[dict], groups: list[dict]) -> str:
        if not concepts:
            return ""
        lines = ["## 候选业务 Concept"]
        for concept in concepts:
            lines.append(
                f"- {concept.get('id')}（{concept.get('name')}，类型={concept.get('concept_type')}）: {concept.get('description', '')}"
            )
        if groups:
            lines.append("## 可执行分析维度组")
            for group in groups:
                lines.append(f"- {group.get('id')}（{group.get('name')}）→ Concept={group.get('concept_id')}")
        return "\n".join(lines)
