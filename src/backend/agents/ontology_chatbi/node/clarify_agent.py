"""Deterministic clarification policy for incomplete governed Metric queries."""

from collections import OrderedDict

from agents.ontology_chatbi.helper import resolve_metric_reference


class ClarifyAgent:
    """Find query requirements that must be resolved before data execution.

    The agent deliberately does not call an LLM: required dimensions are governed
    Metric metadata and should be enforced consistently for every query plan.
    """

    @staticmethod
    def find_missing_required_dimensions(query_plan: dict, ontology_engine) -> list[dict]:
        """Return required Metric dimensions absent from the planned projection."""
        planned_dimensions = {
            str(field).strip()
            for field in query_plan.get("dimensions", [])
            if isinstance(field, str) and field.strip()
        }
        missing: OrderedDict[str, dict] = OrderedDict()

        for metric_ref in query_plan.get("metrics", []):
            metric, output = resolve_metric_reference(
                str(metric_ref).strip(), ontology_engine.list_metrics()
            )
            if not metric:
                continue
            metric_id = str(metric.get("id") or metric_ref).strip()
            metric_name = str(metric.get("name") or metric_id).strip()
            output_name = str((output or {}).get("output_name") or "").strip()
            for field in metric.get("required_dimensions") or []:
                field = str(field).strip()
                if not field or field in planned_dimensions:
                    continue
                item = missing.setdefault(
                    field,
                    {"field": field, "metric_ids": [], "metric_names": [], "output_names": []},
                )
                if metric_id and metric_id not in item["metric_ids"]:
                    item["metric_ids"].append(metric_id)
                if metric_name and metric_name not in item["metric_names"]:
                    item["metric_names"].append(metric_name)
                if output_name and output_name not in item["output_names"]:
                    item["output_names"].append(output_name)
        return list(missing.values())

    @staticmethod
    def build_required_dimension_question(missing_dimensions: list[dict]) -> dict:
        """Build one focused, client-compatible question for the first missing field."""
        if not missing_dimensions:
            return {}
        missing = missing_dimensions[0]
        field = str(missing.get("field") or "必要维度").strip()
        metric_names = "、".join(missing.get("metric_names") or [])
        output_names = "、".join(missing.get("output_names") or [])
        metric_hint = (
            f"指标“{metric_names}”的结果“{output_names}”"
            if metric_names and output_names
            else f"指标“{metric_names}”" if metric_names else "该指标"
        )
        return {
            "question": f"查询{metric_hint}前，请确认“{field}”。",
            "field": field,
            "multi_select": False,
            "options": ClarifyAgent._suggested_options(field),
        }

    @staticmethod
    def _suggested_options(field: str) -> list[dict]:
        normalized = field.casefold()
        if any(token in normalized for token in ("日期", "时间", "期间", "月份", "季度", "年度", "year", "month", "date")):
            values = [("current", "本期"), ("previous", "上期"), ("custom", "指定时间范围")]
        else:
            values = [("all", "全部"), ("custom", "指定值")]
        return [{"id": key, "label": label, "value": label} for key, label in values]
