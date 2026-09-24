"""Silent server-boot entrypoint for background autostart.

Runs the same FastAPI companion server as ``fs serve`` but is intended to be
invoked by a hidden ``pythonw`` process (no console window) at logon, so the
engine is always available to the Chrome extension without the user opening a
terminal.

Use directly with::

    pythonw -m flash_resume.bootstrap
"""

from __future__ import annotations


def main() -> None:
    """Start uvicorn on the same app/port the ``fs serve`` command uses."""
    import uvicorn

    uvicorn.run(
        "flash_resume.services.server:app",
        host="127.0.0.1",
        port=13450,
        reload=False,
    )


if __name__ == "__main__":
    main()
