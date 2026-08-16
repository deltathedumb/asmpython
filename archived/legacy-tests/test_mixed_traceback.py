from __future__ import annotations

import os
from pathlib import Path

import pytest

from asmpython.pyinbin import MixedTracebackError, run_source
from asmpython.runtime import (
    attach_native_frame,
    format_mixed_exception,
    get_mixed_traceback,
    native_frame,
)


def test_native_frame_context_is_attached() -> None:
    try:
        with native_frame("native_module.py", "native_call", 17):
            raise ValueError("boom")
    except ValueError as exc:
        trace = get_mixed_traceback(exc)
        rendered = format_mixed_exception(exc)
    assert any(frame.engine == "native" for frame in trace.frames)
    assert "native_module.py" in rendered
    assert "ValueError: boom" in rendered


def test_explicit_native_attachment() -> None:
    exc = RuntimeError("failure")
    attach_native_frame(exc, filename="generated.py", function="entry", line=4)
    rendered = format_mixed_exception(exc)
    assert "[native]" in rendered
    assert "generated.py" in rendered


def test_pyinbin_uncaught_error_has_interpreted_frames(tmp_path: Path) -> None:
    source = tmp_path / "boom.py"
    source.write_text(
        "def inner():\n"
        "    return 1 / 0\n"
        "\n"
        "def outer():\n"
        "    return inner()\n"
        "\n"
        "outer()\n",
        encoding="utf-8",
    )
    with pytest.raises(ZeroDivisionError) as caught:
        run_source(source)
    trace = get_mixed_traceback(caught.value)
    assert any(frame.engine == "pyinbin" for frame in trace.frames)
    rendered = format_mixed_exception(caught.value)
    assert str(source) in rendered
    assert "ZeroDivisionError" in rendered


def test_cli_mode_uses_printable_mixed_traceback_carrier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "fail.py"
    source.write_text("raise ValueError('bad')\n", encoding="utf-8")
    monkeypatch.setenv("ASMPYTHON_CLI_MIXED_TRACEBACK", "1")
    with pytest.raises(MixedTracebackError) as caught:
        run_source(source)
    text = str(caught.value)
    assert "Traceback (most recent call last):" in text
    assert "ValueError" in text
