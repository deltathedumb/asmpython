"""Unified native, host, and PyinBin traceback records."""
from __future__ import annotations

import contextlib
import contextvars
import traceback as host_traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import memory as _memory
from .memory import MemoryManager, Ownership


@dataclass(frozen=True)
class TraceFrame:
    engine: str
    filename: str
    function: str
    line: int
    column: int | None = None
    instruction: int | None = None
    detail: str | None = None

    def key(self) -> tuple[object, ...]:
        return (
            self.engine, self.filename, self.function, self.line,
            self.column, self.instruction,
        )


@dataclass
class MixedTraceback:
    exception_type: str
    message: str
    frames: list[TraceFrame] = field(default_factory=list)
    cause: "MixedTraceback | None" = None
    context: "MixedTraceback | None" = None
    suppress_context: bool = False

    def add(self, frame: TraceFrame) -> None:
        if not self.frames or self.frames[-1].key() != frame.key():
            self.frames.append(frame)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_type": self.exception_type,
            "message": self.message,
            "frames": [frame.__dict__ for frame in reversed(self.frames)],
            "cause": self.cause.as_dict() if self.cause else None,
            "context": self.context.as_dict() if self.context else None,
            "suppress_context": self.suppress_context,
        }


class MixedTracebackError(RuntimeError):
    """CLI-facing carrier that preserves the original exception as ``cause``."""

    def __init__(self, original: BaseException) -> None:
        self.original = original
        self.mixed_traceback = get_mixed_traceback(original, include_host=True)
        super().__init__(format_mixed_traceback(self.mixed_traceback))
        self.__cause__ = original


_TRACE_ATTRIBUTE = "__asmpython_mixed_traceback__"
_native_frames: contextvars.ContextVar[tuple[TraceFrame, ...]] = contextvars.ContextVar(
    "asmpython_native_trace_frames", default=()
)


def _new_trace(exception: BaseException) -> MixedTraceback:
    return MixedTraceback(
        exception_type=type(exception).__name__,
        message=str(exception),
        suppress_context=bool(getattr(exception, "__suppress_context__", False)),
    )


def _trace_for(exception: BaseException) -> MixedTraceback:
    existing = getattr(exception, _TRACE_ATTRIBUTE, None)
    if isinstance(existing, MixedTraceback):
        return existing
    trace = _new_trace(exception)
    try:
        setattr(exception, _TRACE_ATTRIBUTE, trace)
    except BaseException:
        pass
    return trace


def attach_frame(exception: BaseException, frame: TraceFrame) -> MixedTraceback:
    trace = _trace_for(exception)
    trace.add(frame)
    return trace


def attach_native_frame(
    exception: BaseException,
    *,
    filename: str,
    function: str,
    line: int,
    column: int | None = None,
    instruction: int | None = None,
    detail: str | None = None,
) -> MixedTraceback:
    return attach_frame(exception, TraceFrame(
        engine="native", filename=filename, function=function, line=line,
        column=column, instruction=instruction, detail=detail,
    ))


@contextlib.contextmanager
def native_frame(
    filename: str,
    function: str,
    line: int,
    *,
    column: int | None = None,
    instruction: int | None = None,
    detail: str | None = None,
) -> Iterator[None]:
    frame = TraceFrame(
        engine="native", filename=filename, function=function, line=line,
        column=column, instruction=instruction, detail=detail,
    )
    current = _native_frames.get()
    token = _native_frames.set((*current, frame))
    try:
        yield
    except BaseException as exception:
        attach_frame(exception, frame)
        raise
    finally:
        _native_frames.reset(token)


def _host_frames(exception: BaseException) -> list[TraceFrame]:
    frames: list[TraceFrame] = []
    for entry in host_traceback.extract_tb(exception.__traceback__):
        # Internal hook frames are implementation detail; the meaningful VM
        # frame is recorded separately as engine=pyinbin.
        normalized = entry.filename.replace("\\", "/")
        if normalized.endswith("/_runtime/mixed_traceback.py"):
            continue
        frames.append(TraceFrame(
            engine="host",
            filename=entry.filename,
            function=entry.name,
            line=entry.lineno,
            detail=entry.line,
        ))
    return frames


def get_mixed_traceback(
    exception: BaseException,
    *,
    include_host: bool = True,
    _seen: set[int] | None = None,
) -> MixedTraceback:
    seen = set() if _seen is None else _seen
    if id(exception) in seen:
        return _trace_for(exception)
    seen.add(id(exception))
    trace = _trace_for(exception)

    if include_host:
        existing = {frame.key() for frame in trace.frames}
        # Host traceback extraction is outer->inner, while attached VM frames
        # are stored inner->outer. Append in reverse so final formatting remains
        # outer->inner after the whole list is reversed.
        for frame in reversed(_host_frames(exception)):
            if frame.key() not in existing:
                trace.frames.append(frame)
                existing.add(frame.key())
    for frame in reversed(_native_frames.get()):
        if frame.key() not in {item.key() for item in trace.frames}:
            trace.frames.append(frame)

    cause = exception.__cause__
    context = exception.__context__
    if isinstance(cause, BaseException):
        trace.cause = get_mixed_traceback(cause, include_host=include_host, _seen=seen)
    elif isinstance(context, BaseException) and not trace.suppress_context:
        trace.context = get_mixed_traceback(context, include_host=include_host, _seen=seen)
    return trace


def _format_one(trace: MixedTraceback) -> str:
    lines = ["Traceback (most recent call last):\n"]
    for frame in reversed(trace.frames):
        engine = "" if frame.engine == "host" else f" [{frame.engine}]"
        location = f'  File "{frame.filename}", line {frame.line}, in {frame.function}{engine}\n'
        lines.append(location)
        detail = frame.detail
        if frame.instruction is not None:
            suffix = f"instruction {frame.instruction}"
            detail = f"{detail}; {suffix}" if detail else suffix
        if detail:
            lines.append(f"    {detail}\n")
    lines.append(f"{trace.exception_type}: {trace.message}\n")
    return "".join(lines)


def format_mixed_traceback(trace: MixedTraceback) -> str:
    if trace.cause is not None:
        return (
            format_mixed_traceback(trace.cause)
            + "\nThe above exception was the direct cause of the following exception:\n\n"
            + _format_one(trace)
        )
    if trace.context is not None and not trace.suppress_context:
        return (
            format_mixed_traceback(trace.context)
            + "\nDuring handling of the above exception, another exception occurred:\n\n"
            + _format_one(trace)
        )
    return _format_one(trace)


def format_mixed_exception(exception: BaseException) -> str:
    return format_mixed_traceback(get_mixed_traceback(exception, include_host=True))


def _iter_container_values(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        yield from value.keys()
        yield from value.values()
    elif isinstance(value, (list, tuple, set, frozenset)):
        yield from value


def _frame_children(frame: object) -> Iterator[object]:
    for name in (
        "globals", "locals", "stack", "block_stack", "closure", "active_exception",
        "pending_exception", "awaiting", "awaiting_send", "code",
    ):
        value = getattr(frame, name, None)
        yield value
        yield from _iter_container_values(value)


def _vm_children(vm: object) -> Iterator[object]:
    current = getattr(vm, "_current_frame", None)
    if current is not None:
        yield current
    tracebacks = getattr(vm, "_synthetic_tracebacks", None)
    if tracebacks is not None:
        yield tracebacks
        yield from _iter_container_values(tracebacks)


def _object_children(value: object) -> Iterator[object]:
    for name in (
        "vm", "frame", "code", "globals", "defaults", "kw_defaults", "closure",
        "cls", "attributes", "bases", "instance", "function", "cache",
    ):
        child = getattr(value, name, None)
        if child is not None:
            yield child
            yield from _iter_container_values(child)


def _close_if_possible(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except (StopIteration, StopAsyncIteration, GeneratorExit):
            pass


def _install_constructor_hook(cls: type, *, finalizer: bool = False) -> None:
    marker = "__asmpython_memory_hooked__"
    if getattr(cls, marker, False):
        return
    original = cls.__init__

    def wrapped(self: object, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)
        manager = _memory.current_manager()
        manager.track_internal(
            self,
            trace=_object_children,
            finalizer=_close_if_possible if finalizer else None,
            label=f"pyinbin.{cls.__name__}",
        )

    cls.__init__ = wrapped  # type: ignore[assignment]
    setattr(cls, marker, True)


def install_pyinbin_hooks(vm_module: object) -> None:
    """Install idempotent frame-trace and ownership hooks on the PyinBin VM."""

    vm_cls = getattr(vm_module, "VirtualMachine")
    if getattr(vm_cls, "__asmpython_runtime_hooked__", False):
        return

    original_vm_init = vm_cls.__init__
    original_run = vm_cls.run
    original_run_frame = vm_cls._run_frame

    def vm_init(self: object, *args: object, **kwargs: object) -> None:
        original_vm_init(self, *args, **kwargs)
        manager = MemoryManager(f"pyinbin:{id(self):x}")
        setattr(self, "_asmpython_memory_manager", manager)

    def run(self: object, code: object, globals_: dict[str, object] | None = None) -> object:
        manager: MemoryManager = getattr(self, "_asmpython_memory_manager")
        token = _memory._current_manager.set(manager)
        vm_handle = manager.track(self, ownership=Ownership.OWNED, trace=_vm_children, label="pyinbin.VirtualMachine")
        if globals_ is not None:
            manager.track(
                globals_, ownership=Ownership.HOST,
                trace=lambda mapping: mapping.values() if isinstance(mapping, dict) else (),
                label="pyinbin.globals",
            )
        try:
            result = original_run(self, code, globals_)
            if result is not None and not isinstance(result, (bool, int, float, str, bytes)):
                manager.track(result, ownership=Ownership.HOST, trace=_object_children, label="pyinbin.result")
            return result
        except BaseException as exception:
            for active in reversed(_native_frames.get()):
                attach_frame(exception, active)
            raise
        finally:
            try:
                vm_handle.release()
                manager.collect_cycles()
            finally:
                _memory._current_manager.reset(token)

    def run_frame(self: object, frame: object) -> object:
        manager: MemoryManager = getattr(self, "_asmpython_memory_manager", _memory.current_manager())
        manager.track_internal(frame, trace=_frame_children, label="pyinbin.Frame")
        try:
            return original_run_frame(self, frame)
        except BaseException as exception:
            code = getattr(frame, "code", None)
            globals_ = getattr(frame, "globals", {})
            filename = str(globals_.get("__file__", getattr(code, "co_filename", "<pyinbin>")))
            function = str(getattr(code, "name", getattr(code, "co_name", "<module>")))
            instruction = max(0, int(getattr(frame, "ip", 0)) - 1)
            line = int(getattr(code, "co_firstlineno", 1) or 1)
            attach_frame(exception, TraceFrame(
                engine="pyinbin", filename=filename, function=function,
                line=line, instruction=instruction,
            ))
            raise

    def destroy(self: object) -> None:
        manager = getattr(self, "_asmpython_memory_manager", None)
        if isinstance(manager, MemoryManager):
            manager.teardown()

    def vm_del(self: object) -> None:
        try:
            destroy(self)
        except BaseException:
            pass

    vm_cls.__init__ = vm_init
    vm_cls.run = run
    vm_cls._run_frame = run_frame
    vm_cls.destroy = destroy
    if not hasattr(vm_cls, "__del__"):
        vm_cls.__del__ = vm_del
    vm_cls.__asmpython_runtime_hooked__ = True

    for class_name, finalizer in (
        ("Frame", False), ("Function", False), ("PyClass", False),
        ("PyInstance", False), ("GeneratorObject", True),
        ("CoroutineObject", True), ("AsyncGeneratorObject", True),
    ):
        cls = getattr(vm_module, class_name, None)
        if isinstance(cls, type):
            _install_constructor_hook(cls, finalizer=finalizer)


__all__ = [
    "MixedTraceback", "MixedTracebackError", "TraceFrame", "attach_frame",
    "attach_native_frame", "format_mixed_exception", "format_mixed_traceback",
    "get_mixed_traceback", "install_pyinbin_hooks", "native_frame",
]
