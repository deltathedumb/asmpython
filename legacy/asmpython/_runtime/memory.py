"""Shared ownership, cycle collection, and deterministic teardown runtime.

This Python implementation is the executable reference model for the native
runtime ABI. PyinBin uses it directly; native backends and PortaPy can expose the
same ownership states and operations over their own heap representation.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Iterator


class Ownership(str, Enum):
    OWNED = "owned"
    BORROWED = "borrowed"
    TRANSFERRED = "transferred"
    PINNED = "pinned"
    HOST = "host-owned"
    RUNTIME = "runtime-owned"


class MemoryState(str, Enum):
    LIVE = "live"
    FINALIZING = "finalizing"
    FREED = "freed"


class MemoryErrorBase(RuntimeError):
    pass


class InvalidHandleError(MemoryErrorBase):
    pass


class DoubleReleaseError(MemoryErrorBase):
    pass


class FinalizerError(MemoryErrorBase):
    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} memory finalizer(s) failed")


TraceFunction = Callable[[object], Iterable[object]]
FinalizerFunction = Callable[[object], None]


@dataclass
class MemoryRecord:
    identifier: int
    value: object
    ownership: Ownership
    external_refs: int
    pins: int = 0
    edges: set[int] = field(default_factory=set)
    trace: TraceFunction | None = None
    finalizer: FinalizerFunction | None = None
    size: int = 0
    state: MemoryState = MemoryState.LIVE
    order: int = 0
    label: str = ""


@dataclass(frozen=True)
class MemoryStats:
    live_objects: int
    live_bytes: int
    external_references: int
    pinned_objects: int
    collections: int
    collected_objects: int
    finalized_objects: int
    finalizer_failures: int


class Handle:
    """Strong ownership token for a managed value."""

    def __init__(
        self,
        manager: "MemoryManager",
        identifier: int,
        ownership: Ownership,
        *,
        owns_reference: bool,
    ) -> None:
        self._manager_ref = weakref.ref(manager)
        self.identifier = identifier
        self.ownership = ownership
        self._owns_reference = owns_reference
        self._released = False

    @property
    def value(self) -> object:
        manager = self._manager_ref()
        if manager is None or self._released:
            raise InvalidHandleError("memory handle is no longer valid")
        return manager.value(self.identifier)

    @property
    def released(self) -> bool:
        return self._released

    def retain(self, ownership: Ownership = Ownership.OWNED) -> "Handle":
        manager = self._manager_ref()
        if manager is None or self._released:
            raise InvalidHandleError("cannot retain a released handle")
        return manager.retain(self.identifier, ownership=ownership)

    def borrow(self) -> "Handle":
        manager = self._manager_ref()
        if manager is None or self._released:
            raise InvalidHandleError("cannot borrow a released handle")
        return Handle(manager, self.identifier, Ownership.BORROWED, owns_reference=False)

    def weak(self) -> "WeakHandle":
        manager = self._manager_ref()
        if manager is None or self._released:
            raise InvalidHandleError("cannot weaken a released handle")
        return WeakHandle(manager, self.identifier)

    def transfer(self) -> "Handle":
        manager = self._manager_ref()
        if manager is None or self._released:
            raise InvalidHandleError("cannot transfer a released handle")
        if not self._owns_reference:
            raise InvalidHandleError("borrowed handles cannot transfer ownership")
        self._released = True
        self._owns_reference = False
        return Handle(manager, self.identifier, Ownership.TRANSFERRED, owns_reference=True)

    def release(self) -> None:
        if self._released:
            raise DoubleReleaseError("memory handle was released twice")
        manager = self._manager_ref()
        self._released = True
        if manager is not None and self._owns_reference:
            manager.release(self.identifier)
        self._owns_reference = False

    def __enter__(self) -> "Handle":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._released:
            self.release()


class WeakHandle:
    """Non-owning handle that becomes empty after collection."""

    def __init__(self, manager: "MemoryManager", identifier: int) -> None:
        self._manager_ref = weakref.ref(manager)
        self.identifier = identifier

    @property
    def value(self) -> object | None:
        manager = self._manager_ref()
        if manager is None:
            return None
        try:
            return manager.value(self.identifier)
        except InvalidHandleError:
            return None

    @property
    def alive(self) -> bool:
        return self.value is not None

    def lock(self) -> Handle | None:
        manager = self._manager_ref()
        if manager is None or not manager.contains(self.identifier):
            return None
        return manager.retain(self.identifier)


class MemoryManager:
    """Thread-safe explicit heap ownership and tracing-cycle collector."""

    def __init__(self, name: str = "runtime") -> None:
        self.name = name
        self._lock = threading.RLock()
        self._records: dict[int, MemoryRecord] = {}
        self._python_ids: dict[int, int] = {}
        self._next_identifier = 1
        self._next_order = 1
        self._closed = False
        self._collections = 0
        self._collected = 0
        self._finalized = 0
        self._finalizer_failures = 0

    def _ensure_open(self) -> None:
        if self._closed:
            raise MemoryErrorBase(f"memory manager {self.name!r} is closed")

    def contains(self, identifier: int) -> bool:
        with self._lock:
            record = self._records.get(identifier)
            return record is not None and record.state is MemoryState.LIVE

    def identifier_for(self, value: object) -> int | None:
        with self._lock:
            identifier = self._python_ids.get(id(value))
            if identifier is None:
                return None
            record = self._records.get(identifier)
            if record is None or record.value is not value:
                return None
            return identifier

    def track(
        self,
        value: object,
        *,
        ownership: Ownership = Ownership.OWNED,
        root: bool | None = None,
        trace: TraceFunction | None = None,
        finalizer: FinalizerFunction | None = None,
        size: int = 0,
        label: str | None = None,
    ) -> Handle:
        with self._lock:
            self._ensure_open()
            existing = self.identifier_for(value)
            owns = root if root is not None else ownership in {
                Ownership.OWNED, Ownership.TRANSFERRED, Ownership.PINNED, Ownership.HOST,
            }
            if existing is not None:
                record = self._records[existing]
                if trace is not None:
                    record.trace = trace
                if finalizer is not None:
                    record.finalizer = finalizer
                if owns:
                    record.external_refs += 1
                if ownership is Ownership.PINNED:
                    record.pins += 1
                return Handle(self, existing, ownership, owns_reference=bool(owns))

            identifier = self._next_identifier
            self._next_identifier += 1
            external_refs = 1 if owns else 0
            pins = 1 if ownership is Ownership.PINNED else 0
            record = MemoryRecord(
                identifier=identifier,
                value=value,
                ownership=ownership,
                external_refs=external_refs,
                pins=pins,
                trace=trace,
                finalizer=finalizer,
                size=max(0, int(size)),
                order=self._next_order,
                label=label or type(value).__name__,
            )
            self._next_order += 1
            self._records[identifier] = record
            self._python_ids[id(value)] = identifier
            return Handle(self, identifier, ownership, owns_reference=bool(owns))

    def track_internal(
        self,
        value: object,
        *,
        trace: TraceFunction | None = None,
        finalizer: FinalizerFunction | None = None,
        size: int = 0,
        label: str | None = None,
    ) -> int:
        handle = self.track(
            value,
            ownership=Ownership.RUNTIME,
            root=False,
            trace=trace,
            finalizer=finalizer,
            size=size,
            label=label,
        )
        return handle.identifier

    def value(self, identifier: int) -> object:
        with self._lock:
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"invalid or freed memory handle {identifier}")
            return record.value

    def retain(self, identifier: int, *, ownership: Ownership = Ownership.OWNED) -> Handle:
        with self._lock:
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"cannot retain invalid handle {identifier}")
            record.external_refs += 1
            if ownership is Ownership.PINNED:
                record.pins += 1
            return Handle(self, identifier, ownership, owns_reference=True)

    def release(self, identifier: int) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"cannot release invalid handle {identifier}")
            if record.external_refs <= 0:
                raise DoubleReleaseError(f"handle {identifier} has no external reference to release")
            record.external_refs -= 1

    def pin(self, identifier: int) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"cannot pin invalid handle {identifier}")
            record.pins += 1

    def unpin(self, identifier: int) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"cannot unpin invalid handle {identifier}")
            if record.pins <= 0:
                raise DoubleReleaseError(f"handle {identifier} is not pinned")
            record.pins -= 1

    def add_edge(self, owner: int, child: int) -> None:
        with self._lock:
            if not self.contains(owner) or not self.contains(child):
                raise InvalidHandleError("cannot connect an invalid managed object")
            self._records[owner].edges.add(child)

    def remove_edge(self, owner: int, child: int) -> None:
        with self._lock:
            if self.contains(owner):
                self._records[owner].edges.discard(child)

    def replace_edges(self, owner: int, children: Iterable[object | int]) -> None:
        with self._lock:
            record = self._records.get(owner)
            if record is None or record.state is not MemoryState.LIVE:
                raise InvalidHandleError(f"invalid owner handle {owner}")
            resolved: set[int] = set()
            for child in children:
                identifier = child if isinstance(child, int) else self.identifier_for(child)
                if identifier is not None and identifier != owner and self.contains(identifier):
                    resolved.add(identifier)
            record.edges = resolved

    def refresh_edges(self) -> None:
        with self._lock:
            snapshots = [record for record in self._records.values() if record.state is MemoryState.LIVE and record.trace]
        for record in snapshots:
            try:
                children = list(record.trace(record.value)) if record.trace is not None else []
            except BaseException:
                # A tracer is diagnostic runtime machinery and must not make
                # ordinary object collection fail. Keep its last known edges.
                continue
            self.replace_edges(record.identifier, children)

    def _reachable(self) -> set[int]:
        roots = [
            record.identifier for record in self._records.values()
            if record.state is MemoryState.LIVE
            and (record.external_refs > 0 or record.pins > 0 or record.ownership is Ownership.HOST)
        ]
        reachable: set[int] = set()
        pending = list(roots)
        while pending:
            identifier = pending.pop()
            if identifier in reachable:
                continue
            record = self._records.get(identifier)
            if record is None or record.state is not MemoryState.LIVE:
                continue
            reachable.add(identifier)
            pending.extend(record.edges - reachable)
        return reachable

    def collect_cycles(self) -> int:
        self.refresh_edges()
        with self._lock:
            self._ensure_open()
            reachable = self._reachable()
            candidates = [
                record.identifier for record in self._records.values()
                if record.state is MemoryState.LIVE and record.identifier not in reachable
            ]
            self._collections += 1
        self._finalize_identifiers(candidates)
        with self._lock:
            self._collected += len(candidates)
        return len(candidates)

    def _finalization_order(self, identifiers: Iterable[int]) -> list[int]:
        selected = set(identifiers)
        visited: set[int] = set()
        visiting: set[int] = set()
        ordered: list[int] = []

        def visit(identifier: int) -> None:
            if identifier in visited:
                return
            if identifier in visiting:
                return
            visiting.add(identifier)
            record = self._records.get(identifier)
            if record is not None:
                for child in record.edges:
                    if child in selected:
                        visit(child)
            visiting.discard(identifier)
            visited.add(identifier)
            ordered.append(identifier)

        for identifier in sorted(selected, key=lambda item: self._records[item].order, reverse=True):
            visit(identifier)
        return ordered

    def _finalize_identifiers(self, identifiers: Iterable[int]) -> None:
        errors: list[BaseException] = []
        with self._lock:
            ordered = self._finalization_order(identifiers)
            records = []
            for identifier in ordered:
                record = self._records.get(identifier)
                if record is None or record.state is not MemoryState.LIVE:
                    continue
                record.state = MemoryState.FINALIZING
                records.append(record)
        for record in records:
            if record.finalizer is not None:
                try:
                    record.finalizer(record.value)
                except BaseException as exc:
                    errors.append(exc)
            with self._lock:
                record.state = MemoryState.FREED
                self._records.pop(record.identifier, None)
                self._python_ids.pop(id(record.value), None)
                self._finalized += 1
                for owner in self._records.values():
                    owner.edges.discard(record.identifier)
        if errors:
            with self._lock:
                self._finalizer_failures += len(errors)
            raise FinalizerError(errors)

    def teardown(self) -> None:
        with self._lock:
            if self._closed:
                return
            identifiers = [record.identifier for record in self._records.values() if record.state is MemoryState.LIVE]
        try:
            self._finalize_identifiers(identifiers)
        finally:
            with self._lock:
                self._closed = True
                self._records.clear()
                self._python_ids.clear()

    def stats(self) -> MemoryStats:
        with self._lock:
            live = [record for record in self._records.values() if record.state is MemoryState.LIVE]
            return MemoryStats(
                live_objects=len(live),
                live_bytes=sum(record.size for record in live),
                external_references=sum(record.external_refs for record in live),
                pinned_objects=sum(record.pins > 0 for record in live),
                collections=self._collections,
                collected_objects=self._collected,
                finalized_objects=self._finalized,
                finalizer_failures=self._finalizer_failures,
            )

    def __enter__(self) -> "MemoryManager":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.teardown()


_current_manager: contextvars.ContextVar[MemoryManager | None] = contextvars.ContextVar(
    "asmpython_memory_manager", default=None
)
_global_manager = MemoryManager("global")


def current_manager() -> MemoryManager:
    return _current_manager.get() or _global_manager


@contextlib.contextmanager
def memory_domain(name: str = "runtime") -> Iterator[MemoryManager]:
    manager = MemoryManager(name)
    token = _current_manager.set(manager)
    try:
        yield manager
    finally:
        try:
            manager.collect_cycles()
        finally:
            try:
                manager.teardown()
            finally:
                _current_manager.reset(token)


def retain(value: object, *, ownership: Ownership = Ownership.OWNED) -> Handle:
    manager = current_manager()
    identifier = manager.identifier_for(value)
    if identifier is None:
        return manager.track(value, ownership=ownership)
    return manager.retain(identifier, ownership=ownership)


def borrow(value: object) -> Handle:
    manager = current_manager()
    identifier = manager.identifier_for(value)
    if identifier is None:
        return manager.track(value, ownership=Ownership.BORROWED, root=False)
    return Handle(manager, identifier, Ownership.BORROWED, owns_reference=False)


__all__ = [
    "DoubleReleaseError", "FinalizerError", "Handle", "InvalidHandleError",
    "MemoryManager", "MemoryStats", "MemoryState", "Ownership", "WeakHandle",
    "borrow", "current_manager", "memory_domain", "retain",
]
