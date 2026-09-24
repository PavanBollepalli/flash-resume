"""Windows autostart registration for the companion server.

Registers a per-user Run-key entry so ``flash_resume.bootstrap`` (the silent
server boot) starts automatically at logon, with no console window and no
admin rights required.

Run key chosen over Task Scheduler: a single HKCU registry write, no
schtasks/COM edge cases, and it works without elevation.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("flash_resume.tailor")

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "FlashResume"


class AutostartUnsupportedError(RuntimeError):
    """Raised when autostart is requested on a non-Windows platform."""


def _require_windows() -> None:
    if os.name != "nt":
        raise AutostartUnsupportedError(
            "Autostart is only supported on Windows. "
            "Start the server manually with 'fs serve'."
        )


def server_command() -> str:
    """Return the command line used to silently start the server at logon.

    Uses the ``pythonw.exe`` sibling of the running interpreter so no console
    window is shown, invoking the ``flash_resume.bootstrap`` boot module.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    return f'"{pythonw}" -m flash_resume.bootstrap'


def install() -> str:
    """Register the autostart Run-key value and return the command string."""
    _require_windows()
    cmd = server_command()
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
    except OSError as e:  # noqa: PERF203 - only reached on failure
        logger.exception("Failed to write autostart Run key")
        raise RuntimeError(f"Could not register autostart: {e}") from e
    logger.info("Autostart registered: %s", cmd)
    return cmd


def uninstall() -> bool:
    """Remove the autostart Run-key value. Returns True if one was removed."""
    _require_windows()
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                return False
    except OSError as e:
        logger.exception("Failed to remove autostart Run key")
        raise RuntimeError(f"Could not unregister autostart: {e}") from e
    logger.info("Autostart removed")
    return True


def status() -> tuple[bool, str]:
    """Return (is_registered, command_or_reason) for the autostart entry."""
    _require_windows()
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            try:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                return False, "Not registered"
            return True, value
    except FileNotFoundError:
        return False, "Run key path not present"
    except OSError as e:
        return False, f"Could not read Run key: {e}"
