# version.py — single source of truth for the app version.
#
# Reads the VERSION file (bundled into the frozen build via the .spec datas),
# so the in-app version can never drift from the tag CI builds and ships.

import os
import sys

REPO = "Godimas101/claude-usage-monitor"


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _read_version() -> str:
    try:
        with open(os.path.join(_base_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


APP_VERSION = _read_version()
