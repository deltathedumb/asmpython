"""Machine-readable build reports shared by compiler stages and plugins."""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


_current_report: contextvars.ContextVar["BuildReport | None"] = contextvars.ContextVar(
    "asmpython_build_report", default=None
)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return repr(value)


@dataclass
class BuildReport:
    path: Path
    argv: list[str]
    options: dict[str, Any]
    started_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.perf_counter)
    events: list[dict[str, Any]] = field(default_factory=list)
    extensions: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, kind: str, **data: Any) -> None:
        self.events.append({
            "kind": kind,
            "offset_seconds": time.perf_counter() - self.started_monotonic,
            **{key: _json_value(value) for key, value in data.items()},
        })

    def add_extension(self, *, id: str, version: str, scope: str, path: Path,
                      production_suitable: bool) -> None:
        self.extensions.append({
            "id": id,
            "version": version,
            "scope": scope,
            "path": str(path),
            "production_suitable": bool(production_suitable),
        })

    def write(self, *, exit_code: int, error: str | None = None) -> None:
        finished = time.time()
        payload = {
            "format": "asmpython.build-report",
            "format_version": 1,
            "argv": self.argv,
            "options": _json_value(self.options),
            "started_at_unix": self.started_at,
            "finished_at_unix": finished,
            "duration_seconds": time.perf_counter() - self.started_monotonic,
            "exit_code": int(exit_code),
            "success": exit_code == 0,
            "error": error,
            "host": {
                "platform": platform.platform(),
                "python": sys.version,
                "executable": sys.executable,
                "cwd": os.getcwd(),
            },
            "extensions": self.extensions,
            "events": self.events,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


@contextlib.contextmanager
def report_session(path: Path | None, argv: list[str], options: dict[str, Any]) -> Iterator[BuildReport | None]:
    if path is None:
        yield None
        return
    report = BuildReport(path=path, argv=list(argv), options=dict(options))
    token = _current_report.set(report)
    try:
        yield report
    finally:
        _current_report.reset(token)


def current_report() -> BuildReport | None:
    return _current_report.get()


def verbosity() -> int:
    """0 = quiet (default), 1 = stages, 2 = every event.

    Read fresh each call rather than cached at import so a test or a harness
    can turn it on around one build.
    """
    raw = os.environ.get("ASMPYTHON_VERBOSE", "")
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 1                      # any non-numeric truthy value means "on"


def _render(kind: str, data: dict[str, Any]) -> str:
    parts = []
    for key, value in data.items():
        if value is None or key == "success":
            continue
        if key == "duration_seconds":
            parts.append(f"{value * 1000:.0f}ms")
            continue
        parts.append(f"{key}={_json_value(value)}")
    return f"  {kind:<22} {' '.join(parts)}".rstrip()


def _echo(kind: str, data: dict[str, Any], *, level: int) -> None:
    if verbosity() >= level:
        print(_render(kind, data), file=sys.stderr, flush=True)


def event(kind: str, **data: Any) -> None:
    report = current_report()
    if report is not None:
        report.add_event(kind, **data)
    _echo(kind, data, level=2)


@contextlib.contextmanager
def stage(kind: str, **data: Any) -> Iterator[None]:
    start = time.perf_counter()
    event(f"{kind}.start", **data)
    try:
        yield
    except BaseException as exc:
        event(
            f"{kind}.finish",
            duration_seconds=time.perf_counter() - start,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            **data,
        )
        raise
    else:
        elapsed = time.perf_counter() - start
        event(
            f"{kind}.finish",
            duration_seconds=elapsed,
            success=True,
            **data,
        )
        # At -v a stage reports once, on completion, with its duration. At -vv
        # the paired start/finish events already say that, so don't say it
        # three times.
        if verbosity() == 1:
            _echo(kind, {**data, "duration_seconds": elapsed}, level=1)


__all__ = ["BuildReport", "current_report", "event", "report_session", "stage"]
