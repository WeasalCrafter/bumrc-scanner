"""Paths and settings shared by the whole app.

Everything the app writes (camera settings, saved captures, label images) lives
under DATA_DIR, so in Docker a single volume keeps it across restarts.
Set DATA_DIR in the environment or .env to move it; default is ./data.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
LOGO_FILE = ROOT / "scanner" / "assets" / "mrc-logo.png"
REFERENCE_CAPTURES = ROOT / "references" / "captures"


def load_env() -> dict:
    """Settings from .env, overridden by real environment variables."""
    env = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None} if ENV_FILE.exists() else {}
    env.update({k: v for k, v in os.environ.items()})
    return env


def _data_dir() -> Path:
    return Path(load_env().get("DATA_DIR") or ROOT / "data")


DATA_DIR = _data_dir()
OUT_DIR = DATA_DIR / "labels"                    # last few printed labels, as PNGs
CAPTURES_DIR = DATA_DIR / "captures"             # frames saved from the calibration page
CAMERA_SETTINGS_FILE = DATA_DIR / "camera.json"  # result of camera calibration
