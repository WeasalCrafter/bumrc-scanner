"""Keep a copy of each printed label as a PNG, in case the printer jams.
Only the newest KEEP labels are kept."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image

from scanner import config

KEEP = 5


def save_label(img: Image.Image, item_id: int, out_dir: Path | None = None, keep: int = KEEP) -> Path:
    out_dir = out_dir or config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = out_dir / f"label_{int(item_id):08d}_{stamp}.png"
    img.save(path)
    labels = sorted(out_dir.glob("label_*.png"), key=lambda p: p.stat().st_mtime_ns)
    for old in labels[:-keep]:
        old.unlink(missing_ok=True)
    return path
