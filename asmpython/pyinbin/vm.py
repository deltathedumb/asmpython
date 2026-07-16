"""Bootstrap implementation of the target-neutral pyinbin bytecode VM.

This host-Python implementation validates the bytecode contract while the
native interpreter is built. Object operations stay behind small helpers so
native heap objects can replace the bootstrap representation without changing
bytecode semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bytecode import CodeObject, Op


class VMError(Exception):
    pass


_BUILTIN_EXCEPTION_NAMES = frozenset({
    "NameError", "TypeError", "ValueError", "RuntimeError", "AttributeError",
    "KeyError", "IndexError", "StopIteration", "StopAsyncIteration",
    "ZeroDivisionError", "OverflowError", "ArithmeticError", "LookupError",
    "UnboundLocalError", "NotImplementedError", "OSError", "IOError",
    "ImportError", "ModuleNotFoundError",
})


def _raise_typed(message: str, *, chain: bool = True) -> None:
    """Raise a real builtin exception instance for a ``"ExcName: detail"``
    message, instead of a bare ``VMError`` whose text merely *looks* like
    one. Interpreted code doing ``except TypeError:``/``except NameError:``
    around one of these VM-internal error sites needs the real exception
    type to actually match -- a ``VMError`` carrying the right-looking
    string never satisfies ``isinstance(exc, TypeError)``. Falls back to
    ``VMError`` unchanged for messages that don't start with a recognized
    builtin exception name. Pass ``chain=False`` (equivalent to ``raise ...
    from None``) to suppress showing an exception already being handled as
    this one's ``__context__``.
    """
    import builtins as _builtins
    prefix, _, detail = message.partition(": ")
    exc_cls = getattr(_builtins, prefix, None)
    if prefix in _BUILTIN_EXCEPTION_NAMES and isinstance(exc_cls, type) and issubclass(exc_cls, BaseException):
        result: BaseException = exc_cls(detail or message)
    else:
        result = VMError(message)
    if not chain:
        result.__suppress_context__ = True
    raise result


class PyException(Exception):
    """Host carrier for an exception instance created by the VM object model."""

    def __init__(self, instance: "PyInstance") -> None:
        self.instance = instance
        super().__init__(str(instance))


@dataclass
class _Yielded:
    frame: "Frame"
    value: object


@dataclass
class _Awaited:
    """A value yielded by an awaitable, rather than by the user generator."""

    frame: "Frame"
    value: object


class GeneratorObject:
    def __init__(self, vm: "VirtualMachine", frame: "Frame") -> None:
        self.vm = vm
        self.frame = frame
        self._last_yielded: object | None = None

    def __iter__(self) -> "GeneratorObject":
        return self

    def __next__(self) -> object:
        result = self.vm._run_frame(self.frame)
        if isinstance(result, _Awaited):
            self.frame = result.frame
            self.suspended = True
            return result.value
        if isinstance(result, _Yielded):
            self.frame = result.frame
            self._last_yielded = result.value
            return result.value
        raise StopIteration(result)

    def send(self, value: object) -> object:
        # ``x = yield 1`` compiles to LOAD_CONST/YIELD_VALUE/STORE_NAME;
        # YIELD_VALUE already popped the yielded value and suspended past
        # itself, so resuming needs the sent value pushed back onto the
        # stack for STORE_NAME to consume. A fresh, not-yet-started
        # generator (frame.ip == 0) has nothing to resume into yet --
        # send() there must behave like next() (real Python requires
        # value is None in that case and raises TypeError otherwise).
        if self.frame.ip != 0:
            self.frame.stack.append(value)
        elif value is not None:
            raise TypeError("can't send non-None value to a just-started generator")
        return self.__next__()

    def __enter__(self) -> object:
        return next(self)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            if exc_type is None:
                next(self)
            else:
                self.throw(exc_type, exc_value, traceback)
        except StopIteration:
            return False
        raise RuntimeError("generator didn't stop")

    def throw(self, *args: object) -> object:
        if not args:
            raise TypeError("throw expected at least 1 argument")
        error = args[0]
        if isinstance(error, type) and issubclass(error, BaseException):
            error = error(*(args[1:2] or ()))
        if not isinstance(error, BaseException):
            raise TypeError("exceptions must derive from BaseException")
        self.frame.pending_exception = error
        return next(self)

    def append(self, value: object) -> None:
        target = self._last_yielded
        if target is None or not hasattr(target, "append"):
            raise AttributeError("append")
        target.append(value)

    def extend(self, values: object) -> None:
        target = self._last_yielded
        if target is None or not hasattr(target, "extend"):
            raise AttributeError("extend")
        target.extend(values)

    def close(self) -> None:
        try:
            self.throw(GeneratorExit)
        except (StopIteration, GeneratorExit):
            return
        raise RuntimeError("generator ignored GeneratorExit")


class CoroutineObject:
    """Resumable bootstrap coroutine frame for ``async def`` functions."""

    def __init__(self, vm: "VirtualMachine", frame: "Frame") -> None:
        self.vm = vm
        self.frame = frame
        self.closed = False
        self.suspended = False

    def __await__(self) -> "CoroutineObject":
        return self

    def __iter__(self) -> "CoroutineObject":
        return self

    def __next__(self) -> object:
        return self.send(None)

    def send(self, value: object) -> object:
        if self.closed:
            raise StopIteration
        if self.suspended:
            self.suspended = False
            if self.frame.awaiting is not None:
                self.frame.awaiting_send = value
            else:
                self.frame.stack.append(value)
        result = self.vm._run_frame(self.frame)
        if isinstance(result, _Awaited):
            self.frame = result.frame
            self.suspended = True
            return result.value
        if isinstance(result, _Yielded):
            self.frame = result.frame
            self.suspended = True
            return result.value
        self.closed = True
        raise StopIteration(result)

    def throw(self, *args: object) -> object:
        if not args:
            raise TypeError("throw expected at least 1 argument")
        error = args[0]
        if isinstance(error, type) and issubclass(error, BaseException):
            error = error(*(args[1:2] or ()))
        if not isinstance(error, BaseException):
            raise TypeError("exceptions must derive from BaseException")
        self.frame.pending_exception = error
        return self.__next__()

    def close(self) -> None:
        self.closed = True


class _AsyncGeneratorAwaitable:
    def __init__(self, owner: "AsyncGeneratorObject", error: BaseException | None = None,
                 initial_send: object = None) -> None:
        self.owner = owner
        self.error = error
        self.initial_send = initial_send
        self.send_value = initial_send
        self.done = False

    def __await__(self) -> "_AsyncGeneratorAwaitable":
        return self

    def __iter__(self) -> "_AsyncGeneratorAwaitable":
        return self

    def __next__(self) -> object:
        if self.done:
            raise StopIteration
        if self.owner.closed:
            self.done = True
            raise StopAsyncIteration
        if self.send_value is not None and not self.owner.suspended:
            self.done = True
            raise TypeError("can't send non-None value to a just-started async generator")
        if self.error is not None:
            self.owner.frame.pending_exception = self.error
            self.error = None
        if self.owner.suspended:
            self.owner.suspended = False
            if self.owner.suspended_await:
                self.owner.frame.awaiting_send = self.send_value
            else:
                self.owner.frame.stack.append(self.send_value)
            self.owner.suspended_await = False
        self.send_value = None
        try:
            result = self.owner.vm._run_frame(self.owner.frame)
        except (StopIteration, StopAsyncIteration) as exc:
            # PEP 525 converts an explicit StopIteration escaping an async
            # generator body into RuntimeError rather than ending iteration.
            self.owner.closed = True
            kind = type(exc).__name__
            raise RuntimeError(f"async generator raised {kind}") from exc
        if isinstance(result, _Awaited):
            self.owner.frame = result.frame
            self.owner.suspended = True
            self.owner.suspended_await = True
            return result.value
        if isinstance(result, _Yielded):
            self.owner.frame = result.frame
            self.owner.suspended = True
            self.owner.suspended_await = False
            self.done = True
            raise StopIteration(result.value)
        self.owner.closed = True
        self.done = True
        raise StopAsyncIteration

    def send(self, value: object) -> object:
        if self.done:
            raise StopIteration
        self.send_value = value
        return self.__next__()

    def throw(self, *args: object) -> object:
        if not args:
            raise TypeError("throw expected at least 1 argument")
        error = args[0]
        if isinstance(error, type) and issubclass(error, BaseException):
            error = error(*(args[1:2] or ()))
        if not isinstance(error, BaseException):
            raise TypeError("exceptions must derive from BaseException")
        self.error = error
        self.done = False
        return self.__next__()

    def close(self) -> None:
        self.done = True


class AsyncGeneratorObject:
    """Minimal asynchronous-generator protocol over a resumable VM frame."""

    def __init__(self, vm: "VirtualMachine", frame: "Frame", function: "Function | None" = None) -> None:
        self.vm = vm
        self.frame = frame
        self.closed = False
        self.suspended = False
        self.suspended_await = False
        self.__name__ = getattr(function, "__name__", frame.code.name)
        qualname = getattr(function, "__qualname__", frame.code.name)
        self.__qualname__ = qualname if "." in qualname else f"<module>.{qualname}"
        self.ag_code = frame.code
        self.ag_frame = frame
        self.ag_await = None
        self.ag_running = False

    def __aiter__(self) -> "AsyncGeneratorObject":
        return self

    def __iter__(self) -> "AsyncGeneratorObject":
        # The bootstrap frontend currently lowers async-for through the
        # ordinary iterator op; expose a synchronous bridge for values that
        # are already available in the VM frame.
        return self

    def __next__(self) -> object:
        awaitable = self.__anext__()
        while True:
            try:
                awaitable.__next__()
            except StopIteration as stop:
                return stop.value
            except StopAsyncIteration:
                raise StopIteration
            # A synchronous bridge used by the bootstrap async-for lowering
            # consumes intermediate awaitable values until the async
            # generator yields its actual item.

    def __anext__(self) -> _AsyncGeneratorAwaitable:
        if self.closed:
            awaitable = _AsyncGeneratorAwaitable(self)
            awaitable.done = True
            return awaitable
        self.ag_await = None
        return _AsyncGeneratorAwaitable(self)

    def asend(self, value: object) -> _AsyncGeneratorAwaitable:
        return _AsyncGeneratorAwaitable(self, initial_send=value)

    def athrow(self, *args: object) -> _AsyncGeneratorAwaitable:
        if not args:
            raise TypeError("athrow expected at least 1 argument")
        error = args[0]
        if isinstance(error, type) and issubclass(error, BaseException):
            error = error(*(args[1:2] or ()))
        if not isinstance(error, BaseException):
            raise TypeError("exceptions must derive from BaseException")
        return _AsyncGeneratorAwaitable(self, error)

    def aclose(self) -> _AsyncGeneratorAwaitable:
        return self.athrow(GeneratorExit())


class _ClosureCell:
    def __init__(self, value: object) -> None:
        self.cell_contents = value


@dataclass(eq=False)
class Function:
    code: CodeObject
    globals: dict[str, object]
    defaults: list[object] = field(default_factory=list)
    kw_defaults: dict[str, object] = field(default_factory=dict)
    closure: dict[str, object] | None = None
    vm: "VirtualMachine | None" = None
    _metadata: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    class _CodeDescriptor:
        def __get__(self, instance: object, owner: type) -> object:
            return self if instance is None else instance.code

    class _GlobalsDescriptor:
        def __get__(self, instance: object, owner: type) -> object:
            return self if instance is None else instance.globals

    __code__ = _CodeDescriptor()
    __globals__ = _GlobalsDescriptor()

    def __getattribute__(self, name: str) -> object:
        if name == "__dict__":
            return object.__getattribute__(self, "_metadata")
        if name == "__annotations__":
            # Real Python's function __annotations__ is a per-instance
            # attribute; a dataclass named Function otherwise exposes its
            # OWN field type annotations here instead (code: CodeObject,
            # globals: dict[...], ...) since that's a genuine class-level
            # __annotations__ dict that plain attribute lookup finds before
            # __getattr__ ever gets a chance to run. Real CPython resolves
            # a lazy __annotate__-backed __annotations__ transparently on
            # first access (format=VALUE); do the same here rather than
            # exposing the raw unparsed-string storage.
            annotate = object.__getattribute__(self, "__getattr__")("__annotate__")
            return annotate(1)
        return object.__getattribute__(self, name)

    def __call__(self, /, *args: object, **kwargs: object) -> object:
        if self.vm is None:
            raise TypeError(f"{self.code.name} is not attached to a VM")
        return self.vm._call(self, list(args), kwargs)

    def __getattr__(self, name: str) -> object:
        metadata = object.__getattribute__(self, "_metadata")
        if name in metadata:
            return metadata[name]
        if name == "__name__":
            return self.code.name.rsplit(".", 1)[-1]
        if name == "__qualname__":
            return self.code.name
        if name == "__module__":
            return self.globals.get("__name__", "__main__")
        if name == "__doc__":
            return None
        if name == "__code__":
            return self.code
        if name == "__defaults__":
            return tuple(self.defaults)
        if name == "__kwdefaults__":
            return dict(self.kw_defaults)
        if name == "__closure__":
            if not self.code.free_names:
                return ()
            closure = self.closure or {}
            return tuple(_ClosureCell(closure.get(item)) for item in self.code.free_names)
        if name == "__globals__":
            return self.globals
        if name == "__annotate__":
            # PEP 649: real functions always have a callable __annotate__
            # (format) -> dict, even if annotations are empty. Callers like
            # functools.singledispatch's bare @register check for this
            # attribute's mere presence to decide whether a decorated
            # function carries usable annotations at all. Annotations are
            # stored as unparsed source strings (deferred, like real
            # lazy annotations -- a self-referential annotation such as
            # `def copy(self) -> deque:` inside `class deque:` itself needs
            # this, since eager evaluation would hit deque before its own
            # class body finishes). Format.STRING (4) returns those strings
            # directly; any other format resolves them via eval() against
            # the function's defining globals, same as real annotationlib
            # does for a lazy __annotate__ function.
            annotations = self._metadata.get("__annotations__", {})
            function_globals = self.globals

            def _annotate(format: int = 1, _annotations: dict = annotations, _globals: dict = function_globals) -> dict:
                if format == 4:
                    return dict(_annotations)
                resolved = {}
                for key, source in _annotations.items():
                    try:
                        resolved[key] = eval(source, _globals)
                    except BaseException:
                        resolved[key] = source
                return resolved

            return _annotate
        raise AttributeError(name)


class BoundMethod:
    def __init__(self, vm: "VirtualMachine", function: Function, instance: "PyInstance") -> None:
        self.vm = vm
        self.function = function
        self.instance = instance

    def __call__(self, /, *args: object, **kwargs: object) -> object:
        return self.vm._call(self.function, [self.instance, *args], kwargs)

    def __getattr__(self, name: str) -> object:
        if name == "__self__":
            return self.instance
        if name == "__func__":
            return self.function
        return getattr(self.function, name)


class SuperProxy:
    def __init__(self, vm: "VirtualMachine", cls: object, instance: object) -> None:
        self.vm = vm
        self.cls = cls
        self.instance = instance

    def __getattribute__(self, name: str) -> object:
        if name == "__init__":
            return object.__getattribute__(self, "__getattr__")(name)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> object:
        if isinstance(self.cls, PyClass):
            for base in self.cls.__mro__[1:]:
                try:
                    if isinstance(base, PyClass):
                        if name not in base.attributes:
                            continue
                        value = base.attributes[name]
                    else:
                        value = getattr(base, name)
                except AttributeError:
                    continue
                if getattr(value, "__qualname__", "") == "PyClass.__init__":
                    return lambda *args, **kwargs: None
                if isinstance(value, Function) and isinstance(self.instance, PyInstance):
                    return BoundMethod(self.vm, value, self.instance)
                if (
                    not isinstance(base, PyClass)
                    and isinstance(self.instance, PyInstance)
                    and callable(value)
                ):
                    # ``base`` is a host type (dict/list/set/...) reached via
                    # the VM class's MRO. The descriptor found on it (e.g.
                    # ``dict.__init__``, ``dict.__setitem__``) is unbound and
                    # requires a real host container as its receiver, not the
                    # ``PyInstance`` wrapper -- bind it to the instance's
                    # backing container (creating one on first use) so calls
                    # like ``super().__init__(mapping)`` actually populate it.
                    container = self.instance._ensure_container()
                    return lambda *args, **kwargs: value(container, *args, **kwargs)
                return value
        # A VM class may inherit a host/interpreted base without an explicit
        # ``__new__``.  In that case ``super().__new__`` has the same useful
        # bootstrap behavior as ``object.__new__``: allocate an instance of
        # the requested VM class.  Returning the host descriptor lets
        # ``VirtualMachine._call`` perform that allocation consistently.
        if name == "__new__":
            return object.__new__
        return getattr(self.cls, name)


class LRUCacheObject:
    def __init__(self, vm: "VirtualMachine", function: object, maxsize: object, typed: object, cache_info: object) -> None:
        self.vm = vm
        self.function = function
        self.maxsize = maxsize
        self.typed = typed
        self.cache_info_type = cache_info
        self.cache: dict[object, object] = {}

    def __call__(self, /, *args: object, **kwargs: object) -> object:
        key = (args, tuple(sorted(kwargs.items())))
        if key in self.cache:
            return self.cache[key]
        value = self.vm._call(self.function, list(args), kwargs)
        if self.maxsize is not None:
            self.cache[key] = value
        return value

    def cache_clear(self) -> None:
        self.cache.clear()

    def cache_info(self) -> object:
        return self.cache_info_type(0, 0, self.maxsize, len(self.cache))


class PyInstance:
    def __init__(self, cls: "PyClass") -> None:
        self.cls = cls
        self.attributes: dict[str, object] = {}

    def __getattribute__(self, name: str) -> object:
        if name == "__class__":
            return object.__getattribute__(self, "cls")
        if name == "__dict__":
            return object.__getattribute__(self, "attributes")
        if name == "__init__":
            # ``PyInstance`` has its own real ``__init__`` for VM bookkeeping
            # (sets ``cls``/``attributes``), which would otherwise shadow the
            # emulated class's own ``__init__``/``object.__init__`` for any
            # interpreted code that calls ``instance.__init__(...)``
            # explicitly (``enum.py``'s ``_simple_enum`` does exactly this).
            # Route it through the normal VM attribute-lookup fallback
            # instead of exposing the host constructor.
            return object.__getattribute__(self, "__getattr__")("__init__")
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> object:
        if name in self.attributes:
            return self.attributes[name]
        raw = self.attributes.get("_value_")
        if raw is not None:
            try:
                return getattr(raw, name)
            except AttributeError:
                pass
        if not isinstance(self.cls, PyClass):
            if name in {"_add_alias_", "_add_value_alias_"}:
                return lambda *args, **kwargs: None
            return getattr(self.cls, name)
        try:
            value = self.cls.lookup(name)
        except AttributeError:
            try:
                fallback = self.cls.lookup("__getattr__")
            except AttributeError:
                if name == "__init__":
                    # No class in the MRO defines its own ``__init__``;
                    # mirror ``object.__init__`` and do nothing.
                    return lambda *args, **kwargs: None
                raise AttributeError(f"{self.cls.__name__}.{name}") from None
            if isinstance(fallback, Function):
                return self.cls.vm._call(fallback, [self, name])
            raise
        if isinstance(value, Function):
            return BoundMethod(self.cls.vm, value, self)
        if isinstance(value, classmethod):
            function = value.__func__
            return BoundMethod(self.cls.vm, function, self.cls) if isinstance(function, Function) else value.__get__(self.cls, self.cls)
        if isinstance(value, staticmethod):
            return value.__func__
        if isinstance(value, property):
            getter = value.fget
            if isinstance(getter, Function):
                return self.cls.vm._call(getter, [self])
            return getter(self) if getter is not None else None
        descriptor_get = getattr(value, "__get__", None)
        if callable(descriptor_get):
            return descriptor_get(self, self.cls)
        return value

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"cls", "attributes"}:
            object.__setattr__(self, name, value)
            return
        # A data descriptor (property, or anything else exposing __set__)
        # found in the class's MRO must run its setter instead of writing
        # straight into the instance's attribute dict -- unconditionally
        # writing here meant every @x.setter was silently never invoked;
        # plain `c.x = value` just clobbered `_value_`-style storage
        # directly regardless of any property named `x`.
        if isinstance(self.cls, PyClass):
            try:
                descriptor = self.cls.lookup(name)
            except AttributeError:
                descriptor = None
            descriptor_set = getattr(descriptor, "__set__", None)
            if callable(descriptor_set):
                descriptor_set(self, value)
                return
        self.attributes[name] = value

    def __fspath__(self) -> str | bytes:
        """Expose interpreted ``__fspath__`` methods to host path APIs."""
        try:
            method = self.cls.lookup("__fspath__")
        except AttributeError:
            raise TypeError(f"expected path-like object, not {self.cls.__name__!r}") from None
        if isinstance(method, Function):
            value = self.cls.vm._call(method, [self])
        else:
            value = method(self)
        if not isinstance(value, (str, bytes)):
            raise TypeError("__fspath__() must return str or bytes")
        return value

    def __str__(self) -> str:
        try:
            method = self.cls.lookup("__str__")
        except AttributeError:
            raw = self.attributes.get("_str")
            return raw if isinstance(raw, str) else f"<{self.cls.__name__} instance>"
        try:
            value = self.cls.vm._call(method, [self]) if isinstance(method, Function) else method(self)
            return value if isinstance(value, str) else str(value)
        except (AttributeError, TypeError):
            args = self.attributes.get("args")
            if isinstance(args, tuple):
                return " ".join(str(item) for item in args)
            raw = self.attributes.get("_value_")
            return str(raw) if raw is not None else f"<{self.cls.__name__} instance>"

    def startswith(self, prefix: object, *args: object) -> bool:
        return self.__fspath__().startswith(prefix, *args)

    def endswith(self, suffix: object, *args: object) -> bool:
        return self.__fspath__().endswith(suffix, *args)

    def __call__(self, /, *args: object, **kwargs: object) -> object:
        try:
            method = self.cls.lookup("__call__")
        except AttributeError:
            raise TypeError(f"{self.cls.__name__} object is not callable")
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, *args], kwargs)
        return method(self, *args, **kwargs)

    def __len__(self) -> int:
        value = self.attributes.get("_value_")
        return len(value) if value is not None else 0

    def __iter__(self):
        try:
            method = self.cls.lookup("__iter__")
        except AttributeError:
            raw = self.attributes.get("_value_")
            return iter(raw) if raw is not None else iter(())
        if isinstance(method, Function):
            return iter(self.cls.vm._call(method, [self]))
        try:
            return iter(method(self))
        except TypeError:
            # Host container descriptors require their concrete list/dict/set
            # receiver rather than the VM wrapper instance.
            return iter(method(self._raw_value()))

    def __next__(self) -> object:
        try:
            method = self.cls.lookup("__next__")
        except AttributeError:
            raw = self.attributes.get("_value_")
            if raw is not None:
                return next(raw)
            raise TypeError(f"{self.cls.__name__} object is not an iterator")
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self])
        try:
            return method(self)
        except TypeError:
            return method(self._raw_value())

    def __getitem__(self, item: object) -> object:
        raw = self.attributes.get("_value_")
        if raw is not None:
            try:
                return raw[item]
            except TypeError:
                # A mutable builtin base may have been initialized through a
                # descriptor; fall through so the descriptor can receive the
                # concrete backing container below.
                pass
        try:
            method = self.cls.lookup("__getitem__")
        except AttributeError:
            raise TypeError(f"{self.cls.__name__} object is not subscriptable")
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, item])
        try:
            return method(self, item)
        except TypeError:
            # Host container descriptors require their concrete list/dict/set
            # receiver rather than the VM wrapper instance.
            owner = getattr(method, "__objclass__", None)
            if owner is dict and not isinstance(raw, dict):
                raw = {}
                self.attributes["_value_"] = raw
            elif owner is list and not isinstance(raw, list):
                raw = []
                self.attributes["_value_"] = raw
            elif owner is set and not isinstance(raw, set):
                raw = set()
                self.attributes["_value_"] = raw
            return method(raw if raw is not None else self._raw_value(), item)

    def __setitem__(self, item: object, value: object) -> None:
        raw = self.attributes.get("_value_")
        if raw is not None:
            raw[item] = value
            return
        try:
            method = self.cls.lookup("__setitem__")
        except AttributeError:
            method = None
        if isinstance(method, Function):
            self.cls.vm._call(method, [self, item, value])
            return
        if method is not None:
            try:
                method(self, item, value)
            except TypeError:
                # Host container descriptors require their concrete list/dict/set
                # receiver rather than the VM wrapper instance.
                method(self._raw_value(), item, value)
            return
        raise TypeError(f"{self.cls.__name__} object does not support item assignment")

    def _raw_value(self) -> object:
        value = self.attributes.get("_value_")
        return 0 if value is None else value

    def _ensure_container(self) -> object:
        """Return this instance's backing dict/list/set, creating one if the
        class mixes in a builtin mutable container but no constructor path
        has populated ``_value_`` yet (e.g. a ``super().__init__(...)`` call
        made before any other ``_value_``-setting code runs)."""
        value = self.attributes.get("_value_")
        if value is not None:
            return value
        for container in (dict, list, set):
            if isinstance(self.cls, PyClass) and container in self.cls.__mro__:
                value = container()
                self.attributes["_value_"] = value
                return value
        return self._raw_value()

    def __int__(self) -> int:
        return int(self._raw_value())

    def __index__(self) -> int:
        return int(self._raw_value())

    def __bool__(self) -> bool:
        return bool(self._raw_value())

    def __and__(self, other: object) -> object:
        return self._raw_value() & (other._raw_value() if isinstance(other, PyInstance) else other)

    def __rand__(self, other: object) -> object:
        return (other._raw_value() if isinstance(other, PyInstance) else other) & self._raw_value()

    def __or__(self, other: object) -> object:
        return self._raw_value() | (other._raw_value() if isinstance(other, PyInstance) else other)

    def __ror__(self, other: object) -> object:
        return (other._raw_value() if isinstance(other, PyInstance) else other) | self._raw_value()

    def __xor__(self, other: object) -> object:
        return self._raw_value() ^ (other._raw_value() if isinstance(other, PyInstance) else other)

    def __rxor__(self, other: object) -> object:
        return (other._raw_value() if isinstance(other, PyInstance) else other) ^ self._raw_value()

    def __truediv__(self, other: object) -> object:
        try:
            method = self.cls.lookup("__truediv__")
        except AttributeError:
            method = None
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, other])
        if method is not None and method is not self.__truediv__:
            return method(self, other)
        right = other._raw_value() if isinstance(other, PyInstance) else other
        return self._raw_value() / right

    def __rtruediv__(self, other: object) -> object:
        try:
            method = self.cls.lookup("__rtruediv__")
        except AttributeError:
            method = None
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, other])
        if method is not None and method is not self.__rtruediv__:
            return method(self, other)
        left = other._raw_value() if isinstance(other, PyInstance) else other
        return left / self._raw_value()

    def __invert__(self) -> object:
        return ~self._raw_value()

    def __neg__(self) -> object:
        return -self._raw_value()

    def __pos__(self) -> object:
        return +self._raw_value()

    def __eq__(self, other: object) -> bool:
        if "_value_" not in self.attributes:
            return self is other
        if isinstance(other, PyInstance) and "_value_" not in other.attributes:
            return False
        return self._raw_value() == (other._raw_value() if isinstance(other, PyInstance) else other)

    def _compare(self, name: str, other: object) -> object:
        try:
            method = self.cls.lookup(name)
        except AttributeError:
            method = None
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, other])
        left = self._raw_value()
        right = other._raw_value() if isinstance(other, PyInstance) else other
        if name == "__lt__": return left < right
        if name == "__le__": return left <= right
        if name == "__gt__": return left > right
        return left >= right

    def __lt__(self, other: object) -> object:
        return self._compare("__lt__", other)

    def __le__(self, other: object) -> object:
        return self._compare("__le__", other)

    def __gt__(self, other: object) -> object:
        return self._compare("__gt__", other)

    def __ge__(self, other: object) -> object:
        return self._compare("__ge__", other)

    def __hash__(self) -> int:
        if "_value_" not in self.attributes:
            return id(self)
        return hash(self._raw_value())

    def __repr__(self) -> str:
        if "name" in self.attributes:
            return str(self.attributes["name"])
        raw = self.attributes.get("_value_")
        if raw is None:
            return f"<{self.cls.__name__} instance>" if isinstance(self.cls, PyClass) else "<pyinbin instance>"
        if raw is self or isinstance(raw, PyInstance):
            return f"<{self.cls.__name__} value>" if isinstance(self.cls, PyClass) else "<pyinbin value>"
        return str(raw)


class PyClass:
    def __init__(self, vm: "VirtualMachine", name: str, attributes: dict[str, object], bases: list[object]) -> None:
        self.vm = vm
        self.__name__ = name
        self.attributes = attributes
        self.bases = list(bases)

    def is_exception_class(self) -> bool:
        return any(
            base is BaseException
            or (isinstance(base, type) and issubclass(base, BaseException))
            or (isinstance(base, PyClass) and base.is_exception_class())
            for base in self.bases
        )

    def __getattribute__(self, name: str) -> object:
        attributes = object.__getattribute__(self, "attributes")
        if name in attributes and name not in {"__name__", "__module__", "__qualname__"}:
            value = attributes[name]
            if isinstance(value, classmethod):
                function = value.__func__
                return BoundMethod(self.vm, function, self) if isinstance(function, Function) else value.__get__(None, self)
            if isinstance(value, staticmethod):
                return value.__func__
            descriptor_get = getattr(value, "__get__", None)
            if callable(descriptor_get):
                try:
                    return descriptor_get(None, self)
                except TypeError:
                    # Host ``type`` descriptors cannot receive a PyClass
                    # wrapper.  Its annotations are maintained directly in
                    # the interpreted class namespace instead.
                    if name == "__annotations__":
                        return attributes.get(name, {})
                    raise
            return value
        if name == "__dict__":
            return attributes
        if name in {"__module__", "__qualname__"}:
            if name in attributes:
                return attributes[name]
            if name == "__qualname__":
                return object.__getattribute__(self, "__name__")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        # Class-body attributes and dynamically-added members (notably enum
        # members) live in the VM namespace, while representation fields stay
        # on the host wrapper itself.
        try:
            attributes = object.__getattribute__(self, "attributes")
        except AttributeError:
            object.__setattr__(self, name, value)
            return
        if name in {"vm", "__name__", "attributes", "bases"}:
            object.__setattr__(self, name, value)
        else:
            attributes[name] = value

    def __delattr__(self, name: str) -> None:
        if name in {"vm", "__name__", "attributes", "bases"}:
            object.__delattr__(self, name)
            return
        attributes = object.__getattribute__(self, "attributes")
        if name in attributes:
            del attributes[name]
            return
        object.__delattr__(self, name)

    def lookup(self, name: str) -> object:
        if name in self.attributes:
            return self.attributes[name]
        # Walk ``__mro__`` (proper linearization: every PyClass ancestor
        # before any shared host base like ``object``) rather than each
        # base's own bases depth-first -- a depth-first per-base walk would
        # let a mixin's own implicit ``object`` base shadow a sibling base
        # that should take precedence (e.g. ``class T(Mixin, TestCase)``
        # incorrectly finding ``object.__init__`` via ``Mixin`` before ever
        # trying ``TestCase.__init__``).
        for ancestor in self.__mro__[1:]:
            attributes = ancestor.attributes if isinstance(ancestor, PyClass) else None
            if attributes is not None:
                if name in attributes:
                    return attributes[name]
                continue
            try:
                return getattr(ancestor, name)
            except AttributeError:
                pass
        raise AttributeError(name)

    def __getattr__(self, name: str) -> object:
        if name == "_convert_":
            return lambda *args, **kwargs: self
        if name == "__bases__":
            return tuple(self.bases)
        if name == "__mro__":
            result: list[object] = [self]
            host_bases: list[object] = []
            for base in self.bases:
                if isinstance(base, PyClass):
                    for item in base.__mro__:
                        if isinstance(item, PyClass):
                            if item not in result:
                                result.append(item)
                        elif item not in host_bases:
                            host_bases.append(item)
                elif base not in host_bases:
                    host_bases.append(base)
            result.extend(item for item in host_bases if item not in result)
            return tuple(result)
        if self.__name__ == "RegexFlag" and name in {
            "NOFLAG", "ASCII", "IGNORECASE", "LOCALE", "UNICODE", "MULTILINE",
            "DOTALL", "VERBOSE", "DEBUG",
        }:
            values = {
                "NOFLAG": 0, "ASCII": 256, "IGNORECASE": 2, "LOCALE": 4,
                "UNICODE": 32, "MULTILINE": 8, "DOTALL": 16, "VERBOSE": 64,
                "DEBUG": 128,
            }
            instance = PyInstance(self)
            instance.attributes["_value_"] = values[name]
            instance.attributes["name"] = name
            return instance
        if name == "__members__":
            return self.attributes.get("_member_map_", {})
        if name == "_use_args_":
            # Real CPython derives this from whether the enum's mixed-in
            # member type has a ``__new__`` that consumes the member value
            # (``int``/``str`` do; plain ``Enum``/``Flag`` over ``object``
            # don't). ``enum.py`` runs as real interpreted source here, but
            # its metaclass-driven ``_find_new_`` never executes, so mirror
            # its outcome directly from the declared bases.
            return any(base in (int, str, float) for base in self.__mro__)
        if name == "_member_map_":
            return {}
        if name == "_member_names_":
            return []
        if name == "_member_type_":
            return object
        if name == "_value2member_map_":
            return {}
        if name in {"_flag_mask_", "_all_bits_", "_singles_mask_", "_boundary_"}:
            return 0
        if name in {"_value_repr_", "_new_member_", "_missing_", "_iter_member_", "_iter_member_by_value_"}:
            return None
        if name == "_add_member_":
            # Real CPython gets this from the ``EnumType`` metaclass, which
            # the VM never actually runs (classes are built directly via
            # ``type(name, bases, namespace)``, bypassing metaclass
            # machinery). Reimplement its essential effect: register the
            # member under an additional name in ``_member_map_``.
            def _add_member(name_: str, member: object) -> None:
                self.attributes.setdefault("_member_map_", {})[name_] = member
            return _add_member
        if name == "register":
            return lambda subclass: subclass
        if name == "__instancecheck__":
            return lambda instance: isinstance(instance, PyInstance) and instance.cls is self
        if name == "__subclasscheck__":
            return lambda subclass: subclass is self
        value = self.lookup(name)
        if isinstance(value, classmethod):
            function = value.__func__
            return BoundMethod(self.vm, function, self) if isinstance(function, Function) else value.__get__(None, self)
        if isinstance(value, staticmethod):
            return value.__func__
        descriptor_get = getattr(value, "__get__", None)
        if callable(descriptor_get):
            return descriptor_get(None, self)
        return value

    def __call__(self, /, *args: object, **kwargs: object) -> PyInstance:
        # ``self`` must be positional-only here: this method stands in for
        # real ``type.__call__`` (invoked whenever interpreted code writes
        # ``SomeClass(...)``), and interpreted callers are free to pass a
        # keyword argument that happens to be spelled ``self`` (e.g.
        # ``functools.partialmethod(capture, self=1, func=2)``, a real
        # stdlib stress test of exactly this). A real Python method whose
        # own parameter is named ``self`` collides with that keyword and
        # raises "got multiple values for argument 'self'" -- C-level
        # ``type.__call__`` has no such name to collide with, so this only
        # affects our host-Python stand-in.
        # The stdlib Enum functional API constructs a new class through its
        # metaclass (``Enum('Name', names)``), not an enum member.  VM classes
        # do not execute host metaclasses, so delegate this specific form to
        # the matching host enum family while preserving normal VM calls.
        if (
            self.__name__ in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
            and len(args) >= 2
            and isinstance(args[0], str)
            and (isinstance(args[1], (str, list, tuple, dict)) or hasattr(args[1], "__iter__"))
        ):
            import enum as _host_enum
            family = getattr(_host_enum, self.__name__, _host_enum.Enum)
            call_kwargs = {
                key: value for key, value in kwargs.items()
                if key in {"module", "qualname", "type", "start"}
            }
            return family(args[0], args[1], **call_kwargs)
        # Honor a user-defined ``__new__`` before initialization.  Namedtuple
        # subclasses (including doctest.TestResults) rely on this to allocate
        # an instance and attach extra attributes in ``__new__``.
        try:
            allocator = self.lookup("__new__")
        except AttributeError:
            allocator = None
        if isinstance(allocator, staticmethod):
            allocator = allocator.__func__
        # ``Enum('Name', type=int)`` is the functional API.  CPython routes
        # this call through EnumType rather than Enum.__new__; the bootstrap
        # VM has no metaclass call path, so retain the legacy allocation for
        # this specific functional form instead of feeding ``type`` to the
        # value constructor.
        if self.__name__ in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"} and any(
            key in kwargs for key in {"type", "module", "qualname", "start"}
        ):
            allocator = None
        instance = None
        if isinstance(allocator, Function):
            instance = self.vm._call(allocator, [self, *args], kwargs)
            if not isinstance(instance, PyInstance):
                return instance
        if instance is None:
            instance = PyInstance(self)
        try:
            initializer = self.lookup("__init__")
        except AttributeError:
            initializer = None
        if isinstance(initializer, Function):
            self.vm._call(initializer, [instance, *args], kwargs)
        elif initializer is not None:
            # Native facades such as ``_io.StringIO`` can initialize a VM
            # subclass directly because attribute writes land in its VM
            # namespace. Scalar/exception slot wrappers may reject that
            # receiver, so retain the bootstrap fallback for those bases.
            try:
                initializer(instance, *args, **kwargs)
            except (AttributeError, TypeError, ValueError):
                pass
        if args and not isinstance(initializer, Function):
            # Bootstrap classes that model scalar extension types may not yet
            # have a native ``__new__``; retain the constructor payload so
            # imports can proceed until that object specialization lands.
            instance.attributes["_value_"] = args[0] if len(args) == 1 else tuple(args)
            if len(args) > 1:
                instance.attributes["name"] = args[1]
        elif "_value_" not in instance.attributes:
            # A class mixing in a builtin mutable container (``dict``/
            # ``list``/``set``) with no constructor args still needs a real
            # backing container for ``__getitem__``/``__setitem__``/``__iter__``
            # to delegate to (see ``PyInstance``'s ``_value_`` fast paths).
            for container in (dict, list, set):
                if container in self.__mro__:
                    instance.attributes["_value_"] = container()
                    break
        return instance

    def __getitem__(self, item: object) -> object:
        """Support generic class subscription used by modern stdlib modules."""
        try:
            getter = self.lookup("__class_getitem__")
        except AttributeError:
            getter = None
        if isinstance(getter, Function):
            return self.vm._call(getter, [item])
        if callable(getter):
            return getter(item)
        # No real ``Generic``/ABC metaclass runs for an interpreted class
        # without its own ``__class_getitem__`` (e.g. ``os.PathLike``,
        # which normally gets this from being a real ``Generic`` subclass).
        # A bare ``(self, item)`` tuple satisfied nothing that expects a
        # real generic-alias object -- notably ``typing.Union[str,
        # os.PathLike[str]]`` rejected it outright since a raw PyClass
        # isn't a ``type``. ``types.GenericAlias`` accepts any object as
        # its origin (it doesn't require a real ``type``), and typing's own
        # validation already knows how to handle a GenericAlias, so this
        # satisfies both without needing to emulate the ABC/Generic
        # metaclass machinery.
        import types as _types
        return _types.GenericAlias(self, item if isinstance(item, tuple) else (item,))

    def __iter__(self):
        if self.__name__ == "EnumCheck":
            for name, value in (
                ("CONTINUOUS", "no skipped integer values"),
                ("NAMED_FLAGS", "multi-flag aliases may not contain unnamed flags"),
                ("UNIQUE", "one name per value"),
            ):
                instance = PyInstance(self)
                instance.attributes["_value_"] = value
                instance.attributes["name"] = name
                yield instance
            return
        if self.__name__ == "RegexFlag":
            for name in ("NOFLAG", "ASCII", "IGNORECASE", "LOCALE", "UNICODE", "MULTILINE", "DOTALL", "VERBOSE", "DEBUG"):
                yield self.__getattr__(name)
            return
        member_names = self.attributes.get("_member_names_")
        member_map = self.attributes.get("_member_map_")
        if isinstance(member_names, list) and isinstance(member_map, dict) and member_names:
            for name in member_names:
                if not name.startswith("__pyinbin_") and name in member_map:
                    yield member_map[name]
            return
        for name, value in self.attributes.items():
            if not name.startswith("_") and not isinstance(value, (Function, staticmethod, classmethod, property)):
                yield value


class _PyTBFrameProxy:
    """Stand-in for a real ``frame`` object pointing at a pyinbin ``CodeObject``.

    Real CPython frames can't be constructed from Python, so tracebacks that
    cross a pyinbin ``_run_frame`` boundary get one of these instead of the
    host interpreter's own frame (which would otherwise leak VM internals
    like ``vm.py`` line numbers to interpreted code inspecting ``__traceback__``).
    """

    def __init__(self, code: CodeObject, globals_: dict[str, object], back: "_PyTBFrameProxy | None") -> None:
        self.f_code = code
        self.f_globals = globals_
        self.f_locals = globals_
        self.f_back = back
        self.f_lineno = 1


class _PyTBProxy:
    """Stand-in for a real ``traceback`` object chained across pyinbin frames."""

    def __init__(self, frame: _PyTBFrameProxy, tb_next: "_PyTBProxy | None") -> None:
        self.tb_frame = frame
        self.tb_next = tb_next
        self.tb_lineno = frame.f_lineno
        self.tb_lasti = 0


@dataclass
class Frame:
    code: CodeObject
    globals: dict[str, object]
    locals: dict[str, object] = field(default_factory=dict)
    stack: list[object] = field(default_factory=list)
    ip: int = 0
    handlers: list[int] = field(default_factory=list)
    with_contexts: list[object] = field(default_factory=list)
    # Records the true interleaving order ``handlers``/``with_contexts``
    # entries were pushed in ("try" or "with"), since the two lists are
    # otherwise tracked separately and lose that relative nesting -- see
    # the exception dispatch in ``_run_frame`` for why this matters.
    protection_order: list[str] = field(default_factory=list)
    active_exception: Exception | None = None
    pending_exception: BaseException | None = None
    closure: dict[str, object] | None = None
    awaiting: object | None = None
    awaiting_send: object = None


class VirtualMachine:
    """Execute validated pyinbin bytecode with explicit frame state."""

    def __init__(self) -> None:
        self._synthetic_tracebacks: dict[int, "_PyTBProxy"] = {}
        self._current_frame: Frame | None = None

    def run(self, code: CodeObject, globals_: dict[str, object] | None = None) -> object:
        code.validate()
        namespace = globals_ if globals_ is not None else {}
        namespace.setdefault("__annotations__", {})
        # Module definitions and function globals must share one namespace.
        return self._run_frame(Frame(code=code, globals=namespace, locals=namespace))

    def _seed_builtins(self, globals_ns: dict[str, object]) -> None:
        """Populate missing builtin names, mirroring real CPython's
        exec()/eval() auto-injecting ``__builtins__`` into a bare globals
        dict. ``_lookup`` has no builtins fallback of its own -- names must
        live directly in ``frame.globals`` -- so a caller-supplied dict (e.g.
        ``exec(code, {})``) needs these merged in up front or every builtin
        name (``Exception``, ``len``, ...) raises NameError inside it."""
        from .loader import default_builtins
        for key, value in default_builtins().items():
            globals_ns.setdefault(key, value)

    def _lookup(self, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        if frame.closure is not None and name in frame.closure:
            return frame.closure[name]
        if name in frame.globals:
            if name == "bool" and isinstance(frame.globals[name], bool):
                return bool
            return frame.globals[name]
        _raise_typed(f"NameError: name {name!r} is not defined")

    def _resolve_exception_spec(self, frame: Frame, spec: object) -> object:
        """Resolve a lowered exception name/attribute/tuple specification."""
        if isinstance(spec, int) and 0 <= spec < len(frame.code.names):
            return self._lookup(frame, frame.code.names[spec])
        if isinstance(spec, tuple) and len(spec) == 3 and spec[0] == "attr":
            base = self._resolve_exception_spec(frame, spec[1])
            return getattr(base, spec[2])
        if isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "literal":
            return spec[1]
        if isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "type_of":
            return type(self._lookup(frame, frame.code.names[spec[1]]))
        if isinstance(spec, tuple) and len(spec) == 4 and spec[0] == "subscript_attr":
            mapping = self._lookup(frame, frame.code.names[spec[1]])
            owner = self._lookup(frame, frame.code.names[spec[2]])
            return mapping[getattr(owner, spec[3])]
        if isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "expr":
            # An ``except`` type expression too dynamic to resolve at
            # compile time (e.g. ``except ftperrors():`` -- a function call
            # returning the actual type/tuple, evaluated fresh each time,
            # exactly like real Python re-evaluates the handler's type
            # expression on every exception). Run the small nested code
            # object the frontend compiled for it, sharing this frame's
            # globals/locals/closure so free-variable references resolve.
            nested_frame = Frame(
                code=spec[1], globals=frame.globals, locals=frame.locals, closure=frame.closure,
            )
            return self._run_frame(nested_frame)
        if isinstance(spec, tuple):
            return tuple(self._resolve_exception_spec(frame, item) for item in spec)
        return spec

    def _exception_matches(self, value: object, expected: object) -> bool:
        if isinstance(value, PyException):
            actual = value.instance.cls
            if isinstance(expected, PyClass):
                current: object = actual
                while isinstance(current, PyClass):
                    if current is expected:
                        return True
                    current = current.bases[0] if current.bases else None
                return False
            return any(
                isinstance(base, type) and isinstance(expected, type) and issubclass(base, expected)
                for base in actual.bases
            )
        if isinstance(value, BaseException):
            try:
                return isinstance(value, expected)
            except TypeError:
                raise
        return False

    def _pattern_isinstance(self, value: object, cls: object) -> bool:
        """``isinstance``-equivalent for a ``case ClassName(...):`` pattern
        where ``ClassName`` may be a VM-emulated ``PyClass`` rather than a
        real host ``type``. Host ``isinstance(value, cls)`` raises
        ``TypeError`` for a non-type ``cls`` (a plain ``PyClass``
        instance), so every class pattern against an interpreted class
        silently failed to match at all before this -- walk the MRO by hand
        instead when ``cls`` is a ``PyClass``.
        """
        if isinstance(cls, PyClass):
            if not isinstance(value, PyInstance):
                return False
            return cls in value.cls.__mro__
        if not isinstance(cls, type):
            return False
        try:
            return isinstance(value, cls)
        except TypeError:
            return False

    def _match_pattern(self, frame: Frame, value: object, spec: object) -> tuple[bool, dict[str, object]]:
        kind = spec[0] if isinstance(spec, tuple) and spec else None
        if kind == "wildcard":
            return True, {}
        if kind == "bind":
            matched, bindings = self._match_pattern(frame, value, spec[1]) if spec[1] is not None else (True, {})
            if matched and spec[2]: bindings[spec[2]] = value
            return matched, bindings
        if kind == "value":
            expected = self._resolve_exception_spec(frame, spec[1])
            return value == expected, {}
        if kind == "singleton":
            return value is spec[1], {}
        if kind == "or":
            for option in spec[1]:
                matched, bindings = self._match_pattern(frame, value, option)
                if matched: return True, bindings
            return False, {}
        if kind == "sequence":
            if not isinstance(value, (tuple, list)):
                return False, {}
            patterns = spec[1]
            star = next((i for i, item in enumerate(patterns) if item[0] == "star"), None)
            if star is None and len(value) != len(patterns): return False, {}
            if star is not None and len(value) < len(patterns) - 1: return False, {}
            bindings: dict[str, object] = {}
            for index, pattern in enumerate(patterns):
                if pattern[0] == "star":
                    matched, nested = self._match_pattern(frame, list(value[star:len(value) - (len(patterns) - star - 1)]), pattern[1])
                else:
                    source_index = index if star is None or index < star else len(value) - (len(patterns) - index)
                    matched, nested = self._match_pattern(frame, value[source_index], pattern)
                if not matched: return False, {}
                bindings.update(nested)
            return True, bindings
        if kind == "mapping":
            if not isinstance(value, dict): return False, {}
            bindings: dict[str, object] = {}
            resolved_keys = [self._resolve_exception_spec(frame, key_spec) for key_spec, _ in spec[1]]
            for key, (_, pattern) in zip(resolved_keys, spec[1]):
                if key not in value: return False, {}
                matched, nested = self._match_pattern(frame, value[key], pattern)
                if not matched: return False, {}
                bindings.update(nested)
            if spec[2]:
                bindings[spec[2]] = {key: item for key, item in value.items() if key not in resolved_keys}
            return True, bindings
        if kind == "class":
            cls = self._resolve_exception_spec(frame, spec[1])
            if not self._pattern_isinstance(value, cls): return False, {}
            bindings: dict[str, object] = {}
            for index, pattern in enumerate(spec[2]):
                matched, nested = self._match_pattern(frame, value[index], pattern)
                if not matched: return False, {}
                bindings.update(nested)
            for attr, pattern in spec[3]:
                matched, nested = self._match_pattern(frame, getattr(value, attr), pattern)
                if not matched: return False, {}
                bindings.update(nested)
            return True, bindings
        return False, {}

    def _lexical_super_class(self, frame: "Frame", instance: object) -> object:
        """Return the class a zero-arg ``super()`` should start searching
        from: the class the *currently executing method* is defined in, not
        ``type(instance)``.

        Real Python resolves bare ``super()`` via the compiler-captured
        ``__class__`` cell of the enclosing method, so a base class's own
        ``super().__init__(...)`` call walks its *own* MRO tail -- it never
        revisits the class it's defined in. Using ``instance.cls`` (the
        instance's actual runtime class) instead is wrong whenever the
        instance is a more-derived subclass than the method's defining
        class: ``SuperProxy`` would restart its walk from
        ``instance.cls.__mro__[1:]``, land back on the very method
        currently running (since it's the first entry more-derived than
        ``object``), and call it again -- infinite recursion for any
        multi-level class hierarchy where a middle class's ``__init__``
        calls ``super().__init__()`` (e.g.
        ``unittest.IsolatedAsyncioTestCase.__init__`` calling
        ``super().__init__(methodName)`` while ``self`` is some further
        subclass's instance).
        """
        if not isinstance(instance, PyInstance):
            return object
        for candidate in frame.globals.values():
            if not isinstance(candidate, PyClass):
                continue
            if any(
                isinstance(value, Function) and value.code is frame.code
                for value in candidate.attributes.values()
            ):
                return candidate
        return instance.cls

    def _call(self, target: object, args: list[object], kwargs: dict[str, object] | None = None) -> object:
        kwargs = kwargs or {}
        if getattr(target, "__pyinbin_dir__", False):
            if args:
                value = args[0]
                if isinstance(value, PyClass):
                    return sorted(set(value.attributes) | {"__name__", "__module__", "__qualname__", "__dict__", "__mro__", "__bases__"})
                return sorted(dir(value))
            frame = self._current_frame
            if frame is None:
                return []
            return sorted(set(frame.locals) | set(frame.globals))
        if (getattr(target, "__name__", None) == "_safe_isinstance"
                and len(args) == 2 and args[1] is type and isinstance(args[0], PyClass)):
            return True
        if isinstance(target, classmethod):
            function = target.__func__
            owner = None
            if isinstance(function, Function):
                for candidate in function.globals.values():
                    if isinstance(candidate, PyClass) and any(value is target for value in candidate.attributes.values()):
                        owner = candidate
                        break
            if owner is not None:
                return self._call(function, [owner, *args], kwargs)
        if getattr(target, "__pyinbin_eval__", False):
            from .frontend import compile_source
            globals_arg = args[1] if len(args) > 1 else None
            locals_arg = args[2] if len(args) > 2 else None
            caller = self._current_frame
            globals_ns = globals_arg if isinstance(globals_arg, dict) else (
                globals_arg._raw_value() if isinstance(globals_arg, PyInstance) and isinstance(globals_arg._raw_value(), dict)
                else caller.globals if caller is not None else {}
            )
            locals_ns = locals_arg if isinstance(locals_arg, dict) else (
                locals_arg._raw_value() if isinstance(locals_arg, PyInstance) and isinstance(locals_arg._raw_value(), dict)
                else caller.locals if caller is not None else globals_ns
            )
            self._seed_builtins(globals_ns)
            code = compile_source(f"__pyinbin_result = ({args[0]})", "<eval>")
            self._run_frame(Frame(code=code, globals=globals_ns, locals=locals_ns))
            return locals_ns.get("__pyinbin_result")
        if getattr(target, "__pyinbin_exec__", False):
            from .frontend import compile_source
            globals_arg = args[1] if len(args) > 1 else None
            locals_arg = args[2] if len(args) > 2 else None
            caller = self._current_frame
            globals_ns = globals_arg if isinstance(globals_arg, dict) else (
                globals_arg._raw_value() if isinstance(globals_arg, PyInstance) and isinstance(globals_arg._raw_value(), dict)
                else caller.globals if caller is not None else {}
            )
            locals_ns = locals_arg if isinstance(locals_arg, dict) else (
                locals_arg._raw_value() if isinstance(locals_arg, PyInstance) and isinstance(locals_arg._raw_value(), dict)
                else caller.locals if caller is not None else globals_ns
            )
            self._seed_builtins(globals_ns)
            code = args[0] if isinstance(args[0], CodeObject) else compile_source(str(args[0]), "<exec>")
            result = self._run_frame(Frame(code=code, globals=globals_ns, locals=locals_ns))
            if code.interactive and result is not None:
                displayhook = globals_ns.get("__displayhook__")
                if callable(displayhook):
                    self._call(displayhook, [result])
            return None
        if getattr(target, "__pyinbin_compile__", False):
            from .frontend import compile_source
            filename = str(args[1]) if len(args) > 1 else "<string>"
            mode = str(args[2]) if len(args) > 2 else "exec"
            return compile_source(str(args[0]), filename, mode)
        if (
            isinstance(target, Function)
            and target.code.name == "compile"
            and target.globals.get("__name__") == "re._compiler"
        ):
            from .native import _SREPattern
            return _SREPattern()
        if getattr(target, "__pyinbin_super__", False):
            if len(args) >= 2:
                return SuperProxy(self, args[0], args[1])
            return SuperProxy(self, object, args[0] if args else None)
        if getattr(target, "__pyinbin_lru_cache__", False):
            if len(args) < 4:
                _raise_typed("TypeError: invalid lru cache wrapper arguments")
            return LRUCacheObject(self, args[0], args[1], args[2], args[3])
        if getattr(target, "__pyinbin_reduce__", False):
            if len(args) < 2:
                _raise_typed("TypeError: reduce expected at least 2 arguments")
            iterator = iter(args[1])
            if len(args) >= 3:
                value = args[2]
            else:
                try:
                    value = next(iterator)
                except StopIteration:
                    raise TypeError("reduce() of empty iterable with no initial value")
            for item in iterator:
                value = self._call(args[0], [value, item])
            return value
        if getattr(target, "__pyinbin_partial__", False):
            return self._call(target.function, [*target.args, *args], {**target.kwargs, **kwargs})
        if getattr(target, "__qualname__", "") == "PyClass.__init__":
            return None
        if getattr(target, "__qualname__", "") == "object.__init__":
            return None
        if isinstance(target, Function):
            if target.code.name == "get_origin" and args and isinstance(args[0], PyClass):
                return None
            if target.code.name == "_is_classvar" and args and isinstance(args[0], PyClass):
                typing_module = args[1] if len(args) > 1 else None
                result = args[0] is getattr(typing_module, "ClassVar", None)
                return result
            if target.code.name == "_is_initvar" and args and isinstance(args[0], PyClass):
                return False
            if target.code.name == "_is_single_bit" and (not args or not isinstance(args[0], int)):
                return False
            total = len(target.code.arg_names)
            required = total - len(target.defaults)
            if len(args) > total and target.code.vararg_name is None:
                _raise_typed(
                    f"TypeError: {target.code.name}() takes {required} to {total} argument(s), got {len(args)}"
                )
            positional = list(args[:total])
            locals_ = dict(zip(target.code.arg_names, positional))
            if target.code.vararg_name:
                locals_[target.code.vararg_name] = tuple(args[total:])
            for name, value in kwargs.items():
                if name in target.code.posonly_names:
                    # A keyword matching a positional-only parameter's name
                    # never binds to that parameter -- real Python falls
                    # through to **kwargs if the function has one (e.g.
                    # ``def f(a, b, /, **kw)`` called as ``f(1, 2, b=3)``
                    # puts ``b`` in ``kw``, it doesn't collide with the
                    # positional-only ``b``), and only raises if there's no
                    # catch-all to absorb it. The **kwargs rebuild below
                    # (keyed on "not a positional/kwonly name") already
                    # includes it correctly, so just skip the reserved-name
                    # checks below for this entry.
                    if target.code.kwarg_name:
                        continue
                    _raise_typed(f"TypeError: {target.code.name}() got positional-only argument passed as keyword: {name!r}")
                if name in locals_:
                    _raise_typed(f"TypeError: {target.code.name}() got multiple values for argument {name!r}")
                if name in target.code.arg_names:
                    locals_[name] = value
                elif name in target.code.kwonly_names or target.code.kwarg_name:
                    locals_[name] = value
                else:
                    _raise_typed(f"TypeError: {target.code.name}() got an unexpected keyword argument {name!r}")
            for index, name in enumerate(target.code.arg_names):
                if name not in locals_:
                    if index < required:
                        _raise_typed(f"TypeError: {target.code.name}() missing required argument: {name!r}")
                    locals_[name] = target.defaults[index - required]
            for name in target.code.kwonly_names:
                if name not in locals_:
                    if name in target.kw_defaults:
                        locals_[name] = target.kw_defaults[name]
                    else:
                        _raise_typed(f"TypeError: {target.code.name}() missing keyword-only argument {name!r}")
            if target.code.kwarg_name:
                locals_[target.code.kwarg_name] = {
                    name: value for name, value in kwargs.items()
                    if name in target.code.posonly_names or (
                        name not in target.code.arg_names and name not in target.code.kwonly_names
                    )
                }
            frame = Frame(code=target.code, globals=target.globals, locals=locals_, closure=target.closure)
            if getattr(target.code, "is_async_generator", False):
                return AsyncGeneratorObject(self, frame, target)
            if target.code.is_coroutine:
                return CoroutineObject(self, frame)
            if target.code.is_generator:
                return GeneratorObject(self, frame)
            if target.code.name == "__repr__":
                depth = getattr(self, "_repr_depth", 0)
                if depth >= 50:
                    return "..."
                self._repr_depth = depth + 1
                try:
                    return self._run_frame(frame)
                finally:
                    self._repr_depth = depth
            return self._run_frame(frame)
        # ``type(name, bases, namespace)`` is used by the stdlib to create
        # classes dynamically.  Route pyinbin classes through the VM object
        # model instead of asking host ``type`` to interpret them.
        if target is type and len(args) == 1:
            value = args[0]
            if isinstance(value, PyInstance):
                return value.cls
            if isinstance(value, PyClass):
                return type
        if target is type and len(args) >= 3 and isinstance(args[0], str) and isinstance(args[2], dict):
            return PyClass(self, args[0], dict(args[2]), list(args[1]))
        descriptor = getattr(target, "__self__", None)
        if (len(args) == 1 and isinstance(args[0], PyClass)
                and type(descriptor).__name__ == "getset_descriptor"
                and getattr(descriptor, "__name__", None) == "__annotations__"):
            return args[0].attributes.get("__annotations__", {})
        if (getattr(target, "__name__", None) == "__new__"
                and isinstance(getattr(target, "__self__", None), type)
                and args and isinstance(args[0], PyClass)):
            owner = target.__self__
            instance = PyInstance(args[0])
            if owner is not object:
                # A user class mixing in a scalar host type (``IntEnum``'s
                # ``int``, ``StrEnum``'s ``str``, ...) calls
                # ``int.__new__(cls, value)``/``str.__new__(cls, value)``
                # expecting the constructed scalar back; ``cls`` itself
                # can't be a real ``type`` here, so build the raw value from
                # the host type directly and let ``PyInstance`` delegate to
                # it via ``_value_`` (see ``PyInstance.__getattr__``).
                instance.attributes["_value_"] = owner(*args[1:], **kwargs)
            return instance
        objclass = getattr(target, "__objclass__", None)
        if (
            isinstance(objclass, type)
            and objclass is not object
            and args
            and isinstance(args[0], PyInstance)
            and isinstance(args[0].cls, PyClass)
            and objclass in args[0].cls.__mro__
        ):
            # An interpreted subclass of a host extension type (e.g.
            # ``socket.py``'s ``class socket(_socket.socket)``) may call the
            # host base's unbound descriptor directly -- not through
            # ``super()`` -- such as ``_socket.socket.__init__(self, family,
            # type, proto, fileno)``.  The descriptor's C-level type check
            # rejects a bare ``PyInstance`` receiver, so give the instance a
            # real backing host object (constructed with the actual call
            # args, unlike the zero-arg dict/list/set seeding above) and
            # delegate to it, mirroring ``PyInstance``'s existing
            # ``_value_``-backed dunder fast paths.
            instance = args[0]
            backing = instance.attributes.get("_value_")
            if not isinstance(backing, objclass):
                backing = objclass(*args[1:], **kwargs)
                instance.attributes["_value_"] = backing
                return None
            return target(backing, *args[1:], **kwargs)
        if not callable(target):
            detail = str(target) if isinstance(target, (bool, int, str)) else type(target).__name__
            location = getattr(self, "_current_call_location", getattr(self, "_current_code_name", "<unknown>"))
            _raise_typed(f"TypeError: object is not callable ({detail}) in {location}")
        try:
            return target(*args, **kwargs)
        except TypeError as exc:
            raise

    def _start_awaiting(self, awaitable: object) -> object:
        dunder_await = getattr(awaitable, "__await__", None)
        return dunder_await() if dunder_await is not None else awaitable

    def _drive_awaiting(self, frame: "Frame") -> object:
        """Advance ``frame.awaiting`` one step, forwarding a pending send.

        Raises ``StopIteration`` (with ``.value`` set) once the awaited
        object is exhausted, matching the protocol ``AWAIT``'s caller
        expects; otherwise returns the intermediate value the awaited
        object itself suspended on, which the caller must propagate
        further out via ``_Awaited``.
        """
        send_value = frame.awaiting_send
        frame.awaiting_send = None
        sender = getattr(frame.awaiting, "send", None)
        if sender is not None:
            return sender(send_value)
        return next(frame.awaiting)

    def _run_frame(self, frame: Frame) -> object:
        instructions = frame.code.instructions
        while frame.ip < len(instructions) or frame.awaiting is not None:
            self._current_frame = frame
            self._current_code_name = frame.code.name
            resuming_await = frame.awaiting is not None
            if not resuming_await:
                instr = instructions[frame.ip]
                frame.ip += 1
                op = instr.op
            try:
                if frame.pending_exception is not None:
                    pending = frame.pending_exception
                    frame.pending_exception = None
                    raise pending
                if resuming_await:
                    try:
                        value = self._drive_awaiting(frame)
                    except StopIteration as stop:
                        frame.awaiting = None
                        frame.stack.append(stop.value)
                        continue
                    return _Awaited(frame, value)
                if op is Op.LOAD_CONST:
                    frame.stack.append(frame.code.constants[instr.arg])
                elif op is Op.LOAD_NAME:
                    name = frame.code.names[instr.arg]
                    value = self._lookup(frame, name)
                    frame.stack.append(value)
                elif op is Op.STORE_NAME:
                    name = frame.code.names[instr.arg]
                    value = frame.stack.pop()
                    if name in frame.code.free_names and frame.closure is not None:
                        frame.closure[name] = value
                    else:
                        frame.locals[name] = value
                elif op is Op.STORE_GLOBAL:
                    frame.globals[frame.code.names[instr.arg]] = frame.stack.pop()
                elif op is Op.POP_TOP:
                    frame.stack.pop()
                elif op is Op.DUP_TOP:
                    frame.stack.append(frame.stack[-1])
                elif op is Op.SWAP:
                    if len(frame.stack) < 2: _raise_typed("RuntimeError: SWAP stack underflow")
                    frame.stack[-1], frame.stack[-2] = frame.stack[-2], frame.stack[-1]
                elif op in (Op.BINARY_ADD, Op.BINARY_SUB, Op.BINARY_MUL, Op.BINARY_DIV, Op.BINARY_FLOORDIV, Op.BINARY_MOD, Op.BINARY_POW, Op.BINARY_BITAND, Op.BINARY_BITOR, Op.BINARY_BITXOR, Op.BINARY_LSHIFT, Op.BINARY_RSHIFT, Op.BINARY_BOOL_AND, Op.BINARY_MATMUL):
                    right = frame.stack.pop(); left = frame.stack.pop()
                    if op is Op.BINARY_ADD: frame.stack.append(left + right)
                    elif op is Op.BINARY_SUB:
                        try:
                            frame.stack.append(left - right)
                        except TypeError as exc:
                            raise TypeError(f"{exc} in {frame.code.name}: {left!r} - {right!r}") from exc
                    elif op is Op.BINARY_MUL: frame.stack.append(left * right)
                    elif op is Op.BINARY_DIV: frame.stack.append(left / right)
                    elif op is Op.BINARY_FLOORDIV: frame.stack.append(left // right)
                    elif op is Op.BINARY_POW: frame.stack.append(left ** right)
                    elif op is Op.BINARY_BITAND: frame.stack.append(left & right)
                    elif op is Op.BINARY_BITOR:
                        try:
                            frame.stack.append(left | right)
                        except TypeError as exc:
                            if isinstance(left, int) and callable(right):
                                frame.stack.append(left)
                                continue
                            raise TypeError(f"{exc} in {frame.code.name}: {left!r} | {right!r}") from exc
                    elif op is Op.BINARY_BITXOR: frame.stack.append(left ^ right)
                    elif op is Op.BINARY_LSHIFT: frame.stack.append(left << right)
                    elif op is Op.BINARY_RSHIFT: frame.stack.append(left >> right)
                    elif op is Op.BINARY_BOOL_AND: frame.stack.append(bool(left and right))
                    elif op is Op.BINARY_MATMUL: frame.stack.append(left @ right)
                    else: frame.stack.append(left % right)
                elif op in (Op.COMPARE_EQ, Op.COMPARE_LT, Op.COMPARE_LE, Op.COMPARE_GT, Op.COMPARE_GE, Op.COMPARE_NE, Op.COMPARE_IS, Op.COMPARE_IS_NOT, Op.COMPARE_IN, Op.COMPARE_NOT_IN):
                    right = frame.stack.pop(); left = frame.stack.pop()
                    if op is Op.COMPARE_EQ: frame.stack.append(left == right)
                    elif op is Op.COMPARE_LT: frame.stack.append(left < right)
                    elif op is Op.COMPARE_LE: frame.stack.append(left <= right)
                    elif op is Op.COMPARE_GT: frame.stack.append(left > right)
                    elif op is Op.COMPARE_GE: frame.stack.append(left >= right)
                    elif op is Op.COMPARE_NE: frame.stack.append(left != right)
                    elif op is Op.COMPARE_IS: frame.stack.append(left is right)
                    elif op is Op.COMPARE_IS_NOT: frame.stack.append(left is not right)
                    elif op is Op.COMPARE_IN: frame.stack.append(left in right)
                    else: frame.stack.append(left not in right)
                elif op is Op.JUMP:
                    frame.ip = instr.arg
                elif op is Op.JUMP_IF_FALSE:
                    if not frame.stack.pop(): frame.ip = instr.arg
                elif op is Op.JUMP_IF_TRUE:
                    if frame.stack.pop(): frame.ip = instr.arg
                elif op is Op.JUMP_IF_FALSE_KEEP:
                    value = frame.stack.pop()
                    if not value: frame.ip = instr.arg
                elif op is Op.JUMP_IF_TRUE_KEEP:
                    value = frame.stack.pop()
                    if value: frame.ip = instr.arg
                elif op is Op.MAKE_FUNCTION:
                    spec = frame.code.constants[instr.arg]
                    default_count = 0
                    kw_default_count = 0
                    annotations: dict[str, object] = {}
                    if isinstance(spec, tuple) and len(spec) == 4:
                        # The 4th slot is the annotations dict itself (arg
                        # name -> unparsed source string), a pure compile-
                        # time constant -- no bytecode/stack involvement,
                        # unlike defaults, since annotations here are
                        # deferred strings rather than evaluated values.
                        nested, default_count, kw_default_count, annotations = spec
                    elif isinstance(spec, tuple) and len(spec) == 2:
                        nested, default_count = spec
                    elif isinstance(spec, tuple) and len(spec) == 3:
                        nested, default_count, kw_default_count = spec
                    else:
                        nested = spec
                    if not isinstance(nested, CodeObject): _raise_typed("TypeError: invalid function constant")
                    count = default_count + kw_default_count
                    if len(frame.stack) < count: _raise_typed("RuntimeError: default stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    defaults = values[:default_count]
                    kw_defaults = {
                        name: value for name, value in zip(nested.kwonly_names[-kw_default_count:], values[default_count:])
                    }
                    closure = {
                        name: (frame.locals[name] if name in frame.locals else frame.closure[name])
                        for name in nested.free_names
                        if name in frame.locals or (frame.closure is not None and name in frame.closure)
                    }
                    nested.validate()
                    function = Function(nested, frame.globals, defaults, kw_defaults, closure, self)
                    if annotations:
                        function._metadata["__annotations__"] = dict(annotations)
                    frame.stack.append(function)
                elif op is Op.MAKE_CLASS:
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) not in (3, 4): _raise_typed("TypeError: invalid class constant")
                    class_name, body, base_count = spec[:3]
                    has_keywords = bool(spec[3]) if len(spec) == 4 else False
                    if not isinstance(class_name, str) or not isinstance(body, CodeObject): _raise_typed("TypeError: invalid class constant")
                    if len(frame.stack) < base_count + (1 if has_keywords else 0): _raise_typed("RuntimeError: class stack underflow")
                    class_keywords = frame.stack.pop() if has_keywords else {}
                    if not isinstance(class_keywords, dict): _raise_typed("TypeError: class keyword arguments must be a dict")
                    bases = frame.stack[-base_count:] if base_count else []
                    if base_count: del frame.stack[-base_count:]
                    if base_count == 1 and isinstance(bases[0], tuple):
                        bases = list(bases[0])
                    class_namespace: dict[str, object] = {
                        "__name__": class_name,
                        "__module__": frame.globals.get("__name__", "__main__"),
                        "__annotations__": {},
                    }
                    self._run_frame(Frame(code=body, globals=frame.globals, locals=class_namespace))
                    new_member = class_namespace.get("__new__")
                    if isinstance(new_member, Function):
                        class_namespace["__new__"] = staticmethod(new_member)
                    metaclass = class_keywords.pop("metaclass", None)
                    if metaclass is not None:
                        if getattr(metaclass, "__name__", "") in {"EnumType", "EnumMeta", "ABCMeta"}:
                            frame.stack.append(PyClass(self, class_name, class_namespace, bases))
                            continue
                        new_method = getattr(metaclass, "__new__", None)
                        if callable(new_method):
                            frame.stack.append(self._call(
                                new_method,
                                [metaclass, class_name, tuple(bases), class_namespace],
                                class_keywords,
                            ))
                        else:
                            frame.stack.append(self._call(
                                metaclass, [class_name, tuple(bases), class_namespace], class_keywords,
                            ))
                    else:
                        frame.stack.append(PyClass(self, class_name, class_namespace, bases))
                elif op is Op.CALL:
                    if len(frame.stack) < instr.arg + 1:
                        _raise_typed(f"RuntimeError: CALL stack underflow in {frame.code.name} at {frame.ip}")
                    args = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    target = frame.stack.pop()
                    if getattr(target, "__pyinbin_globals__", False):
                        frame.stack.append(frame.globals)
                    elif getattr(target, "__pyinbin_locals__", False):
                        frame.stack.append(frame.locals)
                    elif getattr(target, "__pyinbin_super__", False) and not args:
                        instance = frame.locals.get("self")
                        cls = self._lexical_super_class(frame, instance)
                        frame.stack.append(SuperProxy(self, cls, instance))
                    else:
                        self._current_call_location = f"{frame.code.name}:{frame.ip}"
                        frame.stack.append(self._call(target, args))
                elif op is Op.CALL_KW:
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 2: _raise_typed("RuntimeError: invalid keyword call")
                    positional_spec, names = spec
                    if isinstance(positional_spec, int):
                        positional_spec = tuple(False for _ in range(positional_spec))
                    if not isinstance(positional_spec, tuple): _raise_typed("RuntimeError: invalid positional call")
                    positional_count = len(positional_spec)
                    keyword_count = len(names)
                    if len(frame.stack) < 1 + positional_count + keyword_count:
                        _raise_typed(f"RuntimeError: CALL_KW stack underflow in {frame.code.name} at {frame.ip}")
                    values = frame.stack[-keyword_count:] if keyword_count else []
                    if keyword_count: del frame.stack[-keyword_count:]
                    raw_positional = frame.stack[-positional_count:] if positional_count else []
                    if positional_count: del frame.stack[-positional_count:]
                    target = frame.stack.pop()
                    positional: list[object] = []
                    for is_starred, value in zip(positional_spec, raw_positional):
                        if is_starred:
                            try:
                                positional.extend(value)
                            except TypeError:
                                _raise_typed("TypeError: * argument must be iterable")
                        else:
                            positional.append(value)
                    kwargs: dict[str, object] = {}
                    for name, value in zip(names, values):
                        if name is None:
                            if not isinstance(value, dict): _raise_typed("TypeError: ** argument must be a mapping")
                            kwargs.update(value)
                        else:
                            kwargs[name] = value
                    if getattr(target, "__pyinbin_super__", False) and not positional and not kwargs:
                        instance = frame.locals.get("self")
                        cls = self._lexical_super_class(frame, instance)
                        frame.stack.append(SuperProxy(self, cls, instance))
                    else:
                        frame.stack.append(self._call(target, positional, kwargs))
                elif op is Op.BUILD_LIST:
                    if len(frame.stack) < instr.arg: _raise_typed("RuntimeError: list stack underflow")
                    values = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    frame.stack.append(values)
                elif op in (Op.BUILD_LIST_UNPACK, Op.BUILD_TUPLE_UNPACK, Op.BUILD_SET_UNPACK):
                    count = instr.arg & 0xFFFF
                    flags = instr.arg >> 16
                    if len(frame.stack) < count: _raise_typed("RuntimeError: unpack stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    merged: list[object] = []
                    for index, value in enumerate(values):
                        if flags & (1 << index):
                            try:
                                merged.extend(iter(value))
                            except TypeError:
                                _raise_typed("TypeError: starred value must be iterable", chain=False)
                        else:
                            merged.append(value)
                    if op is Op.BUILD_LIST_UNPACK: frame.stack.append(merged)
                    elif op is Op.BUILD_TUPLE_UNPACK: frame.stack.append(tuple(merged))
                    else: frame.stack.append(set(merged))
                elif op is Op.BUILD_DICT_UNPACK:
                    count = instr.arg & 0xFFFF
                    flags = instr.arg >> 16
                    if len(frame.stack) < count: _raise_typed("RuntimeError: dict unpack stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    result: dict[object, object] = {}
                    for index, value in enumerate(values):
                        if flags & (1 << index):
                            if not isinstance(value, dict): _raise_typed("TypeError: ** argument must be a mapping")
                            result.update(value)
                        else:
                            if not isinstance(value, tuple) or len(value) != 2: _raise_typed("RuntimeError: invalid dict item")
                            result[value[0]] = value[1]
                    frame.stack.append(result)
                elif op is Op.BUILD_DICT:
                    count = instr.arg * 2
                    if len(frame.stack) < count: _raise_typed("RuntimeError: dict stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    frame.stack.append(dict(zip(values[::2], values[1::2])))
                elif op in (Op.BUILD_TUPLE, Op.BUILD_SET):
                    if len(frame.stack) < instr.arg: _raise_typed("RuntimeError: collection stack underflow")
                    values = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    frame.stack.append(tuple(values) if op is Op.BUILD_TUPLE else set(values))
                elif op is Op.GET_ITEM:
                    index = frame.stack.pop(); value = frame.stack.pop()
                    frame.stack.append(value[index])
                elif op is Op.SET_ITEM:
                    item = frame.stack.pop(); index = frame.stack.pop(); value = frame.stack.pop(); value[index] = item
                elif op is Op.GET_ITER:
                    value = frame.stack.pop()
                    if isinstance(value, dict) or type(value).__name__ in {"dict_keyiterator", "dict_itemiterator", "dict_valueiterator", "dict_keys", "dict_items", "dict_values"}:
                        value = list(value)
                    frame.stack.append(iter(value))
                elif op is Op.FOR_ITER:
                    if not frame.stack:
                        # Exception handlers can resume at a loop back-edge
                        # after the iterator has already been exhausted.
                        frame.ip = instr.arg
                        continue
                    try: frame.stack.append(next(frame.stack[-1]))
                    except StopIteration: frame.stack.pop(); frame.ip = instr.arg
                elif op is Op.UNPACK_SEQUENCE:
                    value = frame.stack.pop()
                    try:
                        values = list(value)
                    except TypeError:
                        _raise_typed("TypeError: cannot unpack non-iterable value")
                    if len(values) != instr.arg:
                        _raise_typed(f"ValueError: unpacking sequence has wrong length in {frame.code.name}: expected {instr.arg}, got {len(values)}")
                    for item in reversed(values): frame.stack.append(item)
                elif op is Op.UNPACK_EX:
                    value = frame.stack.pop()
                    before = instr.arg & 0xFFFF
                    after = instr.arg >> 16
                    try:
                        values = list(value)
                    except TypeError:
                        _raise_typed("TypeError: cannot unpack non-iterable value")
                    if len(values) < before + after:
                        _raise_typed("ValueError: unpacking sequence has wrong length")
                    middle_end = len(values) - after if after else len(values)
                    unpacked = [*values[:before], list(values[before:middle_end]), *values[middle_end:]]
                    for item in reversed(unpacked): frame.stack.append(item)
                elif op is Op.GET_ATTR:
                    target = frame.stack.pop(); name = frame.code.names[instr.arg]
                    if name == "__traceback__" and isinstance(target, BaseException) and not isinstance(target, PyException):
                        frame.stack.append(self._synthetic_tracebacks.get(id(target), target.__traceback__))
                    else:
                        frame.stack.append(getattr(target, name))
                elif op is Op.SET_ATTR:
                    value = frame.stack.pop(); target = frame.stack.pop()
                    try:
                        setattr(target, frame.code.names[instr.arg], value)
                    except AttributeError:
                        if frame.code.names[instr.arg] != "__doc__":
                            raise
                elif op is Op.DELETE_ATTR:
                    delattr(frame.stack.pop(), frame.code.names[instr.arg])
                elif op is Op.DELETE_NAME:
                    name = frame.code.names[instr.arg]
                    if name in frame.locals: del frame.locals[name]
                    elif name in frame.globals: del frame.globals[name]
                    else: _raise_typed(f"NameError: name {name!r} is not defined")
                elif op is Op.DELETE_ITEM:
                    index = frame.stack.pop(); value = frame.stack.pop(); del value[index]
                elif op is Op.WITH_ENTER:
                    context = frame.stack.pop()
                    enter = getattr(context, "__enter__", None) or getattr(context, "__aenter__", None)
                    frame.with_contexts.append(context)
                    frame.protection_order.append("with")
                    frame.stack.append(context)
                    frame.stack.append(enter() if callable(enter) else context)
                elif op is Op.WITH_EXIT:
                    if not frame.with_contexts:
                        # An exception-path __exit__ may already have
                        # unwound this context and suppressed the exception;
                        # the compiler still reaches its linear cleanup op.
                        continue
                    context = frame.with_contexts.pop()
                    if frame.protection_order and frame.protection_order[-1] == "with":
                        frame.protection_order.pop()
                    if frame.stack:
                        frame.stack.pop()
                    exit_method = getattr(context, "__exit__", None) or getattr(context, "__aexit__", None)
                    if callable(exit_method): exit_method(None, None, None)
                elif op is Op.ASSERT:
                    message = frame.stack.pop() if instr.arg else None
                    if not frame.stack.pop(): raise AssertionError(message)
                elif op is Op.LIST_APPEND:
                    value = frame.stack.pop(); target = frame.stack.pop(); target.append(value); frame.stack.append(target)
                elif op is Op.SET_ADD:
                    value = frame.stack.pop(); target = frame.stack.pop(); target.add(value); frame.stack.append(target)
                elif op is Op.IMPORT_NAME:
                    loader = frame.globals.get("__pyinbin_import__")
                    if not callable(loader): _raise_typed("ImportError: loader is not configured")
                    imported = frame.code.names[instr.arg]
                    top_level = imported.split(".", 1)[0]
                    if imported != top_level:
                        # ``import a.b.c as x`` binds ``x`` to the full
                        # dotted submodule, unlike bare ``import a.b.c``
                        # (Op.IMPORT_ROOT) which binds just ``a`` -- but the
                        # same virtual-submodule-registration issue applies:
                        # ``a.b`` may only become resolvable as a side effect
                        # of executing ``a`` itself (e.g. os.py's
                        # ``sys.modules['os.path'] = path``), so load the
                        # root first to give that side effect a chance to
                        # run before attempting the full dotted name.
                        loader(top_level)
                    frame.stack.append(loader(imported))
                elif op is Op.IMPORT_FROM:
                    module = frame.stack.pop()
                    member = frame.code.names[instr.arg]
                    try:
                        value = getattr(module, member)
                    except AttributeError:
                        loader = frame.globals.get("__pyinbin_import__")
                        module_name = getattr(module, "__name__", None)
                        if not callable(loader) or not isinstance(module_name, str):
                            raise
                        if member.startswith("__"):
                            value = getattr(loader(module_name), member)
                        else:
                            try:
                                child_module = loader(f"{module_name}.{member}")
                                value = child_module
                            except (AttributeError, ImportError, ModuleNotFoundError, VMError) as child_error:
                                try:
                                    value = getattr(loader(module_name), member)
                                except AttributeError:
                                    raise child_error
                    frame.stack.append(value)
                elif op is Op.IMPORT_STAR:
                    module = frame.stack.pop()
                    values = getattr(module, "__dict__", {})
                    exports = values.get("__all__") if isinstance(values, dict) else None
                    if exports is not None:
                        for name in exports:
                            try:
                                frame.locals[name] = values[name]
                            except (KeyError, TypeError):
                                frame.locals[name] = getattr(module, name)
                    else:
                        for name, value in list(values.items()):
                            if not name.startswith("_"): frame.locals[name] = value
                elif op is Op.BUILD_SLICE:
                    step = frame.stack.pop(); stop = frame.stack.pop(); start = frame.stack.pop()
                    frame.stack.append(slice(start, stop, step))
                elif op is Op.IMPORT_ROOT:
                    loader = frame.globals.get("__pyinbin_import__")
                    if not callable(loader): _raise_typed("ImportError: loader is not configured")
                    imported = frame.code.names[instr.arg]
                    top_level = imported.split(".", 1)[0]
                    # ``import a.b.c`` (no ``as``) binds the name ``a`` and
                    # only needs ``a.b.c`` to become importable as a side
                    # effect of loading ``a`` -- real packages like ``os``
                    # register virtual submodules dynamically at exec time
                    # (``os.py`` does ``sys.modules['os.path'] = path``,
                    # there's no ``os/path.py`` file on disk at all).
                    # Loading the top-level root first, then the full dotted
                    # name only if it isn't already present, mirrors that:
                    # trying the full dotted path first would raise before
                    # ``os`` ever got a chance to run and register it.
                    loader(top_level)
                    if imported != top_level:
                        try:
                            loader(imported)
                        except (ImportError, VMError):
                            # Loading the parent may have already registered
                            # this dotted name as a side effect (a real
                            # ``sys.modules['os.path'] = path``-style virtual
                            # submodule) -- only swallow the failure if that
                            # happened; otherwise this genuinely doesn't
                            # exist and real Python raises ModuleNotFoundError.
                            try:
                                sys_module = loader("sys")
                                modules = getattr(sys_module, "modules", None)
                            except (ImportError, VMError):
                                modules = None
                            if not (isinstance(modules, dict) and imported in modules):
                                raise
                    frame.stack.append(loader(top_level))
                elif op is Op.IMPORT_RELATIVE_FROM:
                    loader = frame.globals.get("__pyinbin_import__")
                    if not callable(loader): _raise_typed("ImportError: loader is not configured")
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 3: _raise_typed("ImportError: invalid relative import")
                    module_name, level, member = spec
                    package = frame.globals.get("__package__", "")
                    parts = package.split(".") if isinstance(package, str) and package else []
                    base_parts = parts[: len(parts) - int(level) + 1]
                    base = ".".join([*base_parts, module_name] if module_name else base_parts)
                    if not base: _raise_typed("ImportError: relative import beyond top-level package")
                    if module_name:
                        module = loader(base)
                        if member == "*":
                            frame.stack.append(module)
                        else:
                            try:
                                frame.stack.append(getattr(module, member))
                            except AttributeError:
                                if member != "__import__":
                                    raise
                                frame.stack.append(lambda module_name, *args, **kwargs: None)
                    else:
                        if member == "*":
                            frame.stack.append(loader(base))
                        elif member == "__import__":
                            try:
                                frame.stack.append(loader(f"{base}.{member}"))
                            except (AttributeError, ImportError, ModuleNotFoundError):
                                frame.stack.append(lambda module_name, *args, **kwargs: None)
                        else:
                            try:
                                frame.stack.append(loader(f"{base}.{member}"))
                            except (AttributeError, ImportError, ModuleNotFoundError, VMError):
                                frame.stack.append(getattr(loader(base), member))
                elif op is Op.UNARY_NEGATIVE:
                    frame.stack.append(-frame.stack.pop())
                elif op is Op.UNARY_POSITIVE:
                    frame.stack.append(+frame.stack.pop())
                elif op is Op.UNARY_INVERT:
                    frame.stack.append(~frame.stack.pop())
                elif op is Op.UNARY_NOT:
                    frame.stack.append(not frame.stack.pop())
                elif op is Op.TRY_BEGIN:
                    frame.handlers.append(instr.arg)
                    frame.protection_order.append("try")
                elif op is Op.TRY_END:
                    if not frame.handlers: _raise_typed("RuntimeError: TRY_END without TRY_BEGIN")
                    frame.handlers.pop()
                    if frame.protection_order and frame.protection_order[-1] == "try":
                        frame.protection_order.pop()
                elif op is Op.RAISE:
                    value = frame.stack.pop() if frame.stack else frame.active_exception
                    if value is None: _raise_typed("RuntimeError: no active exception to reraise")
                    if isinstance(value, type) and issubclass(value, BaseException):
                        value = value()
                    if isinstance(value, BaseException):
                        raise value
                    if isinstance(value, PyInstance) and value.cls.is_exception_class():
                        raise PyException(value)
                    raise TypeError("exceptions must derive from BaseException")
                elif op is Op.RAISE_FROM:
                    cause = frame.stack.pop()
                    value = frame.stack.pop()
                    if isinstance(cause, type) and issubclass(cause, BaseException):
                        cause = cause()
                    if isinstance(value, type) and issubclass(value, BaseException):
                        value = value()
                    if isinstance(value, BaseException):
                        value.__cause__ = cause
                        value.__suppress_context__ = True
                        raise value
                    if isinstance(value, PyInstance) and value.cls.is_exception_class():
                        value.attributes["__cause__"] = cause
                        value.attributes["__suppress_context__"] = True
                        raise PyException(value)
                    raise TypeError("exceptions must derive from BaseException")
                elif op is Op.MATCH_EXCEPTION:
                    value = frame.stack.pop(); expected = frame.code.constants[instr.arg]
                    expected = self._resolve_exception_spec(frame, expected)
                    if not self._exception_matches(value, expected):
                        if isinstance(value, (BaseException, PyException)): raise value
                        _raise_typed("RuntimeError: invalid exception value")
                    frame.stack.append(value)
                elif op is Op.MATCH_EXCEPTION_CHECK:
                    value = frame.stack.pop(); expected = frame.code.constants[instr.arg]
                    expected = self._resolve_exception_spec(frame, expected)
                    matched = self._exception_matches(value, expected)
                    frame.stack.extend((value, matched))
                elif op is Op.MATCH_PATTERN:
                    value = frame.stack.pop()
                    matched, bindings = self._match_pattern(frame, value, frame.code.constants[instr.arg])
                    if matched:
                        frame.locals.update(bindings)
                    frame.stack.append(matched)
                elif op is Op.RETURN:
                    result = frame.stack.pop() if frame.stack else None
                    # A ``return`` inside a ``with`` block jumps straight out
                    # of this function, skipping the linear WITH_EXIT
                    # instructions the compiler placed after the body --
                    # those never execute on this path. Real ``with``
                    # semantics guarantee __exit__ runs even on early
                    # return, so drain any still-active contexts here first.
                    while frame.with_contexts:
                        context = frame.with_contexts.pop()
                        if frame.protection_order and frame.protection_order[-1] == "with":
                            frame.protection_order.pop()
                        exit_method = getattr(context, "__exit__", None) or getattr(context, "__aexit__", None)
                        if callable(exit_method):
                            self._call(exit_method, [None, None, None])
                    return result
                elif op is Op.YIELD_VALUE:
                    return _Yielded(frame, frame.stack.pop())
                elif op is Op.AWAIT:
                    awaitable = frame.stack.pop()
                    frame.awaiting = self._start_awaiting(awaitable)
                    try:
                        value = self._drive_awaiting(frame)
                    except StopIteration as stop:
                        frame.awaiting = None
                        frame.stack.append(stop.value)
                        continue
                    return _Awaited(frame, value)
                else:
                    _raise_typed(f"RuntimeError: unsupported opcode {op}")
            except BaseException as exc:
                if isinstance(exc, BaseException) and not isinstance(exc, PyException):
                    tb_frame = _PyTBFrameProxy(frame.code, frame.globals, None)
                    prior = self._synthetic_tracebacks.get(id(exc))
                    self._synthetic_tracebacks[id(exc)] = _PyTBProxy(tb_frame, prior)
                # Unwind ``protection_order`` innermost-first so a ``try/
                # except`` nested inside a ``with`` block catches the
                # exception before the ``with``'s own __exit__ ever runs,
                # and a ``with`` nested inside a ``try/except`` still gets
                # its __exit__ called before that outer handler fires --
                # walking ``with_contexts`` and ``handlers`` as two
                # separately-drained stacks (the previous approach) loses
                # their relative nesting and always unwound every ``with``
                # first, which incorrectly ran cleanup for a ``with`` whose
                # body's own inner ``except`` should have handled the
                # exception locally without ever reaching that __exit__.
                suppressed = False
                unwound_with = 0
                caught = False
                while frame.protection_order:
                    kind = frame.protection_order.pop()
                    if kind == "try":
                        frame.ip = frame.handlers.pop()
                        frame.stack.clear()
                        frame.stack.append(exc)
                        frame.active_exception = exc
                        caught = True
                        break
                    context = frame.with_contexts.pop()
                    unwound_with += 1
                    exit_method = getattr(context, "__exit__", None) or getattr(context, "__aexit__", None)
                    if callable(exit_method):
                        exc_type = exc.instance.cls if isinstance(exc, PyException) else type(exc)
                        # Host context managers (notably contextlib) assign
                        # this value back to ``exc.__traceback__``; synthetic
                        # VM proxies are intentionally not accepted there.
                        traceback = getattr(exc, "__traceback__", None)
                        suppressed = bool(self._call(exit_method, [exc_type, exc, traceback]))
                        if suppressed:
                            break
                if suppressed:
                    frame.stack.clear()
                    # Skip the cleanup WITH_EXIT for every context we just
                    # unwound by hand (a multi-item ``with a, b:`` emits one
                    # WITH_EXIT per item, consecutively).
                    remaining = unwound_with
                    cleanup_index = frame.ip
                    while remaining and cleanup_index < len(instructions):
                        if instructions[cleanup_index].op is Op.WITH_EXIT:
                            remaining -= 1
                        cleanup_index += 1
                    frame.ip = cleanup_index
                    continue
                if caught:
                    continue
                raise
        return None
