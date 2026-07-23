from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from drawing_recognition.domain.errors import DrawingAnalysisError


def _find_converter() -> str | None:
    configured = os.getenv("ODA_FILE_CONVERTER") or os.getenv("DWG_CONVERTER")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")


def convert_dwg_to_dxf(dwg_path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    """Convert a DWG using an ODA adapter and retain the temporary output."""
    converter = _find_converter()
    if not converter:
        raise DrawingAnalysisError(
            "无法解析 DWG：未配置 ODA File Converter。请设置 ODA_FILE_CONVERTER 为 "
            "ODAFileConverter.exe 的绝对路径，或先转换为 DXF 后上传。"
        )

    temp_dir = tempfile.TemporaryDirectory(prefix="drawing-recognition-")
    root = Path(temp_dir.name)
    input_dir, output_dir = root / "input", root / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    shutil.copy2(dwg_path, input_dir / dwg_path.name)
    command = [converter, str(input_dir), str(output_dir), "ACAD2018", "DXF", "0", "1"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        temp_dir.cleanup()
        raise DrawingAnalysisError("DWG 转换超时（120 秒）") from exc
    if completed.returncode != 0:
        temp_dir.cleanup()
        message = (completed.stderr or completed.stdout or "未知转换错误").strip()
        raise DrawingAnalysisError(f"DWG 转换失败：{message[:500]}")

    candidates = list(output_dir.rglob("*.dxf")) + list(output_dir.rglob("*.DXF"))
    if not candidates:
        temp_dir.cleanup()
        raise DrawingAnalysisError("DWG 转换未生成 DXF 文件，请检查转换器版本和输入图纸。")
    return candidates[0], temp_dir