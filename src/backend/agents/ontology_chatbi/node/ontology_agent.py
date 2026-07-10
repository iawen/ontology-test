"""Ontology planning agent for schema scope and query arguments."""

import json
import re
import time
from typing import cast

from openai.types.chat import ChatCompletionMessageParam

from agents.ontology_chatbi.helper import metric_target_classes
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
        feedback: str = "",
        session_id: str = "",
    ) -> dict:
        """Identify and validate the target class plus explicit join classes."""
        payload = await self._request_planning_json(
            "schema_scope",
            get_schema_scope_planning_prompt(user_message, schema_context, self._json_dumps(glossary_matches)),
            feedback,
            session_id,
        )
        return self.validate_query_scope(payload, ontology_engine)

    async def plan_query_details(
        self,
        user_message: str,
        query_scope: dict,
        metric_candidates: list[str],
        ontology_engine,
        query_engine,
        feedback: str = "",
        session_id: str = "",
    ) -> dict:
        """Extract and validate metrics, dimensions, filters, and ordering within a schema scope."""
        scope_context = self.build_scope_context(query_scope, ontology_engine, metric_candidates)
        payload = await self._request_planning_json(
            "query_details",
            get_query_details_planning_prompt(user_message, scope_context),
            feedback,
            session_id,
        )
        return self.validate_query_plan(payload, query_scope, ontology_engine, query_engine)

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
    def validate_query_scope(payload: dict, engine) -> dict:
        target_class = str(payload.get("target_class") or "").strip()
        known_classes = {str(item.get("id") or "") for item in engine.list_classes()}
        if not target_class or target_class not in known_classes:
            return {"valid": False, "error": f"target_class 不存在：{target_class or '<empty>'}"}
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
            path = engine.get_join_path(target_class, class_id)
            if not path:
                return {"valid": False, "error": f"{target_class} 与 {class_id} 不存在 JOIN 路径"}
            join_classes.append(class_id)
            join_paths[class_id] = path
        return {"valid": True, "target_class": target_class, "join_classes": join_classes, "join_paths": join_paths}

    @staticmethod
    def validate_query_plan(payload: dict, scope: dict, engine, query_engine) -> dict:
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

        join_classes = list(scope.get("join_classes") or [])
        join_paths = dict(scope.get("join_paths") or {})
        allowed_classes = [target_class, *join_classes]

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

        for item in filters:
            if engine.get_metric_info(str(item.get("field") or "")):
                return {"valid": False, "error": f"指标条件必须放入 having：{item['field']}"}

        for metric in metrics:
            metric_info = engine.get_metric_info(metric)
            if metric_info:
                for class_id in metric_target_classes(metric_info):
                    if error := add_dependency(class_id, f"指标 {metric}"):
                        return {"valid": False, "error": error}
            elif not any(query_engine.field_available_in_class(class_id, metric) for class_id in allowed_classes):
                return {"valid": False, "error": f"指标或字段不存在于当前 Schema Scope：{metric}"}

        sources = (("维度", dimensions, False), ("过滤字段", filters, False), ("HAVING", having, True))
        for source, items, is_metric in sources:
            for item in items:
                field = str(item.get("field") if isinstance(item, dict) else item).strip()
                metric_info = engine.get_metric_info(field)
                if metric_info and (is_metric or source == "维度"):
                    for class_id in metric_target_classes(metric_info):
                        if error := add_dependency(class_id, f"{source} {field}"):
                            return {"valid": False, "error": error}
                    continue
                candidates = [
                    class_id for class_id in allowed_classes if query_engine.field_available_in_class(class_id, field)
                ]
                if not candidates:
                    external = query_engine.candidate_field_classes(field, target_class)
                    if not external:
                        return {"valid": False, "error": f"{source} 不存在：{field}"}
                    if error := add_dependency(external[0], f"{source} {field}"):
                        return {"valid": False, "error": error}

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
    def build_scope_context(scope: dict, engine, metric_candidates: list[str] | None = None) -> str:
        scope_classes = [scope.get("target_class"), *(scope.get("join_classes") or [])]
        class_blocks = []
        for class_id in scope_classes:
            if not class_id:
                continue
            info = engine.get_class_info(class_id)
            field_types = engine.get_field_types(class_id)
            fields = [f"{name}({field_types.get(name, 'text')})" for name in engine.get_field_map(class_id)]
            class_blocks.append(
                f"- {class_id}: {info.get('name_cn', '')}\n"
                f"  说明: {info.get('description', '')}\n"
                f"  字段: {', '.join(fields[:80])}"
            )
        metric_blocks = []
        scope_set = {class_id for class_id in scope_classes if class_id}
        metrics = engine.list_metrics()
        metric_by_id = {str(metric.get("id") or ""): metric for metric in metrics}
        candidate_metrics = [
            metric_by_id[metric_id] for metric_id in metric_candidates or [] if metric_id in metric_by_id
        ]
        scoped_candidates = [metric for metric in candidate_metrics if set(metric_target_classes(metric)) & scope_set]
        for metric in scoped_candidates:
            metric_classes = set(metric_target_classes(metric))
            if metric_classes & scope_set:
                metric_blocks.append(
                    f"- {metric.get('name') or metric.get('id')}: target={','.join(metric_classes)}; "
                    f"formula={metric.get('formula') or metric.get('calculation', '')}; "
                    f"description={metric.get('description', '')}"
                )
        return "## Class\n" + "\n".join(class_blocks) + "\n\n## Metrics\n" + "\n".join(metric_blocks[:120])

    @staticmethod
    def _json_dumps(value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)
