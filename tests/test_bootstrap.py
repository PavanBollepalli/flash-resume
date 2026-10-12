"""Regression tests for the console-less autostart boot path.

``pythonw.exe`` launched by Explorer at logon has no console and no redirected
handles, so Python sets ``sys.stdout``/``sys.stderr`` to ``None``. uvicorn's
default logging config calls ``sys.stdout.isatty()`` while building its
formatters, which killed the engine before it bound the port — silently.
"""

from __future__ import annotations

import logging.config

import pytest

from flash_resume import bootstrap


@pytest.fixture
def console_less(monkeypatch, tmp_path):
    """Reproduce the logon condition: no standard streams at all."""
    log_file = tmp_path / "engine.log"
    monkeypatch.setattr(bootstrap, "engine_log_path", lambda: log_file)
    monkeypatch.setattr(bootstrap.sys, "stdout", None)
    monkeypatch.setattr(bootstrap.sys, "stderr", None)
    monkeypatch.setattr(bootstrap.sys, "stdin", None)
    return log_file


def test_ensure_stdio_gives_uvicorn_real_streams(console_less):
    """The engine must survive having no stdio, not die before it binds."""
    bootstrap._ensure_stdio()

    assert bootstrap.sys.stdout is not None
    assert bootstrap.sys.stderr is not None
    assert bootstrap.sys.stdout.isatty() is False

    import uvicorn.config as uc

    # This is the exact call that raised "Unable to configure formatter
    # 'default'" before the fix, because sys.stdout was None.
    logging.config.dictConfig(uc.LOGGING_CONFIG)


def test_main_boots_on_the_companion_port(console_less, monkeypatch):
    """main() reaches uvicorn with the expected host/port, stdio or not."""
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: captured.update(a=a, kw=kw))

    bootstrap.main()

    assert captured["kw"] == {
        "host": "127.0.0.1",
        "port": 13450,
        "reload": False,
    }


def test_engine_log_records_a_crash(console_less):
    """A silent logon failure must leave evidence in engine.log."""
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        bootstrap._log_crash()

    text = console_less.read_text(encoding="utf-8")
    assert "flash-resume engine crashed" in text
    assert "RuntimeError: boom" in text


def test_ensure_stdio_is_a_noop_with_a_console(monkeypatch, tmp_path, capsys):
    """`fs serve` / piped runs keep the streams they already have."""
    monkeypatch.setattr(bootstrap, "engine_log_path", lambda: tmp_path / "engine.log")
    out_before, err_before = bootstrap.sys.stdout, bootstrap.sys.stderr

    assert bootstrap._ensure_stdio() is None
    assert bootstrap.sys.stdout is out_before
    assert bootstrap.sys.stderr is err_before
