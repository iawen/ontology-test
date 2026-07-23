"""FastAPI routes for the vector-first electrical drawing recognition baseline."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from drawing_recognition.domain.errors import DrawingAnalysisError
from drawing_recognition.domain.models import CadPoint
from drawing_recognition.evaluation.audit import audit_drawings
from drawing_recognition.evaluation.coordinate_validation import validate_coordinate_round_trip
from drawing_recognition.ingest.file_validation import SUPPORTED_EXTENSIONS
from drawing_recognition.runtime.repository import UPLOAD_ROOT, create_run, get_run, list_events
from drawing_recognition.runtime.worker import submit_analysis
from drawing_recognition.service import analyze_drawing


router = APIRouter()
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
SAMPLE_DRAWING = Path(__file__).resolve().parent / "data" / "B电气图.dwg"


def _raise_analysis_error(exc: DrawingAnalysisError) -> None:
    message = str(exc)
    status_code = 503 if "ODA File Converter" in message else 422
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/api/drawing-recognition/capabilities")
async def get_drawing_recognition_capabilities():
    """Describe the current P1 baseline so consumers do not infer unsupported features."""
    return {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "implemented": ["p0_batch_audit", "dxf_audit", "block_component_recognition", "native_text_extraction", "text_component_linking", "persistent_runs", "sse_progress"],
        "optional": ["png_rendering", "overlapping_tiling", "obb_detection_when_model_configured"],
        "not_implemented": ["paddleocr", "wire_tracing", "netlist", "human_review_workspace"],
        "sample_available": SAMPLE_DRAWING.is_file(),
    }


@router.post("/api/drawing-recognition/analyze")
async def analyze_uploaded_drawing(file: UploadFile = File(...)):
    """Analyze an uploaded DXF or DWG without retaining the input after completion."""
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "仅支持上传 .dwg 或 .dxf 文件。")

    with tempfile.TemporaryDirectory(prefix="drawing-upload-") as temp_dir:
        target = Path(temp_dir) / f"upload{suffix}"
        size = 0
        try:
            with target.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "图纸超过 100 MB 上传限制。")
                    destination.write(chunk)
        finally:
            await file.close()

        try:
            result = analyze_drawing(target).model_dump()
        except DrawingAnalysisError as exc:
            _raise_analysis_error(exc)
        result["drawing"]["filename"] = original_name
        result["drawing"]["size_bytes"] = size
        return result


@router.post("/api/drawing-recognition/runs")
async def create_recognition_run(file: UploadFile = File(...)):
    """Persist an upload and submit it to the local P1 worker queue."""
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "仅支持上传 .dwg 或 .dxf 文件。")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    stored_path = UPLOAD_ROOT / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with stored_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    stored_path.unlink(missing_ok=True)
                    raise HTTPException(413, "图纸超过 100 MB 上传限制。")
                destination.write(chunk)
    finally:
        await file.close()
    run = create_run(original_name, stored_path)
    submit_analysis(run["id"], stored_path)
    return {"run_id": run["id"], "status": run["status"], "size_bytes": size}


@router.get("/api/drawing-recognition/runs/{run_id}")
async def get_recognition_run(run_id: str):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, "识别任务不存在。")
    return run


@router.get("/api/drawing-recognition/runs/{run_id}/events")
async def get_recognition_events(run_id: str):
    if get_run(run_id) is None:
        raise HTTPException(404, "识别任务不存在。")
    return {"run_id": run_id, "events": list_events(run_id)}


@router.get("/api/drawing-recognition/runs/{run_id}/stream")
async def stream_recognition_run(run_id: str):
    if get_run(run_id) is None:
        raise HTTPException(404, "识别任务不存在。")

    async def generate():
        last_event_count = -1
        while True:
            run = get_run(run_id)
            events = list_events(run_id)
            if len(events) != last_event_count:
                yield f"data: {json.dumps({'run': run, 'events': events}, ensure_ascii=False)}\n\n"
                last_event_count = len(events)
            if run and run["status"] in {"succeeded", "failed"}:
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/api/drawing-recognition/audit-sample")
async def audit_repository_sample():
    """Run the P0 audit report against the checked-in validation drawing."""
    return audit_drawings([SAMPLE_DRAWING]).model_dump()


@router.get("/api/drawing-recognition/validation/coordinates")
async def validate_coordinates():
    """Expose a deterministic P0 CAD/pixel round-trip validation report."""
    return validate_coordinate_round_trip(
        CadPoint(x=0, y=0), CadPoint(x=100, y=50), 2000, 1000,
        [CadPoint(x=0, y=0), CadPoint(x=100, y=50), CadPoint(x=50, y=25), CadPoint(x=20, y=40)],
    )


@router.post("/api/drawing-recognition/analyze-sample")
async def analyze_repository_sample():
    """Analyze the checked-in B电气图.dwg sample for environment validation."""
    try:
        return analyze_drawing(SAMPLE_DRAWING).model_dump()
    except DrawingAnalysisError as exc:
        _raise_analysis_error(exc)