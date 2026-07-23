"""Deterministic native-text association for the P1 vector-first path."""

from __future__ import annotations

import math
import re

from drawing_recognition.domain.models import ComponentCandidate, NativeText


REFERENCE_PREFIXES = {
    "resistor": ("R",), "switch": ("S", "SA", "SB"), "fuse": ("F",),
    "relay": ("K",), "connector": ("X", "J"), "capacitor": ("C",), "diode": ("D",),
}
VALUE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[kKmMuUnNpP](?:\s*(?:Ω|ohm|v|a|w|f|h))?|Ω|ohm|v|a|w|f|h)",
    re.IGNORECASE,
)


def _distance(component: ComponentCandidate, text: NativeText) -> float:
    if text.cad_position is None:
        return float("inf")
    return math.hypot(component.cad_center.x - text.cad_position.x, component.cad_center.y - text.cad_position.y)


def _reference_pattern(component_type: str) -> re.Pattern[str] | None:
    prefixes = REFERENCE_PREFIXES.get(component_type)
    if not prefixes:
        return None
    return re.compile(r"\b(?:" + "|".join(map(re.escape, prefixes)) + r")\s*\d+[A-Za-z0-9-]*\b", re.IGNORECASE)


def associate_native_text(components: list[ComponentCandidate], texts: list[NativeText]) -> list[ComponentCandidate]:
    """Attach ATTRIB-first, then local CAD text reference/value evidence.

    Ambiguous associations remain pending instead of silently selecting a nearest
    label. The search distance scales with the nearest text spacing on a drawing.
    """
    for component in components:
        attributes = component.evidence.attributes
        component.reference = attributes.get("TAG") or attributes.get("REF") or attributes.get("REFERENCE")
        component.value = attributes.get("VALUE") or attributes.get("VAL")
        if component.reference and component.value:
            continue

        pattern = _reference_pattern(component.type)
        nearby = sorted(((item, _distance(component, item)) for item in texts), key=lambda item: item[1])[:4]
        references = [(item, distance) for item, distance in nearby if pattern and pattern.search(item.content)]
        values = [(item, distance) for item, distance in nearby if VALUE_PATTERN.search(item.content)]
        if references and not component.reference:
            best, best_distance = references[0]
            if len(references) == 1 or references[1][1] > best_distance * 1.25:
                component.reference = pattern.search(best.content).group(0).replace(" ", "")
                component.evidence.text_ids.append(best.id)
        if values and not component.value:
            best, best_distance = values[0]
            if len(values) == 1 or values[1][1] > best_distance * 1.25:
                component.value = VALUE_PATTERN.search(best.content).group(0)
                if best.id not in component.evidence.text_ids:
                    component.evidence.text_ids.append(best.id)
        if not component.reference and component.type in REFERENCE_PREFIXES:
            component.review_status = "pending"
            component.confidence = min(component.confidence, 0.75)
    return components