"""Extension contract for agent-specific employee filter handling."""

from __future__ import annotations


class NullEmployeePlugin:
    """Inert fallback used when an agent has no employee policy plugin."""

    def merge_locked_filters(self, filters: list, locked_filters: list[dict]) -> list:
        return list(filters)

    def is_employee_filter(self, item: dict) -> bool:
        return False

    async def align_filter(
        self, item: dict, selected_columns: list[dict], query_engine
    ) -> dict:
        return item