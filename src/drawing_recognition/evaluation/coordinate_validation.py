"""P0 CAD/pixel coordinate-transform round-trip validation."""

from __future__ import annotations

from drawing_recognition.cad.coordinates import CoordinateTransform
from drawing_recognition.domain.models import CadPoint


def validate_coordinate_round_trip(
    min_point: CadPoint,
    max_point: CadPoint,
    width_px: int,
    height_px: int,
    anchors: list[CadPoint],
    *,
    tolerance: float = 1e-6,
) -> dict:
    """Validate anchors through CAD→pixel→CAD transformation for audit evidence."""
    transform = CoordinateTransform(min_point, max_point, width_px, height_px)
    errors: list[float] = []
    for anchor in anchors:
        recovered = transform.pixel_to_cad(transform.cad_to_pixel(anchor))
        errors.append(max(abs(anchor.x - recovered.x), abs(anchor.y - recovered.y)))
    return {
        "anchor_count": len(anchors),
        "max_abs_error": max(errors, default=0.0),
        "tolerance": tolerance,
        "passed": all(error <= tolerance for error in errors),
    }