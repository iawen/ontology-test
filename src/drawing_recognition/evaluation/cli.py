"""P0 command-line audit entry point: python -m drawing_recognition.evaluation.cli data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from drawing_recognition.evaluation.audit import audit_drawings
from drawing_recognition.ingest.file_validation import SUPPORTED_EXTENSIONS


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    paths = sorted(path for path in root.rglob("*") if path.suffix.lower() in SUPPORTED_EXTENSIONS)
    print(json.dumps(audit_drawings(paths).model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()