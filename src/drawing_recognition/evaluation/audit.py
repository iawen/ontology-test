"""P0 batch audit report generation for representative DXF/DWG samples."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from drawing_recognition.domain.models import AuditReport
from drawing_recognition.service import analyze_drawing


def audit_drawings(paths: list[Path]) -> AuditReport:
    layers: Counter[str] = Counter()
    component_types: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    successful = block_components = unknown_blocks = text_count = 0
    for path in paths:
        try:
            result = analyze_drawing(path)
        except Exception as exc:
            failures.append({"filename": path.name, "error": str(exc)})
            continue
        successful += 1
        block_components += result.summary["known_block_component_count"]
        unknown_blocks += result.summary["unknown_block_count"]
        text_count += result.summary["text_count"]
        layers.update(result.audit["layers"])
        component_types.update(component.type for component in result.components)
    recommendations = [
        "先确认未知 Block 的业务含义，并维护版本化 Block 别名词典。" if unknown_blocks else "已知 Block 词典覆盖当前可解析样本。",
        "DWG 转换失败样本需检查 ODA File Converter 配置、版本兼容性与许可。" if failures else "当前样本均可完成解析。",
        "在 P2 前按图纸来源和类型分层补充样本，避免切片级数据泄漏。",
    ]
    return AuditReport(
        generated_at=datetime.now(UTC).isoformat(), drawing_count=len(paths), successful_count=successful,
        failed_count=len(failures), block_component_count=block_components, unknown_block_count=unknown_blocks,
        text_count=text_count, layer_usage=dict(layers.most_common()), component_types=dict(component_types.most_common()),
        failures=failures, recommendations=recommendations,
    )