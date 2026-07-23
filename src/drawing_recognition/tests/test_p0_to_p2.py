"""Regression tests for implemented P0-P2 capabilities."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from drawing_recognition.api import router
from drawing_recognition.domain.models import CadPoint
from drawing_recognition.evaluation.audit import audit_drawings
from drawing_recognition.evaluation.coordinate_validation import validate_coordinate_round_trip
from drawing_recognition.rendering.dxf_renderer import render_dxf_to_png
from drawing_recognition.rendering.tiling import create_tiles
from drawing_recognition.service import analyze_drawing


class P0ToP2RegressionTests(unittest.TestCase):
    def _create_dxf(self, root: Path) -> Path:
        path = root / "electrical.dxf"
        document = ezdxf.new("R2018")
        document.blocks.new("RESISTOR")
        insert = document.modelspace().add_blockref("RESISTOR", (10, 20))
        insert.add_attrib("TAG", "R1")
        document.modelspace().add_text("R1 10k", dxfattribs={"insert": (12, 22)})
        document.saveas(path)
        return path

    def test_p0_audit_and_coordinate_round_trip(self):
        coordinate_report = validate_coordinate_round_trip(
            CadPoint(x=0, y=0), CadPoint(x=100, y=50), 2000, 1000,
            [CadPoint(x=0, y=0), CadPoint(x=100, y=50), CadPoint(x=50, y=25), CadPoint(x=20, y=40)],
        )
        self.assertTrue(coordinate_report["passed"])
        with tempfile.TemporaryDirectory() as temp_dir:
            report = audit_drawings([self._create_dxf(Path(temp_dir))])
        self.assertEqual(report.successful_count, 1)
        self.assertEqual(report.block_component_count, 1)

    def test_p1_vector_recognition_and_api_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            drawing = self._create_dxf(Path(temp_dir))
            result = analyze_drawing(drawing)
            self.assertEqual(result.components[0].reference, "R1")
            self.assertEqual(result.components[0].value, "10k")

            app = FastAPI()
            app.include_router(router)
            response = TestClient(app).post(
                "/api/drawing-recognition/analyze",
                files={"file": (drawing.name, drawing.read_bytes(), "application/dxf")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["component_count"], 1)

    def test_p2_render_and_tile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            drawing = self._create_dxf(root)
            image = render_dxf_to_png(drawing, root / "drawing.png", dpi=72)
            tiles = create_tiles(image, root / "tiles", tile_size=256, overlap=32)
        self.assertTrue(tiles)


if __name__ == "__main__":
    unittest.main()
