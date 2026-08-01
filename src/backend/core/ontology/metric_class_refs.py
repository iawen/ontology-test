"""Helpers for converting Metric definition Class references at persistence boundaries."""

from __future__ import annotations

import copy
from typing import Any


def _input_groups(definition: dict) -> list[list[dict]]:
    groups = [definition.get("inputs", [])]
    groups.extend(
        output.get("inputs", [])
        for output in definition.get("outputs", [])
        if isinstance(output, dict)
    )
    return [group for group in groups if isinstance(group, list)]


def replace_definition_class_refs(definition: dict, class_refs: dict[Any, Any]) -> dict:
    """Return a copy with anchor_class and inputs[].class_id mapped by ``class_refs``."""
    normalized = copy.deepcopy(definition if isinstance(definition, dict) else {})
    anchor = normalized.get("anchor_class")
    if anchor in class_refs:
        normalized["anchor_class"] = class_refs[anchor]
    for inputs in _input_groups(normalized):
        for item in inputs:
            if isinstance(item, dict) and item.get("class_id") in class_refs:
                item["class_id"] = class_refs[item["class_id"]]
    return normalized
