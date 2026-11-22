"""Bag labels on the Brother QL-820NWB: part name, a Code128 barcode of the
inventory ID ("BUMRC-000142"), the same text below it, and the MRC logo.

    img = create_label(item, item_id)    # pillow image, no printer needed
    print_label(img)                     # sends it to the label maker

Try it without the app (prints one label, or saves PNGs with --save):
    .venv/bin/python -m scanner.labels.printer          # print a sample label
    .venv/bin/python -m scanner.labels.printer --save   # out/label_<id>.png for every sample item

.env settings:
    LABEL_PRINTER   (optional) where the printer is, default file:///dev/usb/lp0

The printer device is root:lp, so your user must be in the `lp` group:
    sudo usermod -aG lp $USER        (then log out and back in)
"""
from __future__ import annotations

import os
import textwrap
from functools import lru_cache

import barcode
from brother_ql.backends.helpers import send
from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster
from PIL import Image, ImageDraw, ImageFont

from scanner.config import LOGO_FILE, load_env

# ---------------------------------------------------------------- the label printer

BACKEND = "linux_kernel"
MODEL = "QL-820NWB"
DEFAULT_PRINTER = "file:///dev/usb/lp0"   # override with LABEL_PRINTER in .env
LABEL_TYPE = "62red"
SUPPORTS_RED = True

# ---------------------------------------------------------------- label layout (pixels)

PAPER_WIDTH = 696
PAPER_HEIGHT = 320
DESCRIPTION_HEIGHT = 100   # two lines of name text at the top
BARCODE_HEIGHT = 120
EDGE_SPACE = 75            # left/right margin
FONT_SIZE = 38
LINE_SPACING = 40
TEXT_WIDTH = PAPER_WIDTH - 2 * EDGE_SPACE


@lru_cache(maxsize=1)
def _logo() -> Image.Image:
    """MRC logo, at most 180x120, in pure black/white (loaded once, on first use)."""
    logo = Image.open(LOGO_FILE).convert("RGBA")
    logo.thumbnail((180, 120))
    bg = Image.new("RGBA", logo.size, "white")
    logo = Image.alpha_composite(bg, logo)
    return logo.convert("L").point(lambda p: 255 if p > 128 else 0, "1")


def _name_lines(name: str, font, max_lines: int = 2) -> list[str]:
    """Split the name into lines that fit the label width, breaking between words.
    If it still doesn't fit, the last line ends in '…'."""
    words = name.split()
    lines: list[str] = []
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and font.getlength(line + " " + words[0]) <= TEXT_WIDTH:
            line += " " + words.pop(0)
        lines.append(line)
    if words or (lines and font.getlength(lines[-1]) > TEXT_WIDTH):   # leftovers, or one huge word
        last = lines[-1]
        while last and font.getlength(last + "…") > TEXT_WIDTH:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


ID_PREFIX = "BUMRC-"


def format_id(item_id: int) -> str:
    """Inventory ID as printed and encoded on labels: 142 -> 'BUMRC-000142'."""
    return f"{ID_PREFIX}{int(item_id):06d}"


def create_label(item: dict, item_id: int) -> Image.Image:
    """Label image for one inventory item. item_id is the number the inventory
    server gave it (printed and encoded in the barcode as 'BUMRC-000142')."""
    if item_id is None:
        raise ValueError("no inventory ID yet (the item has to be on the server first)")

    name = item.get("name") or item.get("mpn") or ""
    id_text = format_id(item_id)

    # Code128 as a string of '1'/'0' modules, stretched to fill the space between the margins
    encoded = barcode.get("code128", id_text).build()[0]
    bar_width = TEXT_WIDTH / len(encoded)

    # mode "1" = black/white, filled white
    img = Image.new("1", (PAPER_WIDTH, PAPER_HEIGHT), 1)
    draw = ImageDraw.Draw(img)

    # one black rectangle per '1' module
    for i, bit in enumerate(encoded):
        if bit == "1":
            left = EDGE_SPACE - 1 + (i * bar_width - 1)
            right = EDGE_SPACE - 1 + ((i + 1) * bar_width - 1)
            draw.rectangle(((left, DESCRIPTION_HEIGHT),
                            (right, DESCRIPTION_HEIGHT + BARCODE_HEIGHT - 1)), fill=0)

    font = ImageFont.load_default(size=FONT_SIZE)

    # part name, up to two lines
    for n, line in enumerate(_name_lines(name, font)):
        draw.text((EDGE_SPACE, n * LINE_SPACING), line, fill="black", font=font)

    # ID text bottom left, logo bottom right
    draw.text((EDGE_SPACE, BARCODE_HEIGHT + DESCRIPTION_HEIGHT + 20), id_text, fill="black", font=font)
    img.paste(_logo(), (PAPER_WIDTH - 260, BARCODE_HEIGHT + DESCRIPTION_HEIGHT + 5))
    return img


# ---------------------------------------------------------------- printing

def printer_url(env: dict | None = None) -> str:
    env = load_env() if env is None else env
    return env.get("LABEL_PRINTER") or DEFAULT_PRINTER


def printer_problem(env: dict | None = None) -> str | None:
    """Why printing would fail right now (unplugged / no permission), or None if it looks fine.
    Only checks local device files; a network printer (tcp://...) is assumed fine."""
    url = printer_url(env)
    if not url.startswith("file://"):
        return None
    path = url.removeprefix("file://")
    if not os.path.exists(path):
        return f"label printer not found at {path} (plugged in and switched on?)"
    if not os.access(path, os.W_OK):
        return f"no permission to use {path} (add yourself to the lp group, then log in again)"
    return None


def print_label(img: Image.Image, env: dict | None = None):
    """Send a label image to the printer. Raises RuntimeError with a readable
    message if the printer is missing or refuses it."""
    url = printer_url(env)
    problem = printer_problem(env)
    if problem:
        raise RuntimeError(problem)

    qlr = BrotherQLRaster(MODEL)
    qlr.exception_on_warning = True
    instructions = convert(
        qlr=qlr,
        images=[img],
        label=LABEL_TYPE,
        threshold=70.0,
        dither=False,
        compress=False,
        red=SUPPORTS_RED,
        dpi_600=False,
        hq=True,
        cut=True,
    )
    send(instructions=instructions, printer_identifier=url, backend_identifier=BACKEND, blocking=True)


# ---------------------------------------------------------------- try it

if __name__ == "__main__":
    import argparse
    import json

    from scanner.config import OUT_DIR, ROOT

    ap = argparse.ArgumentParser(description="Print a sample label, or save all sample labels as PNGs.")
    ap.add_argument("--save", action="store_true", help="save out/label_<id>.png instead of printing")
    args = ap.parse_args()

    samples = json.loads((ROOT / "tests" / "sample_items.json").read_text())
    if args.save:
        OUT_DIR.mkdir(exist_ok=True)
        for s in samples:
            path = OUT_DIR / f"label_{s['item_id']}.png"
            create_label(s["item"], s["item_id"]).save(path)
            print(path, "|", textwrap.shorten(s["item"]["name"], 60))
    else:
        s = samples[2]                                    # the 1UF capacitor
        print_label(create_label(s["item"], s["item_id"]))
        print("printed:", s["item"]["name"], "ID", s["item_id"])
