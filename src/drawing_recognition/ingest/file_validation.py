from __future__ import annotations

import hashlib
from pathlib import Path

from drawing_recognition.domain.errors import DrawingAnalysisError


SUPPORTED_EXTENSIONS = {".dwg", ".dxf"}


def validate_drawing_file(path: Path) -> str:
    if not path.is_file():
        raise DrawingAnalysisError("图纸文件不存在。")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DrawingAnalysisError("仅支持 .dwg 和 .dxf 文件。")
    return suffix


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()