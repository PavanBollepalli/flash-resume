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
import subprocess
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


def _pythonw_path() -> Path:
    """Return the windowless interpreter to launch the boot module with.

    Prefers the ``pythonw.exe`` sibling of the running interpreter so no
    console window is shown.
    """
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return pythonw if pythonw.exists() else Path(sys.executable)


def server_command() -> str:
    """Return the command line used to silently start the server at logon.

    Uses the ``pythonw.exe`` sibling of the running interpreter so no console
    window is shown, invoking the ``flash_resume.bootstrap`` boot module.
    """
    return f'"{_pythonw_path()}" -m flash_resume.bootstrap'


def _verify_interpreter(exe: Path) -> None:
    """Fail loudly if ``exe`` cannot import the server.

    The Run key is pinned to whatever interpreter ran ``fs setup``. If that
    interpreter is later deleted (a dev venv) or never had the package, the
    logon entry dies silently with no console to explain why. Output is
    piped, so even ``pythonw`` reports its traceback here.
    """
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import flash_resume.services.server"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f"The autostart interpreter could not be run: {exe}\n{e}"
        ) from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"{exe} cannot import Flash Resume, so the logon entry would fail "
            f"silently:\n{detail[-800:]}"
        )


def engine_is_running(port: int = 13450) -> bool:
    """Return True if something is already serving on the companion port."""
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_now() -> None:
    """Start the companion server without opening a console window.

    Autostart registration only affects the next Windows logon. Setup calls
    this separately so the extension can be used immediately.
    """
    _require_windows()

    from flash_resume.bootstrap import PORT

    if engine_is_running(PORT):
        logger.info("Companion server already listening on port %s", PORT)
        return

    pythonw = _pythonw_path()

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [str(pythonw), "-m", "flash_resume.bootstrap"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as e:
        logger.exception("Failed to start companion server")
        raise RuntimeError(f"Could not start companion server: {e}") from e


def install() -> str:
    """Register the autostart Run-key value and return the command string."""
    _require_windows()
    # Check the command can actually run before pinning it into the Run key:
    # a broken registration is invisible at logon, which is the whole failure
    # mode this feature has to avoid.
    _verify_interpreter(_pythonw_path())
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
