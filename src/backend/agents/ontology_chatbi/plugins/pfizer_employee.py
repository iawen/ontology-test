"""Pfizer employee hierarchy policy for governed ChatBI queries."""

from __future__ import annotations


class EmployeePlugin:
    """Protect Pfizer employee hierarchy filters during subquestion reuse."""

    EMPLOYEE_FIELD_MARKERS = ("employee", "员工", "人员", "代表", "负责人", "经理")

    @classmethod
    def is_employee_filter(cls, item: dict) -> bool:
        field = str(item.get("field") or "").lower()
        return any(marker in field for marker in cls.EMPLOYEE_FIELD_MARKERS)

    @staticmethod
    def _value_key(value):
        if isinstance(value, list):
            return tuple(EmployeePlugin._value_key(item) for item in value)
        return str(value).strip() if value is not None else None

    def merge_locked_filters(self, filters: list, locked_filters: list[dict]) -> list:
        """Retain locked employee hierarchy filters over conflicting child filters."""
        if not locked_filters:
            return list(filters)

        merged = []
        for item in filters:
            if not isinstance(item, dict):
                merged.append(item)
                continue
            operator = str(item.get("operator") or "").upper()
            value = self._value_key(item.get("value"))
            conflicts_with_employee_anchor = any(
                self.is_employee_filter(locked)
                and operator == str(locked.get("operator") or "").upper()
                and value == self._value_key(locked.get("value"))
                and str(item.get("field") or "") != str(locked.get("field") or "")
                for locked in locked_filters
            )
            if not conflicts_with_employee_anchor:
                merged.append(item)
        return merged

    async def align_filter(
        self, item: dict, selected_columns: list[dict], query_engine
    ) -> dict:
        """Use exact employee-name matches only; never fuzzy-rewrite a hierarchy filter."""
        value = item.get("value")
        if not isinstance(value, str) or not value.strip():
            return item

        for column in selected_columns:
            class_id = str(column.get("class_id") or "")
            field = str(column.get("field") or "")
            if not class_id or not field:
                continue
            try:
                result = query_engine.fuzzy_search_values(class_id, field, value, limit=30)
                values = result.get("matched_values") or result.get("values", [])
                if value in {str(candidate) for candidate in values}:
                    return {**item, "field": field, "value": value, "_class_id": class_id}
            except Exception:
                continue
        return item