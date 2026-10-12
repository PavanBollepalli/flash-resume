"""Silent server-boot entrypoint for background autostart.

Runs the same FastAPI companion server as ``fs serve`` but is intended to be
invoked by a hidden ``pythonw`` process (no console window) at logon, so the
engine is always available to the Chrome extension without the user opening a
terminal.

Use directly with::

    pythonw -m flash_resume.bootstrap
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

HOST = "127.0.0.1"
PORT = 13450


class _NullStream:
    """Minimal stdio stand-in for a console-less ``pythonw`` process.

    Output is discarded, but ``isatty()`` exists so libraries that probe it
    (uvicorn's logging formatter, rich, ...) don't crash on ``None``.
    """

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self):  # pragma: no cover - only reached by fd-level callers
        raise OSError("no file descriptor: running without a console")

    def close(self) -> None:  # pragma: no cover
        pass


def engine_log_path() -> Path:
    """Return the file the console-less engine logs to."""
    from flash_resume.config import get_config_dir

    return get_config_dir() / "engine.log"


def _ensure_stdio() -> Path | None:
    """Give the console-less logon process real standard streams.

    ``pythonw.exe`` launched by Explorer at logon has no console and no
    redirected handles, so Python sets ``sys.stdout``/``sys.stderr``/
    ``sys.stdin`` to ``None``. uvicorn's default logging config calls
    ``sys.stdout.isatty()`` while building its formatters, which raised
    ``AttributeError`` and killed the engine before it ever bound the port —
    silently, because there was no stream left to report it on.

    Point both output streams at ``engine.log`` so the engine boots and every
    log line survives; fall back to a discard stream if the file can't be
    opened. Returns the log path when one was attached, else ``None``.
    """
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return None

    log_file = None
    if sys.stdout is None or sys.stderr is None:
        try:
            log_file = open(engine_log_path(), "a", encoding="utf-8", buffering=1)
        except OSError:
            log_file = None

    if sys.stdout is None:
        sys.stdout = log_file or _NullStream()
    if sys.stderr is None:
        sys.stderr = log_file or _NullStream()
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull, encoding="utf-8")
        except OSError:  # pragma: no cover - devnull is always openable
            sys.stdin = _NullStream()

    return engine_log_path() if log_file is not None else None


def _log_crash() -> None:
    """Append the in-flight exception to the engine log.

    Never masks the original error: this process has no console, so an
    appended traceback is the only evidence a logon attempt happened.
    """
    try:
        with open(engine_log_path(), "a", encoding="utf-8") as fh:
            fh.write("\n=== flash-resume engine crashed ===\n")
            traceback.print_exc(file=fh)
    except OSError:
        pass


def main() -> None:
    """Start uvicorn on the same app/port the ``fs serve`` command uses."""
    _ensure_stdio()
    try:
        import uvicorn

        uvicorn.run(
            "flash_resume.services.server:app",
            host=HOST,
            port=PORT,
            reload=False,
        )
    except BaseException:
        _log_crash()
        raise


if __name__ == "__main__":
    main()
