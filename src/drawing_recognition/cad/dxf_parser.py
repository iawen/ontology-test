"""Extract auditable native DXF entities and known Block components."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

try:
    import ezdxf
except ImportError:  # pragma: no cover
    ezdxf = None

from drawing_recognition.domain.errors import DrawingAnalysisError
from drawing_recognition.domain.models import CadPoint, ComponentCandidate, ComponentEvidence, NativeText, ParsedDxfDrawing
from drawing_recognition.recognition.block_classifier import classify_block


def _point(value: Any) -> CadPoint:
    return CadPoint(x=round(float(value.x), 6), y=round(float(value.y), 6))


def _entity_text(entity: Any) -> str:
    return entity.dxf.text if entity.dxftype() == "TEXT" else entity.plain_text()


def _attributes(insert: Any) -> dict[str, str]:
    return {attribute.dxf.tag: attribute.dxf.text for attribute in insert.attribs}


def parse_dxf(path: Path, *, max_components: int = 1000) -> ParsedDxfDrawing:
    if ezdxf is None:
        raise DrawingAnalysisError("服务未安装 ezdxf，无法解析 DXF。")
    try:
        document = ezdxf.readfile(path)
    except Exception as exc:
        raise DrawingAnalysisError(f"DXF 解析失败：{exc}") from exc

    entity_counts: Counter[str] = Counter()
    layers: Counter[str] = Counter()
    texts: list[NativeText] = []
    components: list[ComponentCandidate] = []
    unknown_blocks: Counter[str] = Counter()

    for index, entity in enumerate(document.modelspace()):
        entity_type = entity.dxftype()
        layer = entity.dxf.layer or "0"
        entity_counts[entity_type] += 1
        layers[layer] += 1
        if entity_type in {"TEXT", "MTEXT"}:
            content = _entity_text(entity).strip()
            insert = getattr(entity.dxf, "insert", None)
            if content:
                texts.append(NativeText(
                    id=f"text_{index}", content=content, entity_type=entity_type,
                    layer=layer, cad_position=_point(insert) if insert else None,
                ))
            continue
        if entity_type != "INSERT":
            continue

        block_name = entity.dxf.name
        component_type = classify_block(block_name)
        if component_type is None:
            unknown_blocks[block_name] += 1
        elif len(components) < max_components:
            components.append(ComponentCandidate(
                id=f"component_{len(components) + 1:04d}", type=component_type,
                cad_center=_point(entity.dxf.insert),
                rotation_deg=round(float(entity.dxf.get("rotation", 0.0)), 6),
                confidence=0.98,
                evidence=ComponentEvidence(block_name=block_name, layer=layer, attributes=_attributes(entity)),
            ))

    return ParsedDxfDrawing(
        dxf_version=document.dxfversion,
        units=int(document.header.get("$INSUNITS", 0)),
        entity_types=dict(entity_counts.most_common()),
        layers=dict(layers.most_common()),
        components=components,
        texts=texts,
        unknown_blocks=dict(unknown_blocks.most_common()),
    )