"""Overlapping image tiling with retained global pixel offsets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImageTile:
    path: Path
    x_offset: int
    y_offset: int
    width: int
    height: int


def create_tiles(image_path: Path, output_dir: Path, *, tile_size: int = 1536, overlap: int = 192) -> list[ImageTile]:
    """Create overlapping PNG tiles and preserve their full-image offsets."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法切分渲染图。") from exc
    if not 0 <= overlap < tile_size:
        raise ValueError("overlap 必须小于 tile_size。")
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        width, height = image.size
        stride = tile_size - overlap
        tiles: list[ImageTile] = []
        for y in range(0, height, stride):
            for x in range(0, width, stride):
                right, bottom = min(x + tile_size, width), min(y + tile_size, height)
                tile_path = output_dir / f"tile_{x}_{y}.png"
                image.crop((x, y, right, bottom)).save(tile_path)
                tiles.append(ImageTile(tile_path, x, y, right - x, bottom - y))
        return tiles