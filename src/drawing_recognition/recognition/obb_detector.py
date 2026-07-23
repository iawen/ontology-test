"""Optional OBB detection adapter.

The model is deliberately opt-in: no model weights or GPU dependency are bundled
with the service. Set ``DRAWING_OBB_MODEL`` to a compatible Ultralytics model to
activate it after P0 evaluation freezes a model version.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ObbDetection:
    label: str
    confidence: float
    center_x: float
    center_y: float
    width: float
    height: float
    angle_deg: float


class ObbDetector:
    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or os.getenv("DRAWING_OBB_MODEL")
        self._model = None

    @property
    def enabled(self) -> bool:
        return bool(self.model_path and Path(self.model_path).is_file())

    def detect(self, image_path: Path) -> list[ObbDetection]:
        if not self.enabled:
            return []
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("已配置 DRAWING_OBB_MODEL，但未安装 ultralytics。") from exc
        if self._model is None:
            self._model = YOLO(self.model_path)
        results = self._model(str(image_path), verbose=False)
        detections: list[ObbDetection] = []
        for result in results:
            if result.obb is None:
                continue
            for row in result.obb.data.tolist():
                x, y, width, height, angle, confidence, class_id = row
                detections.append(ObbDetection(
                    label=result.names[int(class_id)], confidence=float(confidence), center_x=float(x),
                    center_y=float(y), width=float(width), height=float(height), angle_deg=float(angle),
                ))
        return detections