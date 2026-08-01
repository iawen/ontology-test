"""Deterministic clarification policy for governed Metric queries."""

from collections import OrderedDict
import re

from agents.ontology_chatbi.services.helper import resolve_metric_reference


class ClarifyAgent:
    """Resolve governed DimensionGroups before execution without using an LLM."""

    def resolve_dimension_groups(
        self,
        query_plan: dict,
        user_message: str,
        ontology_engine,
        session_selections: dict[str, dict] | None = None,
    ) -> dict:
        """Return deterministic selections plus only the required groups still unresolved."""
        session_selections = session_selections or {}
        metrics = ontology_engine.list_metrics()
        groups_by_id = {
            str(group.get("id")): group
            for group in ontology_engine.schema.get("dimension_groups", [])
            if group.get("status") == "approved"
        }
        required: OrderedDict[str, dict] = OrderedDict()
        legacy_metrics = []
        for metric_ref in query_plan.get("metrics", []):
            metric, output = resolve_metric_reference(str(metric_ref).strip(), metrics)
            if not metric:
                continue
            group_ids = [group_id for group_id in metric.get("dimension_group_ids") or [] if group_id in groups_by_id]
            if not group_ids:
                legacy_metrics.append(metric_ref)
                continue
            for group_id in group_ids:
                group = groups_by_id[group_id]
                if not group.get("is_required"):
                    continue
                item = required.setdefault(group_id, {**group, "metric_ids": [], "metric_names": [], "output_names": []})
                metric_id = str(metric.get("id") or metric_ref)
                metric_name = str(metric.get("name") or metric_id)
                output_name = str((output or {}).get("output_name") or "")
                if metric_id not in item["metric_ids"]:
                    item["metric_ids"].append(metric_id)
                if metric_name not in item["metric_names"]:
                    item["metric_names"].append(metric_name)
                if output_name and output_name not in item["output_names"]:
                    item["output_names"].append(output_name)

        resolved, unresolved, audit = [], [], []
        for group in required.values():
            selection, source, selection_value = self._resolve_group(group, query_plan, user_message, session_selections)
            if selection:
                resolved.append({"group_id": group["id"], "option_value": selection, "selection_value": selection_value, "source": source})
                audit.append({"group_id": group["id"], "option_value": selection, "selection_value": selection_value, "source": source})
            else:
                unresolved.append(group)
                audit.append({"group_id": group["id"], "source": "needs_clarification"})

        legacy_plan = {**query_plan, "metrics": legacy_metrics}
        legacy_missing = self.find_missing_required_dimensions(legacy_plan, ontology_engine)
        return {
            "resolved_selections": resolved,
            "unresolved_groups": unresolved,
            "legacy_missing_dimensions": legacy_missing,
            "audit": audit,
        }

    def precheck_metric_candidates(
        self,
        metric_refs: list[str],
        query_scope: dict,
        user_message: str,
        ontology_engine,
        session_selections: dict[str, dict] | None = None,
    ) -> dict:
        """Conservatively identify an early clarification safe before detail planning.

        Blocking is allowed only when every resolved Metric candidate has the
        same non-empty required Group set and that Group maps into the verified
        schema scope. The query-plan check remains the authoritative gate.
        """
        metrics = ontology_engine.list_metrics()
        groups_by_id = {
            str(group.get("id")): group
            for group in ontology_engine.schema.get("dimension_groups", [])
            if group.get("status") == "approved"
        }
        resolved_metrics = []
        for metric_ref in dict.fromkeys(str(item).strip() for item in metric_refs if str(item).strip()):
            metric, _ = resolve_metric_reference(metric_ref, metrics)
            if metric:
                resolved_metrics.append(metric)
        if not resolved_metrics:
            return {"eligible": False, "reason": "no_resolved_metric_candidates"}

        group_sets = []
        for metric in resolved_metrics:
            group_sets.append(tuple(sorted(
                str(group_id)
                for group_id in metric.get("dimension_group_ids") or []
                if str(group_id) in groups_by_id and groups_by_id[str(group_id)].get("is_required")
            )))
        if not group_sets[0] or any(group_ids != group_sets[0] for group_ids in group_sets[1:]):
            return {"eligible": False, "reason": "candidate_groups_not_stable"}

        scope_classes = {
            str(query_scope.get("target_class") or ""),
            *(str(class_id) for class_id in query_scope.get("join_classes", []) or []),
        }
        required_groups = []
        for group_id in group_sets[0]:
            group = groups_by_id[group_id]
            approved_options = {
                str(option.get("value") or "")
                for option in group.get("options", [])
                if option.get("status", "approved") == "approved" and str(option.get("value") or "")
            }
            mapped_options = {
                str(mapping.get("option_value") or "")
                for mapping in group.get("field_mappings", [])
                if str(mapping.get("class_id") or "") in scope_classes
                and str(mapping.get("field_name") or "")
            }
            if not approved_options or not approved_options.issubset(mapped_options):
                return {"eligible": False, "reason": "group_mapping_outside_scope", "group_id": group_id}
            required_groups.append({
                **group,
                "metric_ids": [str(metric.get("id") or "") for metric in resolved_metrics],
                "metric_names": [str(metric.get("name") or metric.get("id") or "") for metric in resolved_metrics],
                "output_names": [],
            })

        preliminary_plan = {"metrics": [str(metric.get("id") or "") for metric in resolved_metrics], "filters": [], "dimensions": []}
        unresolved = []
        resolved = []
        audit = []
        for group in required_groups:
            selection, source, selection_value = self._resolve_group(
                group, preliminary_plan, user_message, session_selections or {}
            )
            if selection:
                resolved.append({"group_id": group["id"], "option_value": selection, "selection_value": selection_value, "source": source})
                audit.append({"group_id": group["id"], "option_value": selection, "selection_value": selection_value, "source": source})
            else:
                unresolved.append(group)
                audit.append({"group_id": group["id"], "source": "needs_clarification"})
        return {
            "eligible": True,
            "reason": "stable_candidate_groups",
            "candidate_metric_ids": preliminary_plan["metrics"],
            "groups": required_groups,
            "resolved_selections": resolved,
            "unresolved_groups": unresolved,
            "audit": audit,
        }

    @staticmethod
    def _planned_fields(query_plan: dict) -> set[str]:
        fields = {str(item).strip() for item in query_plan.get("dimensions", []) if isinstance(item, str)}
        for item in query_plan.get("filters", []) or []:
            if isinstance(item, dict):
                fields.add(str(item.get("field") or item.get("name") or "").strip())
        return fields

    def _resolve_group(self, group: dict, query_plan: dict, user_message: str, selections: dict[str, dict]) -> tuple[str, str, str]:
        planned_fields = self._planned_fields(query_plan)
        for item in query_plan.get("filters", []) or []:
            if not isinstance(item, dict):
                continue
            for mapping in group.get("field_mappings", []):
                if str(mapping.get("field_name") or "") == str(item.get("field") or ""):
                    return str(mapping.get("option_value")), "query_plan", str(item.get("value") or "")

        stored = selections.get(str(group.get("id"))) or {}
        stored_value = str(stored.get("option_value") or stored.get("value") or "")
        member_value = str(stored.get("selection_value") or "").strip()
        if self._approved_option(group, stored_value) and (
            group.get("group_type") != "time" or self._valid_time_value(stored_value, member_value)
        ):
            return stored_value, "user_answer", member_value

        explicit_time = self._explicit_time_selection(group, user_message)
        if explicit_time:
            return explicit_time[0], "message_explicit_time", explicit_time[1]

        text = str(user_message or "").casefold()
        matches = []
        for option in group.get("options", []):
            if option.get("status", "approved") != "approved":
                continue
            terms = [option.get("value"), option.get("label"), *(option.get("aliases") or [])]
            if any(str(term).strip() and str(term).casefold() in text for term in terms):
                matches.append(str(option.get("value")))
        if len(set(matches)) == 1:
            if group.get("group_type") != "time":
                return matches[0], "message_alias", ""

        policy = str(group.get("clarification_policy") or "ask_when_ambiguous")
        default = str(group.get("default_option") or "")
        if group.get("group_type") != "time" and policy == "auto_fill" and self._approved_option(group, default):
            return default, "group_default", ""
        return "", "", ""

    def _explicit_time_selection(self, group: dict, user_message: str) -> tuple[str, str] | None:
        """Return one concrete period explicitly written by the user, if unambiguous."""
        if group.get("group_type") != "time":
            return None

        patterns = {
            "month": r"(?<!\d)(\d{4}AP(?:0[1-9]|1[0-2]))(?!\d)",
            "quarter": r"(?<!\d)(\d{4}Q[1-4])(?!\d)",
            # Do not treat the year prefix of an AP month or quarter as a year selection.
            "year": r"(?<!\d)(\d{4})(?!\d|\s*(?:AP|Q))",
        }
        text = str(user_message or "")
        matches = {
            option_value: {
                match.upper()
                for match in re.findall(patterns[option_value], text, flags=re.IGNORECASE)
            }
            for option_value in patterns
            if self._approved_option(group, option_value)
        }
        candidates = [
            (option_value, value)
            for option_value, values in matches.items()
            for value in values
        ]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _valid_time_value(option_value: str, value: str) -> bool:
        patterns = {
            "month": r"^\d{4}AP(?:0[1-9]|1[0-2])$",
            "quarter": r"^\d{4}Q[1-4]$",
            "year": r"^\d{4}$",
        }
        return bool(re.fullmatch(patterns.get(option_value, r"$^"), value.upper()))

    @staticmethod
    def _approved_option(group: dict, value: str) -> bool:
        return bool(value) and any(
            str(option.get("value")) == value and option.get("status", "approved") == "approved"
            for option in group.get("options", [])
        )

    @staticmethod
    def apply_resolved_selections(query_plan: dict, resolution: dict) -> dict:
        """Append governed physical dimensions; never accept client physical fields."""
        plan = {**query_plan, "dimensions": list(query_plan.get("dimensions", []) or [])}
        selections = []
        groups = {str(group.get("id")): group for group in resolution.get("groups", [])}
        for selected in resolution.get("resolved_selections", []):
            group = groups.get(str(selected.get("group_id")))
            if not group:
                continue
            mappings = [mapping for mapping in group.get("field_mappings", []) if mapping.get("option_value") == selected.get("option_value")]
            if not mappings:
                continue
            mapping = mappings[0]
            field = str(mapping.get("field_name") or "")
            selection_value = str(selected.get("selection_value") or "").strip()
            if field and not selection_value and field not in plan["dimensions"]:
                plan["dimensions"].append(field)
            if selection_value:
                filters = list(plan.get("filters", []) or [])
                if not any(str(item.get("field") or "") == field for item in filters if isinstance(item, dict)):
                    filters.append({"field": field, "operator": "=", "value": selection_value})
                plan["filters"] = filters
            selections.append({**selected, "field": field, "class_id": mapping.get("class_id")})
        if selections:
            plan["dimension_selections"] = selections
        return plan

    @staticmethod
    def build_dimension_group_question(unresolved_groups: list[dict], stage: str = "final") -> dict:
        questions = []
        for group in unresolved_groups:
            options = [
                {"id": option.get("value"), "value": option.get("value"), "label": option.get("label"), "is_default": bool(option.get("is_default"))}
                for option in group.get("options", []) if option.get("status", "approved") == "approved"
            ]
            requires_value = group.get("group_type") == "time"
            questions.append({"group_id": group.get("id"), "group_name": group.get("name"), "group_type": group.get("group_type"), "metric_ids": group.get("metric_ids", []), "required": True, "requires_value": requires_value, "value_label": "统计期间" if requires_value else "", "options": options})
        first = questions[0] if questions else {}
        question = f"请先选择“{first.get('group_name', '必要维度')}”的统计周期，再填写具体期间。" if first.get("requires_value") else f"为保证统计口径一致，请选择“{first.get('group_name', '必要维度')}”。"
        return {"version": 2, "reason": "missing_dimension_groups", "stage": stage, "question": question, "questions": questions, "field": first.get("group_id", ""), "multi_select": len(questions) > 1, "options": first.get("options", [])}

    @staticmethod
    def find_missing_required_dimensions(query_plan: dict, ontology_engine) -> list[dict]:
        # Required input is governed by DimensionGroup.is_required.
        return []

    @staticmethod
    def build_required_dimension_question(missing_dimensions: list[dict]) -> dict:
        if not missing_dimensions: return {}
        field = str(missing_dimensions[0].get("field") or "必要维度")
        return {"question": f"查询前，请确认“{field}”。", "field": field, "multi_select": False, "options": ClarifyAgent._suggested_options(field)}

    @staticmethod
    def _suggested_options(field: str) -> list[dict]:
        values = [("current", "本期"), ("previous", "上期"), ("custom", "指定时间范围")] if any(token in field.casefold() for token in ("日期", "时间", "期间", "month", "date")) else [("all", "全部"), ("custom", "指定值")]
        return [{"id": key, "label": label, "value": label} for key, label in values]
