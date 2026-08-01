"""Agent-specific ChatBI extensions."""

from __future__ import annotations

import importlib
import re

from tools.logger import logger

from .employee import NullEmployeePlugin


def load_employee_plugin(agent_id: str):
    """Load ``{agent_id}_employee`` or return an inert employee plugin."""
    normalized_agent_id = str(agent_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", normalized_agent_id):
        return NullEmployeePlugin()

    module_name = f"{__name__}.{normalized_agent_id}_employee"
    try:
        module = importlib.import_module(module_name)
        plugin_class = getattr(module, "EmployeePlugin")
        return plugin_class()
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            logger.warning(
                "Employee plugin dependency is unavailable: agent_id=%s error=%s",
                agent_id,
                str(exc),
            )
        return NullEmployeePlugin()
    except (AttributeError, TypeError) as exc:
        logger.warning(
            "Employee plugin is invalid; using empty plugin: agent_id=%s error=%s",
            agent_id,
            str(exc),
        )
        return NullEmployeePlugin()


__all__ = ["NullEmployeePlugin", "load_employee_plugin"]