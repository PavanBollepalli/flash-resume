"""Locate and install the bundled Chrome extension.

The extension folder is shipped inside the wheel under
``flash_resume/extension``. Users Load-unpack it in Chrome, so it must live in a
stable, writable location (``%LOCALAPPDATA%\\flash-resume\\extension``) rather
than the site-packages copy, which pip may overwrite or lock on upgrade.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("flash_resume.tailor")

def bundled_dir() -> Path:
    """Return the directory holding the extension to be installed.

    Inside a built/installed wheel the extension ships under the package tree
    (``flash_resume/extension`` via ``force-include``); when running from a
    source checkout it lives at the repo root next to ``src/``. Resolve to the
    first location that actually exists so both layouts work.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "extension",                       # installed wheel copy
        here.parent.parent.parent / "extension",         # source repo root
    ]
    for candidate in candidates:
        if (candidate / "manifest.json").exists():
            return candidate
    raise FileNotFoundError(
        "Bundled extension not found. Expected it at one of: "
        + ", ".join(str(c) for c in candidates)
    )


def default_target_dir() -> Path:
    """Return the stable per-user directory the extension is copied to."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "flash-resume" / "extension"


def install_to(where: Path | None = None) -> Path:
    """Copy the bundled extension to a stable location and return that path.

    If ``where`` is None, defaults to
    ``%LOCALAPPDATA%\\flash-resume\\extension``. Idempotent: re-copies the
    bundled files over any existing copy.
    """
    src = bundled_dir()
    dst = where or default_target_dir()
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    logger.info("Extension installed to %s", dst)
    return dst


def display_path() -> Path:
    """Return the path users should Load-unpack from, ensuring it exists."""
    dst = default_target_dir()
    if not (dst / "manifest.json").exists():
        install_to(dst)
    return dst
