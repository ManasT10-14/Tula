"""Runtime location shared by the web application and administration commands."""
from __future__ import annotations

import os
from pathlib import Path


def runtime_root() -> Path:
    override = os.environ.get("TULA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    checkout = Path(__file__).resolve().parents[2]
    return checkout if (checkout / "pyproject.toml").is_file() else Path.home() / ".tula"
