"""Read and change V4L2 camera controls (focus, sharpness, ...) with v4l2-ctl.

OpenCV can set a few of these too, but v4l2-ctl also tells us each control's
range and works the same on every kernel. Control names differ between kernel
versions (e.g. focus_auto vs focus_automatic_continuous), so callers should
look names up through ALIASES.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass

# our name -> names different kernels use for it
ALIASES = {
    "focus": ["focus_absolute"],
    "autofocus": ["focus_automatic_continuous", "focus_auto"],
    "sharpness": ["sharpness"],
    "brightness": ["brightness"],
    "contrast": ["contrast"],
    "gain": ["gain"],
    "auto_exposure": ["auto_exposure", "exposure_auto"],
    "exposure": ["exposure_time_absolute", "exposure_absolute"],
}

LINE_RE = re.compile(r"^\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)$")


@dataclass
class Control:
    name: str            # kernel name, e.g. focus_absolute
    kind: str            # int / bool / menu
    min: int = 0
    max: int = 1
    step: int = 1
    default: int = 0
    value: int = 0
    inactive: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def available() -> bool:
    return shutil.which("v4l2-ctl") is not None


def parse_ctrls(text: str) -> dict[str, Control]:
    out = {}
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m or m.group(2) not in ("int", "bool", "menu"):
            continue
        name, kind, rest = m.groups()
        vals = dict(re.findall(r"(\w+)=(-?\d+)", rest))
        c = Control(name, kind, **{k: int(v) for k, v in vals.items() if k in ("min", "max", "step", "default", "value")})
        if kind == "bool":
            c.min, c.max = 0, 1
        c.inactive = "inactive" in rest
        out[name] = c
    return out


def list_controls(device: str) -> dict[str, Control]:
    if not available():
        return {}
    r = subprocess.run(["v4l2-ctl", "-d", device, "--list-ctrls"], capture_output=True, text=True, timeout=5)
    return parse_ctrls(r.stdout)


def resolve(controls: dict[str, Control], ours: str) -> str | None:
    """Kernel name for one of our control names, if this camera has it."""
    return next((n for n in ALIASES.get(ours, [ours]) if n in controls), None)


def set_control(device: str, kernel_name: str, value: int) -> None:
    if not available():
        raise RuntimeError("v4l2-ctl is not installed (apt install v4l-utils)")
    r = subprocess.run(["v4l2-ctl", "-d", device, "-c", f"{kernel_name}={int(value)}"],
                       capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError(f"couldn't set {kernel_name}: {r.stderr.strip() or r.stdout.strip()}")
