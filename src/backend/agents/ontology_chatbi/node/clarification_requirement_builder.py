"""Scope-aware clarification requirements and answer bindings."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict


class ClarificationRequirementBuilder:
    """Build, validate, and bind governed clarification requirements.

    The builder never trusts a group-level answer by itself. Each requirement
    carries the Metric and in-scope field mappings that may consume its answer.
    """

    VERSION = 3

    def build(
        self,
        execution_units: list[dict],
        user_message: str,
        answer_ledger: list[dict],
        ontology_engine,
    ) -> list[dict]:
        groups = {
            str(group.get("id") or ""): group
            for group in ontology_engine.list_dimension_groups()
            if group.get("status") == "approved"
        }
        metrics = {str(metric.get("id") or ""): metric for metric in ontology_engine.list_metrics()}
        requirements: OrderedDict[str, dict] = OrderedDict()
        for unit in execution_units:
            scope = unit.get("query_scope") or {}
            plan = unit.get("query_plan") or {}
            metric_refs = [str(item) for item in plan.get("metrics") or unit.get("metric_refs") or []]
            group_ids = []
            for metric_ref in metric_refs:
                metric = metrics.get(metric_ref)
                if metric:
                    group_ids.extend(metric.get("dimension_group_ids") or [])
            for group_id in dict.fromkeys(str(item) for item in group_ids):
                group = groups.get(group_id)
                if not group or not group.get("is_required"):
                    continue
                mappings = self._scope_mappings(group, scope)
                if not mappings:
                    continue
                requirement = self._requirement(group, mappings, unit, metric_refs)
                key = requirement["requirement_id"]
                existing = requirements.get(key)
                if existing:
                    existing["required_by"].extend(requirement["required_by"])
                    continue
                self._resolve(requirement, plan, user_message, answer_ledger)
                requirements[key] = requirement
        return list(requirements.values())

    def validate_answers(self, requirements: list[dict], submitted_answers: list[dict]) -> list[dict]:
        """Validate submitted values against the checkpoint's immutable requirements."""
        requirement_by_id = {str(item.get("requirement_id") or ""): item for item in requirements}
        accepted = []
        for answer in submitted_answers or []:
            if not isinstance(answer, dict):
                continue
            requirement_id = str(answer.get("requirement_id") or "")
            requirement = requirement_by_id.get(requirement_id)
            if not requirement:
                continue
            option_value = str(answer.get("option_value") or "")
            options = {str(option.get("value") or "") for option in requirement.get("options", [])}
            if option_value not in options:
                continue
            selection_value = str(answer.get("selection_value") or "").strip().upper()
            if requirement.get("requires_value") and not self._valid_value(option_value, selection_value):
                continue
            accepted.append(
                {
                    "answer_id": f"answer-{len(accepted) + 1}",
                    "requirement_id": requirement_id,
                    "group_id": requirement["group_id"],
                    "semantic_role": requirement["semantic_role"],
                    "option_value": option_value,
                    "selection_value": selection_value,
                    "provenance": "user_confirmed",
                    "clarification_version": self.VERSION,
                }
            )
        return accepted

    def bind_answers(self, execution_unit: dict, answer_ledger: list[dict], requirements: list[dict]) -> dict:
        """Inject only compatible confirmed/auto-resolved answers into one unit."""
        unit = {**execution_unit, "query_plan": {**(execution_unit.get("query_plan") or {})}}
        plan = unit["query_plan"]
        filters = list(plan.get("filters") or [])
        dimensions = list(plan.get("dimensions") or [])
        bindings = []
        unit_id = str(unit.get("unit_id") or "")
        for requirement in requirements:
            applies = [item for item in requirement.get("required_by", []) if item.get("unit_id") == unit_id]
            if not applies:
                continue
            answer = self._answer_for_requirement(requirement, answer_ledger)
            if not answer:
                continue
            mapping = next(
                (item for item in requirement.get("mappings", []) if item.get("option_value") == answer["option_value"]),
                None,
            )
            if not mapping:
                continue
            field = str(mapping.get("field_name") or "")
            if not field:
                continue
            if answer.get("selection_value"):
                if not any(str(item.get("field") or "") == field for item in filters if isinstance(item, dict)):
                    answer_provenance = str(answer.get("provenance") or "")
                    filters.append(
                        {
                            "field": field,
                            "operator": "=",
                            "value": answer["selection_value"],
                            "_provenance": (
                                "user_explicit"
                                if answer_provenance == "user_explicit"
                                else "clarification_answer"
                            ),
                            "_answer_provenance": answer_provenance,
                            "_answer_id": answer.get("answer_id"),
                            "_locked": True,
                        }
                    )
            elif field not in dimensions:
                dimensions.append(field)
            bindings.append({"requirement_id": requirement["requirement_id"], "answer_id": answer.get("answer_id"), "field": field})
        plan["filters"] = filters
        plan["dimensions"] = dimensions
        unit["query_plan"] = plan
        unit["answer_bindings"] = bindings
        return unit

    def build_card(self, requirements: list[dict], stage: str = "final") -> dict:
        unresolved = [item for item in requirements if item.get("resolution_status") in {"unresolved", "conflict", "invalid"}]
        questions = [
            {
                "requirement_id": item["requirement_id"],
                "group_id": item["group_id"],
                "group_name": item["group_name"],
                "group_type": item["group_type"],
                "semantic_role": item["semantic_role"],
                "metric_ids": list(dict.fromkeys(ref.get("metric_ref") for ref in item["required_by"] if ref.get("metric_ref"))),
                "execution_unit_ids": list(dict.fromkeys(ref.get("unit_id") for ref in item["required_by"] if ref.get("unit_id"))),
                "required": True,
                "requires_value": item["requires_value"],
                "value_label": "统计期间" if item["requires_value"] else "",
                "options": item["options"],
                "candidate_values": item.get("candidate_values", []),
                "semantic_suggestions": item.get("semantic_suggestions", []),
                "reason": item.get("resolution_reason", ""),
            }
            for item in unresolved
        ]
        first = questions[0] if questions else {}
        return {
            "version": self.VERSION,
            "reason": "clarification_requirements",
            "stage": stage,
            "clarification_version": self.VERSION,
            "question": "请确认以下统计口径后继续查询。",
            "questions": questions,
            "field": first.get("requirement_id", ""),
            "multi_select": len(questions) > 1,
            "options": first.get("options", []),
        }

    @staticmethod
    def _scope_mappings(group: dict, scope: dict) -> list[dict]:
        target = str(scope.get("target_class") or "")
        joins = {str(item) for item in scope.get("join_classes") or []}
        ranked = []
        for index, mapping in enumerate(group.get("field_mappings") or []):
            class_id = str(mapping.get("class_id") or "")
            if class_id != target and class_id not in joins:
                continue
            ranked.append({**mapping, "_rank": 0 if class_id == target else 1, "_index": index})
        return sorted(ranked, key=lambda item: (item["_rank"], int(item.get("priority") or 0), item["_index"]))

    def _requirement(self, group: dict, mappings: list[dict], unit: dict, metric_refs: list[str]) -> dict:
        semantic_role = str(group.get("semantic_role") or group.get("id") or "")
        schema_revision = self._schema_revision(group, mappings)
        signature = {
            "group": group.get("id"),
            "role": semantic_role,
            "schema_revision": schema_revision,
            "mappings": [
                {"class_id": item.get("class_id"), "field_name": item.get("field_name"), "option_value": item.get("option_value")}
                for item in mappings
            ],
        }
        digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        options = [
            {"id": option.get("value"), "value": option.get("value"), "label": option.get("label"), "is_default": bool(option.get("is_default"))}
            for option in group.get("options", [])
            if option.get("status", "approved") == "approved" and any(
                str(mapping.get("option_value") or "") == str(option.get("value") or "") for mapping in mappings
            )
        ]
        return {
            "requirement_id": f"req:v{self.VERSION}:{digest}",
            "group_id": str(group.get("id") or ""),
            "group_name": str(group.get("name") or group.get("id") or "必要维度"),
            "group_type": str(group.get("group_type") or ""),
            "semantic_role": semantic_role,
            "schema_revision": schema_revision,
            "mappings": mappings,
            "options": options,
            "requires_value": group.get("group_type") == "time",
            "required_by": [
                {
                    "unit_id": str(unit.get("unit_id") or ""),
                    "metric_ref": metric_ref,
                    "target_class": str((unit.get("query_scope") or {}).get("target_class") or ""),
                }
                for metric_ref in metric_refs
            ],
            "candidate_values": [],
            "resolution_status": "unresolved",
            "resolution_reason": "missing_required_dimension",
        }

    @staticmethod
    def _schema_revision(group: dict, mappings: list[dict]) -> str:
        """Fingerprint only the approved configuration that governs this requirement."""
        payload = {
            "group_id": group.get("id"),
            "group_type": group.get("group_type"),
            "semantic_role": group.get("semantic_role"),
            "options": [
                {
                    "value": item.get("value"),
                    "status": item.get("status", "approved"),
                }
                for item in group.get("options", [])
            ],
            "mappings": [
                {
                    "class_id": item.get("class_id"),
                    "field_name": item.get("field_name"),
                    "option_value": item.get("option_value"),
                    "priority": item.get("priority"),
                }
                for item in mappings
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]

    def _resolve(self, requirement: dict, plan: dict, user_message: str, answers: list[dict]) -> None:
        for item in plan.get("filters") or []:
            if not isinstance(item, dict):
                continue
            mapping = next((mapping for mapping in requirement["mappings"] if mapping.get("field_name") == item.get("field")), None)
            if mapping:
                requirement.update(
                    resolution_status="resolved",
                    resolution_reason="validated_query_plan",
                    resolved_answer={"option_value": mapping.get("option_value"), "selection_value": str(item.get("value") or ""), "provenance": "query_plan"},
                )
                return
        for answer in answers:
            if answer.get("requirement_id") == requirement["requirement_id"]:
                requirement.update(resolution_status="resolved", resolution_reason="answer_ledger", resolved_answer=answer)
                return
        candidates = self._requirement_candidates(requirement, user_message)
        requirement["candidate_values"] = candidates
        if requirement["group_type"] == "time":
            if len(candidates) == 1 and self._valid_value(candidates[0]["option_value"], candidates[0]["selection_value"]):
                requirement.update(resolution_status="resolved", resolution_reason="requirement_candidate", resolved_answer={**candidates[0], "provenance": "user_explicit"})
                return
            if len(candidates) > 1:
                requirement.update(resolution_status="conflict", resolution_reason="multiple_time_candidates")

    def _requirement_candidates(self, requirement: dict, user_message: str) -> list[dict]:
        if requirement.get("group_type") == "time":
            return self._time_candidates(requirement, user_message)
        descriptor = " ".join(
            str(requirement.get(key) or "")
            for key in ("group_id", "group_name", "semantic_role")
        ).lower()
        parsers = (
            (r"规格|强度|剂量|strength|dosage|dose", "product_strength", re.compile(r"(?<!\d)(\d+(?:\.\d+)?\s*mg)(?![a-z])", re.IGNORECASE)),
            (r"省|市|地区|大区|区域|地理|geography|region|area", "geography_text", re.compile(r"([\u4e00-\u9fff]{2,}(?:省|市|自治区|地区|大区))")),
            (r"员工|人员|角色|employee|\bbd\b|\brm\b|\bsd\b|\bdm\b|mics", "employee_role", re.compile(r"\b(BD|RM|SD|DM|MICS)\b", re.IGNORECASE)),
        )
        for pattern, kind, extractor in parsers:
            if re.search(pattern, descriptor, re.IGNORECASE):
                return self._text_candidates(requirement, user_message, kind, extractor)
        return []

    @staticmethod
    def _text_candidates(requirement: dict, user_message: str, kind: str, pattern: re.Pattern) -> list[dict]:
        candidates = []
        for match in pattern.finditer(str(user_message or "")):
            raw_text = match.group(1)
            candidates.append(
                {
                    "candidate_id": f"{requirement['requirement_id']}:candidate:{len(candidates)}",
                    "kind": kind,
                    "raw_text": raw_text,
                    "selection_value": re.sub(r"\s+", "", raw_text).upper(),
                    "source": "requirement_parser",
                }
            )
        return candidates

    @staticmethod
    def _time_candidates(requirement: dict, user_message: str) -> list[dict]:
        """Parse only values permitted by this already-built time requirement."""
        text = str(user_message or "")
        patterns = (
            ("month", re.compile(r"(?<!\d)(\d{4}AP(?:0[1-9]|1[0-2]))(?!\d)", re.IGNORECASE)),
            ("quarter", re.compile(r"(?<!\d)(\d{4}Q[1-4])(?!\d)", re.IGNORECASE)),
            ("year", re.compile(r"(?<!\d)(\d{4})年", re.IGNORECASE)),
        )
        allowed_options = {str(mapping.get("option_value") or "") for mapping in requirement.get("mappings", [])}
        candidates = []
        for option_value, pattern in patterns:
            if option_value not in allowed_options:
                continue
            for match in pattern.finditer(text):
                selection_value = match.group(1).upper()
                candidates.append(
                    {
                        "candidate_id": f"{requirement['requirement_id']}:candidate:{len(candidates)}",
                        "kind": "time_period",
                        "option_value": option_value,
                        "selection_value": selection_value,
                        "raw_text": match.group(0),
                        "source": "requirement_parser",
                    }
                )
        if not candidates and "quarter" in allowed_options:
            for match in re.finditer(r"(?<!\d)(\d{4})AP(0[1-9]|1[0-2])(?!\d)", text, re.IGNORECASE):
                year, month = match.groups()
                quarter = (int(month) - 1) // 3 + 1
                candidates.append(
                    {
                        "candidate_id": f"{requirement['requirement_id']}:candidate:{len(candidates)}",
                        "kind": "time_period",
                        "option_value": "quarter",
                        "selection_value": f"{year}Q{quarter}",
                        "raw_text": match.group(1),
                        "source": "derived_from_ap",
                    }
                )
        return candidates

    def _answer_for_requirement(self, requirement: dict, answers: list[dict]) -> dict | None:
        if requirement.get("resolution_status") == "resolved" and requirement.get("resolved_answer"):
            resolved = dict(requirement["resolved_answer"])
            resolved.setdefault("answer_id", f"auto-{requirement['requirement_id']}")
            return resolved
        return next((item for item in answers if item.get("requirement_id") == requirement["requirement_id"]), None)

    @staticmethod
    def _valid_value(option_value: str, value: str) -> bool:
        patterns = {"month": r"^\d{4}AP(?:0[1-9]|1[0-2])$", "quarter": r"^\d{4}Q[1-4]$", "year": r"^\d{4}$"}
        return bool(re.fullmatch(patterns.get(option_value, r".+"), str(value or "").upper()))
