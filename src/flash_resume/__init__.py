"""Flash Resume package."""

from __future__ import annotations

import sys

# Ensure Windows terminal standard streams cleanly support UTF-8 characters and emojis
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from importlib.metadata import version as _version

    __version__ = _version("flash-resume")
except Exception:
    # Fallback if the package isn't installed (e.g. running from source).
    __version__ = "0.2.6"
