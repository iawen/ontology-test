"""Pydantic models for the vector-first P1 recognition result."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CadPoint(BaseModel):
    x: float
    y: float


class NativeText(BaseModel):
    id: str
    content: str
    entity_type: Literal["TEXT", "MTEXT"]
    layer: str
    cad_position: CadPoint | None = None


class ComponentEvidence(BaseModel):
    block_name: str
    layer: str
    attributes: dict[str, str] = Field(default_factory=dict)
    text_ids: list[str] = Field(default_factory=list)
    detection_model: str | None = None


class ComponentCandidate(BaseModel):
    id: str
    type: str
    reference: str | None = None
    value: str | None = None
    cad_center: CadPoint
    rotation_deg: float
    source: Literal["block", "vision", "fusion"] = "block"
    confidence: float
    review_status: Literal["approved", "pending", "rejected"] = "approved"
    evidence: ComponentEvidence


class ParsedDxfDrawing(BaseModel):
    dxf_version: str
    units: int
    entity_types: dict[str, int]
    layers: dict[str, int]
    components: list[ComponentCandidate]
    texts: list[NativeText]
    unknown_blocks: dict[str, int]


class DrawingAnalysisResult(BaseModel):
    drawing: dict[str, Any]
    summary: dict[str, int]
    components: list[ComponentCandidate]
    texts: list[NativeText]
    audit: dict[str, Any]


class AuditReport(BaseModel):
    generated_at: str
    drawing_count: int
    successful_count: int
    failed_count: int
    block_component_count: int
    unknown_block_count: int
    text_count: int
    layer_usage: dict[str, int]
    component_types: dict[str, int]
    failures: list[dict[str, str]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class RecognitionRun(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    phase: str
    progress: int = Field(ge=0, le=100)
    message: str
    filename: str
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None