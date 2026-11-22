"""Decode every saved capture and compare with its .json sidecar.

Sidecar format (written by hand or by the calibration page's "Save capture"):
    {"description": "...", "distributor": "MOUSER",
     "expected":      {"mpn": "...", "original_qty": 50},   # must decode today
     "expected_todo": {"mpn": "..."},                       # known not decodable yet (xfail)
     "todo_reason":   "why"}

New captures are picked up automatically; no code change needed.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import cv2
import pytest

from scanner.config import CAPTURES_DIR, REFERENCE_CAPTURES
from scanner.decode.parse import interpret
from scanner.decode.reader import decode_frame


def _captures() -> list[Path]:
    out = []
    for folder in (REFERENCE_CAPTURES, CAPTURES_DIR):
        if folder.exists():
            out += sorted(p for p in folder.iterdir()
                          if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.with_suffix(".json").exists())
    return out


@lru_cache(maxsize=None)
def _scan(path: Path):
    return interpret(decode_frame(cv2.imread(str(path)), thorough=True))


def _cases(key: str):
    for p in _captures():
        spec = json.loads(p.with_suffix(".json").read_text())
        if spec.get(key):
            marks = [pytest.mark.xfail(reason=spec.get("todo_reason", "not decodable yet"), strict=False)] \
                if key == "expected_todo" else []
            yield pytest.param(p, spec[key], id=p.stem, marks=marks)


def _check(path: Path, expected: dict):
    got = _scan(path).fields
    wrong = {k: (got.get(k), v) for k, v in expected.items() if got.get(k) != v}
    assert not wrong, f"{path.name}: field -> (decoded, expected): {wrong}"


@pytest.mark.parametrize("path,expected", list(_cases("expected")))
def test_capture_decodes(path, expected):
    _check(path, expected)


@pytest.mark.parametrize("path,expected", list(_cases("expected_todo")))
def test_capture_todo(path, expected):
    _check(path, expected)
