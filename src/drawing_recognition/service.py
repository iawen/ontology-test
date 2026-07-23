"""Application service orchestrating the P1 vector-first analysis workflow."""

from __future__ import annotations

import tempfile
from pathlib import Path

from drawing_recognition.cad.dxf_parser import parse_dxf
from drawing_recognition.domain.models import DrawingAnalysisResult
from drawing_recognition.fusion.result_assembler import assemble_vector_result
from drawing_recognition.fusion.text_association import associate_native_text
from drawing_recognition.ingest.dwg_converter import convert_dwg_to_dxf
from drawing_recognition.ingest.file_validation import validate_drawing_file
from drawing_recognition.recognition.vision_pipeline import detect_visual_components


def analyze_drawing(path: Path, *, max_components: int = 1000) -> DrawingAnalysisResult:
    """Validate input, adapt DWG if needed, parse DXF, then assemble evidence."""
    suffix = validate_drawing_file(path)
    temporary_output: tempfile.TemporaryDirectory[str] | None = None
    try:
        dxf_path = path
        if suffix == ".dwg":
            dxf_path, temporary_output = convert_dwg_to_dxf(path)
        parsed = parse_dxf(dxf_path, max_components=max_components)
        parsed.components = associate_native_text(parsed.components, parsed.texts)
        visual_components = detect_visual_components(dxf_path)
        if visual_components:
            parsed.components.extend(visual_components[:max(0, max_components - len(parsed.components))])
        return assemble_vector_result(path, suffix, temporary_output is not None, parsed)
    finally:
        if temporary_output is not None:
            temporary_output.cleanup()