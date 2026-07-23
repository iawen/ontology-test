"""Deterministic DXF-to-PNG rendering for P0 validation and P2 OBB input."""

from __future__ import annotations

from pathlib import Path

from drawing_recognition.domain.errors import DrawingAnalysisError


def render_dxf_to_png(dxf_path: Path, output_path: Path, *, dpi: int = 300) -> Path:
    """Render modelspace to a stable PNG using ezdxf's matplotlib backend."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import matplotlib
    except ImportError as exc:
        raise DrawingAnalysisError("缺少 DXF PNG 渲染组件，无法执行渲染校验。") from exc
    try:
        document = ezdxf.readfile(dxf_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        matplotlib.qsave(document.modelspace(), str(output_path), dpi=dpi)
        return output_path
    except Exception as exc:
        raise DrawingAnalysisError(f"DXF PNG 渲染失败：{exc}") from exc