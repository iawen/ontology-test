"""Explicit pixel/CAD transforms used by rendering and future OBB detections."""

from __future__ import annotations

from drawing_recognition.domain.models import CadPoint


class CoordinateTransform:
    """Axis-aligned render transform with the CAD-to-image Y-axis inversion."""

    def __init__(self, min_point: CadPoint, max_point: CadPoint, width_px: int, height_px: int):
        if max_point.x <= min_point.x or max_point.y <= min_point.y:
            raise ValueError("CAD 范围必须具有正宽度和正高度。")
        self.min_point = min_point
        self.max_point = max_point
        self.width_px = width_px
        self.height_px = height_px

    def cad_to_pixel(self, point: CadPoint) -> CadPoint:
        return CadPoint(
            x=(point.x - self.min_point.x) * self.width_px / (self.max_point.x - self.min_point.x),
            y=(self.max_point.y - point.y) * self.height_px / (self.max_point.y - self.min_point.y),
        )

    def pixel_to_cad(self, point: CadPoint) -> CadPoint:
        return CadPoint(
            x=self.min_point.x + point.x * (self.max_point.x - self.min_point.x) / self.width_px,
            y=self.max_point.y - point.y * (self.max_point.y - self.min_point.y) / self.height_px,
        )