"""Ontology planning agent for schema scope and query arguments."""

import json
import re
import time
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from agents.ontology_chatbi.helper import (
    metric_context_summary,
    metric_definition,
    resolve_metric_reference,
)
from agents.ontology_chatbi.prompt import (
    ONTOLOGY_PLANNING_SYSTEM_PROMPT,
    get_ontology_planning_feedback_prompt,
    get_query_details_planning_prompt,
    get_schema_scope_planning_prompt,
)
from tools.logger import logger


class OntologyAgent:
    """Plan and validate ontology query scope and query arguments without mutating chat state."""

    def __init__(self, client=None, model_name: str = ""):
        self.client = client
        self.model_name = model_name

    async def plan_schema_scope(
        self,
        user_message: str,
        schema_context: str,
        glossary_matches: list[dict],
        ontology_engine,
        candidate_class_ids: list[str] | None = None,
        feedback: str = "",
        session_id: str = "",
    ) -> dict:
        """Identify and validate the target class plus explicit join classes.

        This stage intentionally does not receive Metric information. Metric
        selection and Metric-driven dependency expansion happen only after the
        Schema Scope has been validated.
        """
        candidate_ids = self._valid_candidate_class_ids(
            candidate_class_ids or [], ontology_engine
        )
        payload = await self._request_planning_json(
            "schema_scope",
            get_schema_scope_planning_prompt(
                user_message,
                self._candidate_schema_context(ontology_engine, candidate_ids) if candidate_ids else schema_context,
                self._json_dumps(glossary_matches),
            ),
            feedback,
            session_id,
        )
        return self.validate_query_scope(
            payload, ontology_engine, allowed_class_ids=candidate_ids or None
        )

    @staticmethod
    def _valid_candidate_class_ids(candidate_class_ids: list[str], ontology_engine) -> list[str]:
        known_classes = {str(item.get("id") or "") for item in ontology_engine.list_classes()}
        return list(
            dict.fromkeys(
                class_id
                for item in candidate_class_ids
                if (class_id := str(item or "").strip()) in known_classes
            )
        )

    @staticmethod
    def _candidate_schema_context(ontology_engine, candidate_class_ids: list[str]) -> str:
        """Render only routing-selected classes for the Schema Scope planner."""
        class_by_id = {
            str(item.get("id") or ""): item for item in ontology_engine.list_classes()
        }
        parts = ["## 路由候选实体类（仅可从此集合选择）"]
        for class_id in candidate_class_ids:
            schema_class = class_by_id[class_id]
            parts.append(
                f"- **{class_id}**({schema_class.get('name_cn', '')}): "
                f"{schema_class.get('description', '')}"
            )
        relationships = [
            relationship
            for relationship in ontology_engine.list_relationships()
            if relationship.get("source") in candidate_class_ids
            and relationship.get("target") in candidate_class_ids
        ]
        if relationships:
            parts.append("## 候选实体间关系")
            parts.extend(
                f"- {relationship['source']} --[{relationship.get('type', '')}]--> {relationship['target']}"
                for relationship in relationships
            )
        return "\n".join(parts)

    async def plan_query_details(
        self,
        user_message: str,
        query_scope: dict,
        metric_candidates: list[str],
        ontology_engine,
        query_engine,
        reusable_query_plan: dict | None = None,
        reuse_metrics: bool = False,
        trusted_reusable_filters: list[dict] | None = None,
        feedback: str = "",
        session_id: str = "",
    ) -> dict:
        """Extract and validate metrics, dimensions, filters, and ordering within a schema scope."""
        scope_context = self.build_scope_context(query_scope, ontology_engine, metric_candidates)
        payload = await self._request_planning_json(
            "query_details",
            get_query_details_planning_prompt(
                user_message,
                scope_context,
                self._json_dumps(reusable_query_plan) if reusable_query_plan else "",
                reuse_metrics,
            ),
            feedback,
            session_id,
        )
        if reusable_query_plan:
            payload = self._merge_reusable_query_plan(
                reusable_query_plan, payload, reuse_metrics
            )
        return self.validate_query_plan(
            payload,
            query_scope,
            metric_candidates,
            ontology_engine,
            query_engine,
            trusted_filters=trusted_reusable_filters,
        )

    @staticmethod
    def _merge_reusable_query_plan(
        parent_plan: dict, delta_plan: dict, reuse_metrics: bool
    ) -> dict:
        """Build a complete child plan from a validated parent plus LLM delta.

        Parent conditions are intentionally retained. The executor later enforces
        their resolved field/value pairs while it aligns only the new conditions.
        """
        parent = parent_plan if isinstance(parent_plan, dict) else {}
        delta = delta_plan if isinstance(delta_plan, dict) else {}

        def merge_list(key: str) -> list:
            base = parent.get(key) if isinstance(parent.get(key), list) else []
            additions = delta.get(key) if isinstance(delta.get(key), list) else []
            return [*base, *additions]

        return {
            "query_mode": delta.get("query_mode") or parent.get("query_mode") or "aggregate",
            "metrics": list(parent.get("metrics") or []) if reuse_metrics else list(delta.get("metrics") or []),
            "dimensions": merge_list("dimensions"),
            "filters": merge_list("filters"),
            "having": merge_list("having"),
            "order_by": delta.get("order_by") or parent.get("order_by") or "",
        }

    async def _request_planning_json(self, stage: str, instruction: str, feedback: str, session_id: str) -> dict:
        if self.client is None:
            raise RuntimeError("OntologyAgent requires an LLM client for planning")
        messages = [{"role": "system", "content": ONTOLOGY_PLANNING_SYSTEM_PROMPT}]
        if feedback:
            instruction += get_ontology_planning_feedback_prompt(feedback)
        messages.append({"role": "user", "content": instruction})
        started_at = time.time()
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=cast(list[ChatCompletionMessageParam], messages),
            temperature=0.1,
            max_tokens=1200,
        )
        payload = self.parse_planning_json(response.choices[0].message.content or "")
        logger.info(
            "Ontology planning stage completed: session_id=%s stage=%s duration_ms=%d valid_json=%s",
            session_id,
            stage,
            int((time.time() - started_at) * 1000),
            bool(payload),
        )
        return payload

    @staticmethod
    def parse_planning_json(raw: str) -> dict:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def validate_query_scope(
        payload: dict, engine, allowed_class_ids: list[str] | None = None
    ) -> dict:
        target_class = str(payload.get("target_class") or "").strip()
        known_classes = {str(item.get("id") or "") for item in engine.list_classes()}
        allowed_classes = set(allowed_class_ids or known_classes)
        if not target_class or target_class not in known_classes:
            return {"valid": False, "error": f"target_class 不存在：{target_class or '<empty>'}"}
        if target_class not in allowed_classes:
            return {"valid": False, "error": f"target_class 不属于路由候选实体：{target_class}"}
        raw_join_classes = payload.get("join_classes") or []
        if not isinstance(raw_join_classes, list):
            return {"valid": False, "error": "join_classes 必须是数组"}
        join_classes = []
        join_paths = {}
        for value in raw_join_classes:
            class_id = str(value or "").strip()
            if not class_id or class_id == target_class or class_id in join_classes:
                continue
            if class_id not in known_classes:
                return {"valid": False, "error": f"join_class 不存在：{class_id}"}
            if class_id not in allowed_classes:
                return {"valid": False, "error": f"join_class 不属于路由候选实体：{class_id}"}
            path = engine.get_join_path(target_class, class_id)
            if not path:
                return {"valid": False, "error": f"{target_class} 与 {class_id} 不存在 JOIN 路径"}
            join_classes.append(class_id)
            join_paths[class_id] = path
        return {"valid": True, "target_class": target_class, "join_classes": join_classes, "join_paths": join_paths}

    @staticmethod
    def validate_query_plan(
        payload: dict,
        scope: dict,
        metric_candidates: list[str],
        engine,
        query_engine,
        trusted_filters: list[dict] | None = None,
    ) -> dict:
        target_class = str(scope.get("target_class") or "")
        query_mode = str(payload.get("query_mode") or "aggregate").lower()
        if query_mode not in {"aggregate", "detail"}:
            return {"valid": False, "error": "query_mode 只能是 aggregate 或 detail"}
        metrics = payload.get("metrics") or []
        dimensions = payload.get("dimensions") or []
        filters = payload.get("filters") or []
        having = payload.get("having") or []
        if not all(isinstance(value, list) for value in (metrics, dimensions, filters, having)):
            return {"valid": False, "error": "metrics、dimensions、filters、having 必须是数组"}
        if query_mode == "aggregate" and not metrics and not dimensions:
            return {"valid": False, "error": "aggregate 查询至少需要一个 metrics 或 dimensions"}
        if not all(isinstance(item, str) and item.strip() for item in [*metrics, *dimensions]):
            return {"valid": False, "error": "metrics 和 dimensions 只能包含非空逻辑名称"}
        all_conditions = [*filters, *having]
        if not all(isinstance(item, dict) and item.get("field") and item.get("operator") for item in all_conditions):
            return {"valid": False, "error": "filters 和 having 每项都必须包含 field、operator"}

        available_metrics = OntologyAgent._target_class_metrics(scope, engine, metric_candidates)
        available_metric_ids = {str(metric.get("id") or "") for metric in available_metrics}
        join_classes = list(scope.get("join_classes") or [])
        join_paths = dict(scope.get("join_paths") or {})
        allowed_classes = [target_class, *join_classes]
        trusted_filter_keys = {
            OntologyAgent._filter_key(item)
            for item in trusted_filters or []
            if isinstance(item, dict)
        }

        def is_scope_field(field_name: str) -> bool:
            return any(field_name in engine.get_field_map(class_id) for class_id in allowed_classes)

        def validate_candidate_metric(metric_name: str, source: str) -> tuple[dict | None, str]:
            metric_info, output = resolve_metric_reference(metric_name, available_metrics)
            resolved_id = str(output.get("id") or "") if output else str(metric_info.get("id") or "") if metric_info else ""
            if (
                metric_info
                and str(metric_info.get("id") or "") in available_metric_ids
                and str(metric_name).strip() == resolved_id
            ):
                return None, str(output.get("id")) if output else str(metric_info.get("id") or metric_name)
            if not metric_info:
                return {
                    "valid": False,
                    "error": f"{source} 必须填写当前 Class 的 Metrics 列表中展示的 Metric 或并列输出 ID：{metric_name}",
                }, metric_name
            return {
                "valid": False,
                "error": f"{source} 必须填写 Metric 或并列输出 ID，不能填写名称：{metric_name}",
            }, metric_name

        resolved_metrics = []
        for metric in metrics:
            error, resolved_metric = validate_candidate_metric(metric, "metrics")
            if error:
                return error
            resolved_metrics.append(resolved_metric)
        metrics = resolved_metrics
        for item in having:
            field = str(item.get("field") or "").strip()
            error, resolved_metric = validate_candidate_metric(field, "having.field")
            if error:
                return error
            item["field"] = resolved_metric

        for field in dimensions:
            if not is_scope_field(field):
                logger.warning(
                    "Preserving invalid dimension field for downstream handling: target_class=%s field=%s",
                    target_class,
                    field,
                )

        for item in filters:
            if OntologyAgent._filter_key(item) in trusted_filter_keys:
                continue
            field = str(item.get("field") or "").strip()
            resolved_metric, _ = resolve_metric_reference(field, available_metrics)
            if resolved_metric or not is_scope_field(field):
                logger.warning(
                    "Preserving invalid filter field for downstream handling: target_class=%s field=%s",
                    target_class,
                    field,
                )

        def add_dependency(class_id: str, source: str) -> str | None:
            if not class_id or class_id == target_class or class_id in allowed_classes:
                return None
            path = engine.get_join_path(target_class, class_id)
            if not path:
                return f"{source} 解析到 {class_id}，但与 {target_class} 不存在 JOIN 路径"
            allowed_classes.append(class_id)
            join_classes.append(class_id)
            join_paths[class_id] = path
            return None

        for metric in metrics:
            metric_info, selected_output = resolve_metric_reference(metric, available_metrics)
            if metric_info:
                definition = metric_definition(metric_info)
                source_items = selected_output.get("inputs", []) if selected_output else [
                    input_item
                    for output in definition.get("outputs", [])
                    if isinstance(output, dict)
                    for input_item in output.get("inputs", [])
                ] if definition.get("version") == 2 else definition.get("inputs", [])
                classes = [definition.get("anchor_class") or metric_info.get("target_class")]
                classes.extend(item.get("class_id") for item in source_items if isinstance(item, dict))
                for class_id in dict.fromkeys(str(value or "") for value in classes):
                    if error := add_dependency(class_id, f"指标 {metric}"):
                        return {"valid": False, "error": error}
            elif not any(query_engine.field_available_in_class(class_id, metric) for class_id in allowed_classes):
                return {"valid": False, "error": f"指标或字段不存在于当前 Schema Scope：{metric}"}

        return {
            "valid": True,
            "query_scope": {"target_class": target_class, "join_classes": join_classes, "join_paths": join_paths},
            "query_plan": {
                "metrics": metrics,
                "dimensions": dimensions,
                "filters": filters,
                "having": having,
                "order_by": str(payload.get("order_by") or ""),
            },
        }

    @staticmethod
    def _filter_key(item: dict) -> str:
        """Stable condition identity used to exempt already-executed parent filters."""
        return json.dumps(
            {
                "field": item.get("field"),
                "operator": str(item.get("operator") or "").upper(),
                "value": item.get("value"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def build_scope_context(scope: dict, engine, metric_candidates: list[str] | None = None) -> str:
        scope_classes = [scope.get("target_class"), *(scope.get("join_classes") or [])]
        class_blocks = []
        for class_id in scope_classes:
            if not class_id:
                continue
            info = engine.get_class_info(class_id)
            field_types = engine.get_field_types(class_id)
            fields = [
                f"{logical}(表字段={physical}; {field_types.get(physical, 'text')})"
                for logical, physical in engine.get_field_map(class_id).items()
            ]
            class_blocks.append(
                f"- {class_id}: {info.get('name_cn', '')}\n"
                f"  说明: {info.get('description', '')}\n"
                f"  字段: {', '.join(fields[:80])}"
            )
        metric_blocks = []
        target_metrics = OntologyAgent._target_class_metrics(scope, engine, metric_candidates or [])
        for metric in target_metrics:
            metric_blocks.append(f"- {metric_context_summary(metric)}")
        logger.info(
            "Query detail scope context built: target_class=%s class_count=%d metric_count=%d",
            scope.get("target_class"),
            len(class_blocks),
            len(target_metrics),
        )
        return (
            "## Class\n"
            + "\n".join(class_blocks)
            + "\n\n## Metrics（当前 target_class 可用指标；metrics/having.field 只能从此列表选择）\n"
            + "\n".join(metric_blocks or ["（当前 target_class 未配置可用 Metric）"])
        )

    @staticmethod
    def _target_class_metrics(scope: dict, engine, preferred_metric_ids: list[str]) -> list[dict]:
        """Return every Metric owned by target_class, ordering retrieval candidates first."""
        target_class = str(scope.get("target_class") or "")
        preferred = {str(metric_id) for metric_id in preferred_metric_ids}
        metrics = [
            metric
            for metric in engine.list_metrics()
            if str(metric_definition(metric).get("anchor_class") or metric.get("target_class") or "") == target_class
        ]
        return sorted(metrics, key=lambda metric: 0 if str(metric.get("id") or "") in preferred else 1)

    @staticmethod
    def _json_dumps(value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)
