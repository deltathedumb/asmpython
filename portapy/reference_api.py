"""Python-authored reference implementation of PortaPy's embedding API.

This is intentionally an API model, not a native-language interpreter core.  It
wraps pyinbin's Python-written frontend and virtual machine so handle ownership,
status returns, conversion rules, and error behavior can be tested before
asmpython's library-export path emits the final DLL/shared-library symbols.

The standalone PortaPy project will fork the reusable pyinbin sources rather
than import ``asmpython`` at runtime.  Keeping this bootstrap adapter in-tree
lets both products share conformance tests while that extraction is underway.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import traceback

from asmpython.pyinbin.frontend import compile_source
from asmpython.pyinbin.loader import default_builtins
from asmpython.pyinbin.vm import VirtualMachine


class Status(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    COMPILE_ERROR = 2
    RUNTIME_ERROR = 3
    TYPE_ERROR = 4
    NOT_FOUND = 5
    CLOSED = 6
    INVALID_HANDLE = 7


class ValueKind(IntEnum):
    NONE = 0
    BOOL = 1
    INT = 2
    FLOAT = 3
    STRING = 4
    BYTES = 5
    CALLABLE = 6
    OBJECT = 7


@dataclass(frozen=True)
class ErrorInfo:
    status: Status
    type_name: str
    message: str
    traceback_text: str


@dataclass
class _ValueSlot:
    value: object
    refs: int = 1


class Runtime:
    """One isolated PortaPy interpreter instance with opaque integer handles."""

    def __init__(self) -> None:
        self._vm = VirtualMachine()
        self._globals: dict[str, object] = default_builtins()
        self._globals.update(
            {
                "__name__": "__main__",
                "__package__": "",
                "__doc__": None,
            }
        )
        self._values: dict[int, _ValueSlot] = {}
        self._next_handle = 1
        self._eval_counter = 0
        self._last_error: ErrorInfo | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> Status:
        if self._closed:
            return Status.CLOSED
        self._values.clear()
        self._globals.clear()
        self._last_error = None
        self._closed = True
        return Status.OK

    def clear_error(self) -> None:
        self._last_error = None

    def last_error(self) -> ErrorInfo | None:
        return self._last_error

    def _capture(self, status: Status, error: BaseException) -> Status:
        self._last_error = ErrorInfo(
            status=status,
            type_name=type(error).__name__,
            message=str(error),
            traceback_text="".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        )
        return status

    def _ready(self) -> Status | None:
        if self._closed:
            self._last_error = ErrorInfo(
                status=Status.CLOSED,
                type_name="RuntimeClosed",
                message="PortaPy runtime has been destroyed",
                traceback_text="",
            )
            return Status.CLOSED
        self._last_error = None
        return None

    def _store(self, value: object) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self._values[handle] = _ValueSlot(value=value)
        return handle

    def _slot(self, handle: int) -> _ValueSlot | None:
        return self._values.get(handle)

    def exec_utf8(self, source: str, filename: str = "<portapy>") -> Status:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready
        if not isinstance(source, str) or not isinstance(filename, str):
            return self._capture(
                Status.INVALID_ARGUMENT,
                TypeError("source and filename must be strings"),
            )
        try:
            code = compile_source(source, filename)
        except BaseException as error:
            return self._capture(Status.COMPILE_ERROR, error)
        try:
            self._vm.run(code, self._globals)
        except BaseException as error:
            return self._capture(Status.RUNTIME_ERROR, error)
        return Status.OK

    def eval_utf8(
        self,
        expression: str,
        filename: str = "<portapy-eval>",
    ) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if not isinstance(expression, str) or not isinstance(filename, str):
            status = self._capture(
                Status.INVALID_ARGUMENT,
                TypeError("expression and filename must be strings"),
            )
            return status, 0
        self._eval_counter += 1
        result_name = f"__portapy_eval_result_{self._eval_counter}"
        source = f"{result_name} = ({expression})\n"
        try:
            code = compile_source(source, filename)
        except BaseException as error:
            return self._capture(Status.COMPILE_ERROR, error), 0
        try:
            self._vm.run(code, self._globals)
            value = self._globals.pop(result_name)
        except BaseException as error:
            self._globals.pop(result_name, None)
            return self._capture(Status.RUNTIME_ERROR, error), 0
        return Status.OK, self._store(value)

    def get_global(self, name: str) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if not isinstance(name, str):
            return self._capture(Status.INVALID_ARGUMENT, TypeError("name must be a string")), 0
        if name not in self._globals:
            return self._capture(Status.NOT_FOUND, KeyError(name)), 0
        return Status.OK, self._store(self._globals[name])

    def call(self, callable_handle: int, args: list[int] | None = None) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        target_slot = self._slot(callable_handle)
        if target_slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(callable_handle)), 0
        arg_values: list[object] = []
        for handle in args or []:
            slot = self._slot(handle)
            if slot is None:
                return self._capture(Status.INVALID_HANDLE, KeyError(handle)), 0
            arg_values.append(slot.value)
        try:
            result = self._vm._call(target_slot.value, arg_values)
        except BaseException as error:
            return self._capture(Status.RUNTIME_ERROR, error), 0
        return Status.OK, self._store(result)

    def retain(self, handle: int) -> Status:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready
        slot = self._slot(handle)
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle))
        slot.refs += 1
        return Status.OK

    def release(self, handle: int) -> Status:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready
        slot = self._slot(handle)
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle))
        slot.refs -= 1
        if slot.refs <= 0:
            del self._values[handle]
        return Status.OK

    def box_none(self) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        return Status.OK, self._store(None)

    def box_bool(self, value: bool) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if type(value) is not bool:
            return self._capture(Status.TYPE_ERROR, TypeError("value must be bool")), 0
        return Status.OK, self._store(value)

    def box_int(self, value: int) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if type(value) is not int:
            return self._capture(Status.TYPE_ERROR, TypeError("value must be int")), 0
        return Status.OK, self._store(value)

    def box_float(self, value: float) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if type(value) is not float:
            return self._capture(Status.TYPE_ERROR, TypeError("value must be float")), 0
        return Status.OK, self._store(value)

    def box_utf8(self, value: str) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if type(value) is not str:
            return self._capture(Status.TYPE_ERROR, TypeError("value must be str")), 0
        return Status.OK, self._store(value)

    def box_bytes(self, value: bytes) -> tuple[Status, int]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, 0
        if type(value) is not bytes:
            return self._capture(Status.TYPE_ERROR, TypeError("value must be bytes")), 0
        return Status.OK, self._store(value)

    def value_kind(self, handle: int) -> tuple[Status, ValueKind]:
        not_ready = self._ready()
        if not_ready is not None:
            return not_ready, ValueKind.OBJECT
        slot = self._slot(handle)
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), ValueKind.OBJECT
        value = slot.value
        if value is None:
            kind = ValueKind.NONE
        elif type(value) is bool:
            kind = ValueKind.BOOL
        elif type(value) is int:
            kind = ValueKind.INT
        elif type(value) is float:
            kind = ValueKind.FLOAT
        elif type(value) is str:
            kind = ValueKind.STRING
        elif type(value) is bytes:
            kind = ValueKind.BYTES
        elif callable(value):
            kind = ValueKind.CALLABLE
        else:
            kind = ValueKind.OBJECT
        return Status.OK, kind

    def as_bool(self, handle: int) -> tuple[Status, bool]:
        slot = self._slot(handle)
        if self._closed:
            return Status.CLOSED, False
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), False
        if type(slot.value) is not bool:
            return self._capture(Status.TYPE_ERROR, TypeError("value is not bool")), False
        self._last_error = None
        return Status.OK, slot.value

    def as_int(self, handle: int) -> tuple[Status, int]:
        slot = self._slot(handle)
        if self._closed:
            return Status.CLOSED, 0
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), 0
        if type(slot.value) is not int:
            return self._capture(Status.TYPE_ERROR, TypeError("value is not int")), 0
        self._last_error = None
        return Status.OK, slot.value

    def as_float(self, handle: int) -> tuple[Status, float]:
        slot = self._slot(handle)
        if self._closed:
            return Status.CLOSED, 0.0
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), 0.0
        if type(slot.value) is not float:
            return self._capture(Status.TYPE_ERROR, TypeError("value is not float")), 0.0
        self._last_error = None
        return Status.OK, slot.value

    def as_utf8(self, handle: int) -> tuple[Status, bytes]:
        slot = self._slot(handle)
        if self._closed:
            return Status.CLOSED, b""
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), b""
        if type(slot.value) is not str:
            return self._capture(Status.TYPE_ERROR, TypeError("value is not str")), b""
        self._last_error = None
        return Status.OK, slot.value.encode("utf-8")

    def as_bytes(self, handle: int) -> tuple[Status, bytes]:
        slot = self._slot(handle)
        if self._closed:
            return Status.CLOSED, b""
        if slot is None:
            return self._capture(Status.INVALID_HANDLE, KeyError(handle)), b""
        if type(slot.value) is not bytes:
            return self._capture(Status.TYPE_ERROR, TypeError("value is not bytes")), b""
        self._last_error = None
        return Status.OK, slot.value
