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

__version__ = "0.1.0"
