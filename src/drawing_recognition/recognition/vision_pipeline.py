"""P2 optional rendered-drawing OBB detection pipeline.

This pipeline runs only when ``DRAWING_OBB_MODEL`` points to a validated model.
It deliberately remains separate from the deterministic Block path so deployments
without model weights retain the P1 behavior.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from drawing_recognition.cad.coordinates import CoordinateTransform
from drawing_recognition.domain.models import CadPoint, ComponentCandidate, ComponentEvidence
from drawing_recognition.recognition.obb_detector import ObbDetector
from drawing_recognition.rendering.dxf_renderer import render_dxf_to_png
from drawing_recognition.rendering.tiling import create_tiles


def _drawing_transform(dxf_path: Path, width_px: int, height_px: int) -> CoordinateTransform:
    import ezdxf
    from ezdxf import bbox

    document = ezdxf.readfile(dxf_path)
    extent = bbox.extents(document.modelspace())
    return CoordinateTransform(
        CadPoint(x=float(extent.extmin.x), y=float(extent.extmin.y)),
        CadPoint(x=float(extent.extmax.x), y=float(extent.extmax.y)), width_px, height_px,
    )


def _is_duplicate(candidate: ComponentCandidate, prior: list[ComponentCandidate], threshold: float = 2.0) -> bool:
    return any(
        item.type == candidate.type
        and abs(item.cad_center.x - candidate.cad_center.x) <= threshold
        and abs(item.cad_center.y - candidate.cad_center.y) <= threshold
        for item in prior
    )


def detect_visual_components(dxf_path: Path, *, detector: ObbDetector | None = None) -> list[ComponentCandidate]:
    """Render, tile, infer, map detections to CAD coordinates, and de-duplicate."""
    detector = detector or ObbDetector()
    if not detector.enabled:
        return []
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法运行视觉检测。") from exc
    with tempfile.TemporaryDirectory(prefix="drawing-vision-") as temp_dir:
        root = Path(temp_dir)
        rendered = render_dxf_to_png(dxf_path, root / "drawing.png")
        with Image.open(rendered) as image:
            transform = _drawing_transform(dxf_path, image.width, image.height)
        candidates: list[ComponentCandidate] = []
        for tile in create_tiles(rendered, root / "tiles"):
            for detection in detector.detect(tile.path):
                global_center = CadPoint(x=tile.x_offset + detection.center_x, y=tile.y_offset + detection.center_y)
                candidate = ComponentCandidate(
                    id=f"vision_{len(candidates) + 1:04d}", type=detection.label,
                    cad_center=transform.pixel_to_cad(global_center), rotation_deg=detection.angle_deg,
                    source="vision", confidence=detection.confidence, review_status="pending",
                    evidence=ComponentEvidence(block_name="", layer="", detection_model=str(detector.model_path)),
                )
                if not _is_duplicate(candidate, candidates):
                    candidates.append(candidate)
        return candidates