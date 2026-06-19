"""asyncio: asynchronous I/O framework.

In asmpython, ``async def`` and ``await`` are not supported. This module
provides the API surface for code that *references* asyncio types (e.g.
type annotations, exception classes, constants), but raises NotImplementedError
for any runtime execution.

If you need cooperative concurrency, use ``threading.Thread`` instead.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    """Raised when a Future or Task is cancelled."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "CancelledError" if not self.msg else self.msg


class TimeoutError(Exception):
    """Raised when a timeout occurs in asyncio operations."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "TimeoutError" if not self.msg else self.msg


class InvalidStateError(Exception):
    """Raised when an operation is performed on a Future in an invalid state."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "InvalidStateError: " + self.msg


class IncompleteReadError(Exception):
    """Raised when EOF is reached before the requested number of bytes."""

    def __init__(self, partial: str = "", expected: int = 0) -> None:
        self.partial: str = partial
        self.expected: int = expected

    def __str__(self) -> str:
        return "IncompleteReadError"


class LimitOverrunError(Exception):
    """Raised when the stream read limit is exceeded."""

    def __init__(self, msg: str = "", consumed: int = 0) -> None:
        self.msg: str = msg
        self.consumed: int = consumed


class SendfileNotAvailableError(Exception):
    """Raised when sendfile is not available."""
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIRST_COMPLETED: str = "FIRST_COMPLETED"
FIRST_EXCEPTION: str = "FIRST_EXCEPTION"
ALL_COMPLETED: str = "ALL_COMPLETED"

# Log levels
LOG_LEVEL_DEBUG: int = 0


# ---------------------------------------------------------------------------
# Event loop stubs
# ---------------------------------------------------------------------------

class AbstractEventLoop:
    """Abstract base for event loops (stub — raises NotImplementedError)."""

    def run_forever(self) -> None:
        raise NotImplementedError("asyncio: async/await not supported in asmpython")

    def run_until_complete(self, future: object) -> object:
        raise NotImplementedError("asyncio: async/await not supported in asmpython")

    def stop(self) -> None:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def is_running(self) -> int:
        return 0

    def is_closed(self) -> int:
        return 1

    def close(self) -> None:
        pass

    def call_soon(self, callback: int, arg0: object = None, arg1: object = None) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def call_later(self, delay: float, callback: int,
                   arg0: object = None, arg1: object = None) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def call_at(self, when: float, callback: int,
                arg0: object = None, arg1: object = None) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def time(self) -> float:
        import time as _time
        return _time.time()

    def create_task(self, coro: object, name: str = "") -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def create_future(self) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def create_connection(self, protocol_factory: int, host: str = "",
                          port: int = 0, ssl: object = None) -> list:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def create_server(self, protocol_factory: int, host: str = "",
                      port: int = 0, ssl: object = None) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")

    def run_in_executor(self, executor: object, func: int,
                        arg0: object = None) -> object:
        raise NotImplementedError("asyncio: not supported in asmpython")


class BaseEventLoop(AbstractEventLoop):
    """Base event loop implementation (stub)."""
    pass


# ---------------------------------------------------------------------------
# Future / Task stubs
# ---------------------------------------------------------------------------

class Future:
    """Represents an eventual result (stub)."""

    def __init__(self) -> None:
        self._done: int = 0
        self._cancelled: int = 0
        self._result: object = None
        self._exception: object = None

    def done(self) -> int:
        return self._done

    def cancelled(self) -> int:
        return self._cancelled

    def result(self) -> object:
        if self._cancelled:
            raise CancelledError()
        if not self._done:
            raise InvalidStateError("Future is not done")
        return self._result

    def exception(self) -> object:
        if self._cancelled:
            raise CancelledError()
        if not self._done:
            raise InvalidStateError("Future is not done")
        return self._exception

    def set_result(self, result: object) -> None:
        if self._done:
            raise InvalidStateError("Future already done")
        self._result = result
        self._done = 1

    def set_exception(self, exception: object) -> None:
        if self._done:
            raise InvalidStateError("Future already done")
        self._exception = exception
        self._done = 1

    def cancel(self, msg: str = "") -> int:
        if self._done:
            return 0
        self._cancelled = 1
        self._done = 1
        return 1

    def add_done_callback(self, fn: int, context: object = None) -> None:
        raise NotImplementedError("asyncio.Future.add_done_callback: not supported")

    def remove_done_callback(self, fn: int) -> int:
        return 0

    def get_loop(self) -> AbstractEventLoop:
        raise NotImplementedError("asyncio: not supported in asmpython")


class Task(Future):
    """A coroutine wrapped in a Future (stub)."""

    def __init__(self, coro: object, name: str = "") -> None:
        self._done = 0
        self._cancelled = 0
        self._result = None
        self._exception = None
        self._name: str = name

    def get_name(self) -> str:
        return self._name

    def set_name(self, value: str) -> None:
        self._name = value

    def get_coro(self) -> object:
        return None

    def print_stack(self, limit: int = 0) -> None:
        pass


# ---------------------------------------------------------------------------
# Event loop factory / access
# ---------------------------------------------------------------------------

_loop: AbstractEventLoop = BaseEventLoop()


def get_event_loop() -> AbstractEventLoop:
    """Return the current event loop (stub)."""
    return _loop


def set_event_loop(loop: AbstractEventLoop) -> None:
    """Set the current event loop (stub)."""
    global _loop
    _loop = loop


def new_event_loop() -> AbstractEventLoop:
    """Create a new event loop (stub — raises NotImplementedError)."""
    raise NotImplementedError("asyncio.new_event_loop: not supported in asmpython")


def get_running_loop() -> AbstractEventLoop:
    """Return the running event loop (raises RuntimeError — none is running)."""
    raise RuntimeError("no running event loop")


def run(coro: object, debug: int = 0) -> object:
    """Run a coroutine (raises NotImplementedError)."""
    raise NotImplementedError("asyncio.run: async/await not supported in asmpython")


# ---------------------------------------------------------------------------
# Synchronisation primitives (stubs only — no OS thread integration)
# ---------------------------------------------------------------------------

class Lock:
    """Async-friendly lock (stub — not integrated with any event loop)."""

    def __init__(self) -> None:
        self._locked: int = 0

    def locked(self) -> int:
        return self._locked

    async def acquire(self) -> int:
        raise NotImplementedError("asyncio.Lock.acquire: not supported in asmpython")

    def release(self) -> None:
        self._locked = 0

    def __enter__(self) -> Lock:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.release()
        return 0


class Event:
    """Async event (stub)."""

    def __init__(self) -> None:
        self._set: int = 0

    def is_set(self) -> int:
        return self._set

    def set(self) -> None:
        self._set = 1

    def clear(self) -> None:
        self._set = 0

    async def wait(self) -> int:
        raise NotImplementedError("asyncio.Event.wait: not supported in asmpython")


class Semaphore:
    """Async semaphore (stub)."""

    def __init__(self, value: int = 1) -> None:
        self._value: int = value

    def locked(self) -> int:
        return self._value == 0

    async def acquire(self) -> int:
        raise NotImplementedError("asyncio.Semaphore: not supported in asmpython")

    def release(self) -> None:
        self._value = self._value + 1


class BoundedSemaphore(Semaphore):
    pass


class Condition:
    """Async condition variable (stub)."""

    def __init__(self, lock: Lock = None) -> None:
        self._lock: Lock = lock if lock is not None else Lock()

    async def acquire(self) -> int:
        raise NotImplementedError("asyncio.Condition: not supported in asmpython")

    def release(self) -> None:
        self._lock.release()

    async def wait(self) -> int:
        raise NotImplementedError("asyncio.Condition: not supported in asmpython")

    async def wait_for(self, predicate: int) -> int:
        raise NotImplementedError("asyncio.Condition: not supported in asmpython")

    def notify(self, n: int = 1) -> None:
        pass

    def notify_all(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Queue stubs
# ---------------------------------------------------------------------------

class Queue:
    """Async FIFO queue (stub)."""

    def __init__(self, maxsize: int = 0) -> None:
        self._maxsize: int = maxsize
        self._items: list = []

    def qsize(self) -> int:
        return len(self._items)

    def empty(self) -> int:
        return len(self._items) == 0

    def full(self) -> int:
        if self._maxsize <= 0:
            return 0
        return len(self._items) >= self._maxsize

    def put_nowait(self, item: object) -> None:
        if self._maxsize > 0 and len(self._items) >= self._maxsize:
            raise QueueFull()
        self._items.append(item)

    def get_nowait(self) -> object:
        if not self._items:
            raise QueueEmpty()
        item: object = self._items[0]
        self._items = self._items[1:]
        return item

    async def put(self, item: object) -> None:
        raise NotImplementedError("asyncio.Queue.put: not supported in asmpython")

    async def get(self) -> object:
        raise NotImplementedError("asyncio.Queue.get: not supported in asmpython")

    def task_done(self) -> None:
        pass

    async def join(self) -> None:
        raise NotImplementedError("asyncio.Queue.join: not supported in asmpython")


class QueueEmpty(Exception):
    pass


class QueueFull(Exception):
    pass


class LifoQueue(Queue):
    def get_nowait(self) -> object:
        if not self._items:
            raise QueueEmpty()
        item: object = self._items[len(self._items) - 1]
        self._items = self._items[:len(self._items) - 1]
        return item


class PriorityQueue(Queue):
    pass


# ---------------------------------------------------------------------------
# Coroutine/stream helpers (stubs)
# ---------------------------------------------------------------------------

async def sleep(delay: float, result: object = None) -> object:
    raise NotImplementedError("asyncio.sleep: not supported in asmpython")


async def gather(aw0: object = None, aw1: object = None, aw2: object = None,
                 return_exceptions: int = 0) -> list:
    raise NotImplementedError("asyncio.gather: not supported in asmpython")


async def wait(aws: list, timeout: float = 0.0,
               return_when: str = ALL_COMPLETED) -> list:
    raise NotImplementedError("asyncio.wait: not supported in asmpython")


async def wait_for(aw: object, timeout: float) -> object:
    raise NotImplementedError("asyncio.wait_for: not supported in asmpython")


async def shield(aw: object) -> object:
    raise NotImplementedError("asyncio.shield: not supported in asmpython")


def ensure_future(coro_or_future: object, loop: object = None) -> Future:
    raise NotImplementedError("asyncio.ensure_future: not supported in asmpython")


def create_task(coro: object, name: str = "") -> Task:
    raise NotImplementedError("asyncio.create_task: not supported in asmpython")


def current_task(loop: object = None) -> Task:
    return None


def all_tasks(loop: object = None) -> set:
    return set()


# ---------------------------------------------------------------------------
# Transport / Protocol stubs
# ---------------------------------------------------------------------------

class BaseTransport:
    def close(self) -> None:
        pass

    def is_closing(self) -> int:
        return 1

    def get_extra_info(self, name: str, default: object = None) -> object:
        return default

    def set_protocol(self, protocol: object) -> None:
        pass

    def get_protocol(self) -> object:
        return None


class ReadTransport(BaseTransport):
    def is_reading(self) -> int:
        return 0

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass


class WriteTransport(BaseTransport):
    def write(self, data: str) -> None:
        pass

    def writelines(self, list_of_data: list) -> None:
        pass

    def write_eof(self) -> None:
        pass

    def can_write_eof(self) -> int:
        return 0

    def abort(self) -> None:
        pass

    def get_write_buffer_size(self) -> int:
        return 0


class Transport(ReadTransport, WriteTransport):
    pass


class BaseProtocol:
    def connection_made(self, transport: BaseTransport) -> None:
        pass

    def connection_lost(self, exc: object) -> None:
        pass

    def pause_writing(self) -> None:
        pass

    def resume_writing(self) -> None:
        pass


class Protocol(BaseProtocol):
    def data_received(self, data: str) -> None:
        pass

    def eof_received(self) -> int:
        return 0


class DatagramProtocol(BaseProtocol):
    def datagram_received(self, data: str, addr: list) -> None:
        pass

    def error_received(self, exc: object) -> None:
        pass


# ---------------------------------------------------------------------------
# StreamReader / StreamWriter stubs
# ---------------------------------------------------------------------------

class StreamReader:
    def __init__(self, limit: int = 65536) -> None:
        self._limit: int = limit
        self._buf: str = ""
        self._eof: int = 0

    async def read(self, n: int = -1) -> str:
        raise NotImplementedError("asyncio.StreamReader: not supported in asmpython")

    async def readline(self) -> str:
        raise NotImplementedError("asyncio.StreamReader: not supported in asmpython")

    async def readexactly(self, n: int) -> str:
        raise NotImplementedError("asyncio.StreamReader: not supported in asmpython")

    async def readuntil(self, separator: str = "\n") -> str:
        raise NotImplementedError("asyncio.StreamReader: not supported in asmpython")

    def at_eof(self) -> int:
        return self._eof

    def feed_data(self, data: str) -> None:
        self._buf = self._buf + data

    def feed_eof(self) -> None:
        self._eof = 1


class StreamWriter:
    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def write(self, data: str) -> None:
        self._transport.write(data)

    def writelines(self, data: list) -> None:
        for item in data:
            self._transport.write(item)

    def write_eof(self) -> None:
        self._transport.write_eof()

    def can_write_eof(self) -> int:
        return self._transport.can_write_eof()

    def close(self) -> None:
        self._transport.close()

    def is_closing(self) -> int:
        return self._transport.is_closing()

    async def drain(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass

    def get_extra_info(self, name: str, default: object = None) -> object:
        return self._transport.get_extra_info(name, default)


async def open_connection(host: str = "", port: int = 0,
                          limit: int = 65536, ssl: object = None) -> list:
    raise NotImplementedError("asyncio.open_connection: not supported in asmpython")


async def start_server(client_connected_cb: int, host: str = "",
                       port: int = 0, limit: int = 65536,
                       ssl: object = None) -> object:
    raise NotImplementedError("asyncio.start_server: not supported in asmpython")


# ---------------------------------------------------------------------------
# TimeoutHandle (used by wait_for)
# ---------------------------------------------------------------------------

class TimerHandle:
    def __init__(self, when: float, callback: int) -> None:
        self._when: float = when
        self._cancelled: int = 0

    def cancel(self) -> None:
        self._cancelled = 1

    def cancelled(self) -> int:
        return self._cancelled

    def when(self) -> float:
        return self._when
