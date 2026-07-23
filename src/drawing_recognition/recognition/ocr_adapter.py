"""OCR extension point; P1 returns native CAD text as the authoritative source."""

from __future__ import annotations

from drawing_recognition.domain.models import NativeText


class NativeTextOcrAdapter:
    name = "native-dxf-text"

    def extract(self, native_texts: list[NativeText]) -> list[NativeText]:
        """Return CAD text unchanged; PaddleOCR can replace this adapter in production."""
        return native_texts