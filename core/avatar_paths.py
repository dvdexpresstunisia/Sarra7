"""Shared paths for persistent avatar settings and bundled defaults."""
from __future__ import annotations

import sys
from pathlib import Path


def avatar_config_dir() -> Path:
    """Return the avatar settings folder shared with the source app when built in dist/."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent / "config"

    executable_dir = Path(sys.executable).resolve().parent
    project_dir = executable_dir.parent
    if (project_dir / "main.py").is_file() and (project_dir / "Sarra.spec").is_file():
        return project_dir / "config"
    return executable_dir / "config"


def avatar_config_path(name: str) -> Path:
    return avatar_config_dir() / name


def bundled_avatar_config_path(name: str) -> Path:
    """Return the packaged default path, or the source config during development."""
    bundle_dir = Path(getattr(
        sys, "_MEIPASS", Path(__file__).resolve().parent.parent
    ))
    return bundle_dir / "config" / name
