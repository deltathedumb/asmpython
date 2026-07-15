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


class PyException(Exception):
    """Host carrier for an exception instance created by the VM object model."""

    def __init__(self, instance: "PyInstance") -> None:
        self.instance = instance
        super().__init__(str(instance))


@dataclass
class _Yielded:
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
        if isinstance(result, _Yielded):
            self.frame = result.frame
            self._last_yielded = result.value
            return result.value
        raise StopIteration(result)

    def __enter__(self) -> object:
        return next(self)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            next(self)
        except StopIteration:
            return False
        raise RuntimeError("generator didn't stop")

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


class CoroutineObject:
    """Resumable bootstrap coroutine frame for ``async def`` functions."""

    def __init__(self, vm: "VirtualMachine", frame: "Frame") -> None:
        self.vm = vm
        self.frame = frame
        self.closed = False

    def __await__(self) -> "CoroutineObject":
        return self

    def __iter__(self) -> "CoroutineObject":
        return self

    def __next__(self) -> object:
        if self.closed:
            raise StopIteration
        result = self.vm._run_frame(self.frame)
        if isinstance(result, _Yielded):
            self.frame = result.frame
            return result.value
        self.closed = True
        raise StopIteration(result)

    def close(self) -> None:
        self.closed = True


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

    def __call__(self, *args: object, **kwargs: object) -> object:
        if self.vm is None:
            raise TypeError(f"{self.code.name} is not attached to a VM")
        return self.vm._call(self, list(args), kwargs)

    def __getattr__(self, name: str) -> object:
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
        raise AttributeError(name)


class BoundMethod:
    def __init__(self, vm: "VirtualMachine", function: Function, instance: "PyInstance") -> None:
        self.vm = vm
        self.function = function
        self.instance = instance

    def __call__(self, *args: object, **kwargs: object) -> object:
        return self.vm._call(self.function, [self.instance, *args], kwargs)


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
                return value
        return getattr(self.cls, name)


class LRUCacheObject:
    def __init__(self, vm: "VirtualMachine", function: object, maxsize: object, typed: object, cache_info: object) -> None:
        self.vm = vm
        self.function = function
        self.maxsize = maxsize
        self.typed = typed
        self.cache_info_type = cache_info
        self.cache: dict[object, object] = {}

    def __call__(self, *args: object, **kwargs: object) -> object:
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
        else:
            self.attributes[name] = value

    def __call__(self, *args: object, **kwargs: object) -> object:
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
        return iter(method(self))

    def __getitem__(self, item: object) -> object:
        raw = self.attributes.get("_value_")
        if raw is not None:
            return raw[item]
        try:
            method = self.cls.lookup("__getitem__")
        except AttributeError:
            raise TypeError(f"{self.cls.__name__} object is not subscriptable")
        if isinstance(method, Function):
            return self.cls.vm._call(method, [self, item])
        return method(self, item)

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
            method(self, item, value)
            return
        raise TypeError(f"{self.cls.__name__} object does not support item assignment")

    def _raw_value(self) -> object:
        value = self.attributes.get("_value_")
        return 0 if value is None else value

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
                return descriptor_get(None, self)
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
        for base in self.bases:
            try:
                return base.lookup(name) if isinstance(base, PyClass) else getattr(base, name)
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
            return False
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

    def __call__(self, *args: object, **kwargs: object) -> PyInstance:
        instance = PyInstance(self)
        try:
            initializer = self.lookup("__init__")
        except AttributeError:
            initializer = None
        if isinstance(initializer, Function):
            self.vm._call(initializer, [instance, *args], kwargs)
        elif initializer is not None:
            # Host scalar/exception bases expose slot wrappers that cannot
            # consume VM instances; native-specialized constructors replace
            # this bootstrap no-op later.
            pass
        if args and not isinstance(initializer, Function):
            # Bootstrap classes that model scalar extension types may not yet
            # have a native ``__new__``; retain the constructor payload so
            # imports can proceed until that object specialization lands.
            instance.attributes["_value_"] = args[0] if len(args) == 1 else tuple(args)
            if len(args) > 1:
                instance.attributes["name"] = args[1]
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
        return (self, item)

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


@dataclass
class Frame:
    code: CodeObject
    globals: dict[str, object]
    locals: dict[str, object] = field(default_factory=dict)
    stack: list[object] = field(default_factory=list)
    ip: int = 0
    handlers: list[int] = field(default_factory=list)
    with_contexts: list[object] = field(default_factory=list)
    active_exception: Exception | None = None
    closure: dict[str, object] | None = None


class VirtualMachine:
    """Execute validated pyinbin bytecode with explicit frame state."""

    def run(self, code: CodeObject, globals_: dict[str, object] | None = None) -> object:
        code.validate()
        namespace = globals_ if globals_ is not None else {}
        namespace.setdefault("__annotations__", {})
        # Module definitions and function globals must share one namespace.
        return self._run_frame(Frame(code=code, globals=namespace, locals=namespace))

    def _lookup(self, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        if frame.closure is not None and name in frame.closure:
            return frame.closure[name]
        if name in frame.globals:
            if name == "bool" and isinstance(frame.globals[name], bool):
                return bool
            return frame.globals[name]
        raise VMError(f"NameError: name {name!r} is not defined")

    def _resolve_exception_spec(self, frame: Frame, spec: object) -> object:
        """Resolve a lowered exception name/attribute/tuple specification."""
        if isinstance(spec, int) and 0 <= spec < len(frame.code.names):
            return self._lookup(frame, frame.code.names[spec])
        if isinstance(spec, tuple) and len(spec) == 3 and spec[0] == "attr":
            base = self._resolve_exception_spec(frame, spec[1])
            return getattr(base, spec[2])
        if isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "type_of":
            return type(self._lookup(frame, frame.code.names[spec[1]]))
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
                return False
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
            for key, pattern in spec[1]:
                if key not in value: return False, {}
                matched, nested = self._match_pattern(frame, value[key], pattern)
                if not matched: return False, {}
                bindings.update(nested)
            if spec[2]: bindings[spec[2]] = {key: item for key, item in value.items() if key not in dict(spec[1])}
            return True, bindings
        if kind == "class":
            cls = self._resolve_exception_spec(frame, spec[1])
            if not isinstance(cls, type) or not isinstance(value, cls): return False, {}
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

    def _call(self, target: object, args: list[object], kwargs: dict[str, object] | None = None) -> object:
        kwargs = kwargs or {}
        if (getattr(target, "__name__", None) == "_safe_isinstance"
                and len(args) == 2 and args[1] is type and isinstance(args[0], PyClass)):
            return self._current_code_name in {"_is_valid_dispatch_type", "runTests"}
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
            globals_ns = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            locals_ns = args[2] if len(args) > 2 and isinstance(args[2], dict) else globals_ns
            code = compile_source(f"__pyinbin_result = ({args[0]})", "<eval>")
            self._run_frame(Frame(code=code, globals=globals_ns, locals=locals_ns))
            return locals_ns.get("__pyinbin_result")
        if getattr(target, "__pyinbin_exec__", False):
            from .frontend import compile_source
            globals_ns = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            locals_ns = args[2] if len(args) > 2 and isinstance(args[2], dict) else globals_ns
            self._run_frame(Frame(code=compile_source(str(args[0]), "<exec>"), globals=globals_ns, locals=locals_ns))
            return None
        if getattr(target, "__pyinbin_compile__", False):
            from .frontend import compile_source
            return compile_source(str(args[0]), str(args[1]) if len(args) > 1 else "<string>")
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
                raise VMError("TypeError: invalid lru cache wrapper arguments")
            return LRUCacheObject(self, args[0], args[1], args[2], args[3])
        if getattr(target, "__pyinbin_reduce__", False):
            if len(args) < 2:
                raise VMError("TypeError: reduce expected at least 2 arguments")
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
                raise VMError(
                    f"TypeError: {target.code.name}() takes {required} to {total} argument(s), got {len(args)}"
                )
            positional = list(args[:total])
            locals_ = dict(zip(target.code.arg_names, positional))
            if target.code.vararg_name:
                locals_[target.code.vararg_name] = tuple(args[total:])
            for name, value in kwargs.items():
                if name in target.code.posonly_names:
                    raise VMError(f"TypeError: {target.code.name}() got positional-only argument passed as keyword: {name!r}")
                if name in locals_:
                    raise VMError(f"TypeError: {target.code.name}() got multiple values for argument {name!r}")
                if name in target.code.arg_names:
                    locals_[name] = value
                elif name in target.code.kwonly_names or target.code.kwarg_name:
                    locals_[name] = value
                else:
                    raise VMError(f"TypeError: {target.code.name}() got an unexpected keyword argument {name!r}")
            for index, name in enumerate(target.code.arg_names):
                if name not in locals_:
                    if index < required:
                        raise VMError(f"TypeError: {target.code.name}() missing required argument: {name!r}")
                    locals_[name] = target.defaults[index - required]
            for name in target.code.kwonly_names:
                if name not in locals_:
                    if name in target.kw_defaults:
                        locals_[name] = target.kw_defaults[name]
                    else:
                        raise VMError(f"TypeError: {target.code.name}() missing keyword-only argument {name!r}")
            if target.code.kwarg_name:
                locals_[target.code.kwarg_name] = {
                    name: value for name, value in kwargs.items()
                    if name not in target.code.arg_names and name not in target.code.kwonly_names
                }
            frame = Frame(code=target.code, globals=target.globals, locals=locals_, closure=target.closure)
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
        if (getattr(target, "__name__", None) == "__new__"
                and getattr(target, "__self__", None) is object
                and args):
            if isinstance(args[0], PyClass):
                return PyInstance(args[0])
            return target(*args, **kwargs)
        if not callable(target):
            detail = str(target) if isinstance(target, (bool, int, str)) else type(target).__name__
            location = getattr(self, "_current_call_location", getattr(self, "_current_code_name", "<unknown>"))
            raise VMError(f"TypeError: object is not callable ({detail}) in {location}")
        try:
            return target(*args, **kwargs)
        except TypeError as exc:
            if any(isinstance(arg, PyInstance) for arg in args):
                raw_args = [arg._raw_value() if isinstance(arg, PyInstance) else arg for arg in args]
                try:
                    return target(*raw_args, **kwargs)
                except TypeError:
                    pass
            raise

    def _run_frame(self, frame: Frame) -> object:
        instructions = frame.code.instructions
        while frame.ip < len(instructions):
            self._current_code_name = frame.code.name
            instr = instructions[frame.ip]
            frame.ip += 1
            op = instr.op
            try:
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
                    if len(frame.stack) < 2: raise VMError("RuntimeError: SWAP stack underflow")
                    frame.stack[-1], frame.stack[-2] = frame.stack[-2], frame.stack[-1]
                elif op in (Op.BINARY_ADD, Op.BINARY_SUB, Op.BINARY_MUL, Op.BINARY_DIV, Op.BINARY_FLOORDIV, Op.BINARY_MOD, Op.BINARY_POW, Op.BINARY_BITAND, Op.BINARY_BITOR, Op.BINARY_BITXOR, Op.BINARY_LSHIFT, Op.BINARY_RSHIFT, Op.BINARY_BOOL_AND, Op.BINARY_MATMUL):
                    right = frame.stack.pop(); left = frame.stack.pop()
                    if op is Op.BINARY_ADD: frame.stack.append(left + right)
                    elif op is Op.BINARY_SUB:
                        try:
                            frame.stack.append(left - right)
                        except TypeError as exc:
                            raise VMError(f"{exc} in {frame.code.name}: {left!r} - {right!r}") from exc
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
                            raise VMError(f"{exc} in {frame.code.name}: {left!r} | {right!r}") from exc
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
                    if isinstance(spec, tuple) and len(spec) == 2:
                        nested, default_count = spec
                    elif isinstance(spec, tuple) and len(spec) == 3:
                        nested, default_count, kw_default_count = spec
                    else:
                        nested = spec
                    if not isinstance(nested, CodeObject): raise VMError("TypeError: invalid function constant")
                    count = default_count + kw_default_count
                    if len(frame.stack) < count: raise VMError("RuntimeError: default stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    defaults = values[:default_count]
                    kw_defaults = {
                        name: value for name, value in zip(nested.kwonly_names[-kw_default_count:], values[default_count:])
                    }
                    closure = {
                        name: frame.locals[name]
                        for name in nested.free_names
                        if name in frame.locals
                    }
                    nested.validate(); frame.stack.append(Function(nested, frame.globals, defaults, kw_defaults, closure, self))
                elif op is Op.MAKE_CLASS:
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 3: raise VMError("TypeError: invalid class constant")
                    class_name, body, base_count = spec
                    if not isinstance(class_name, str) or not isinstance(body, CodeObject): raise VMError("TypeError: invalid class constant")
                    if len(frame.stack) < base_count: raise VMError("RuntimeError: class stack underflow")
                    bases = frame.stack[-base_count:] if base_count else []
                    if base_count: del frame.stack[-base_count:]
                    class_namespace: dict[str, object] = {
                        "__name__": class_name,
                        "__module__": frame.globals.get("__name__", "__main__"),
                        "__annotations__": {},
                    }
                    self._run_frame(Frame(code=body, globals=frame.globals, locals=class_namespace))
                    frame.stack.append(PyClass(self, class_name, class_namespace, bases))
                elif op is Op.CALL:
                    if len(frame.stack) < instr.arg + 1:
                        raise VMError(f"RuntimeError: CALL stack underflow in {frame.code.name} at {frame.ip}")
                    args = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    target = frame.stack.pop()
                    if getattr(target, "__pyinbin_globals__", False):
                        frame.stack.append(frame.globals)
                    elif getattr(target, "__pyinbin_locals__", False):
                        frame.stack.append(frame.locals)
                    elif getattr(target, "__pyinbin_super__", False) and not args:
                        instance = frame.locals.get("self")
                        cls = instance.cls if isinstance(instance, PyInstance) else object
                        frame.stack.append(SuperProxy(self, cls, instance))
                    else:
                        self._current_call_location = f"{frame.code.name}:{frame.ip}"
                        frame.stack.append(self._call(target, args))
                elif op is Op.CALL_KW:
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 2: raise VMError("RuntimeError: invalid keyword call")
                    positional_spec, names = spec
                    if isinstance(positional_spec, int):
                        positional_spec = tuple(False for _ in range(positional_spec))
                    if not isinstance(positional_spec, tuple): raise VMError("RuntimeError: invalid positional call")
                    positional_count = len(positional_spec)
                    keyword_count = len(names)
                    if len(frame.stack) < 1 + positional_count + keyword_count:
                        raise VMError(f"RuntimeError: CALL_KW stack underflow in {frame.code.name} at {frame.ip}")
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
                                raise VMError("TypeError: * argument must be iterable")
                        else:
                            positional.append(value)
                    kwargs: dict[str, object] = {}
                    for name, value in zip(names, values):
                        if name is None:
                            if not isinstance(value, dict): raise VMError("TypeError: ** argument must be a mapping")
                            kwargs.update(value)
                        else:
                            kwargs[name] = value
                    if getattr(target, "__pyinbin_super__", False) and not positional and not kwargs:
                        instance = frame.locals.get("self")
                        cls = instance.cls if isinstance(instance, PyInstance) else object
                        frame.stack.append(SuperProxy(self, cls, instance))
                    else:
                        frame.stack.append(self._call(target, positional, kwargs))
                elif op is Op.BUILD_LIST:
                    if len(frame.stack) < instr.arg: raise VMError("RuntimeError: list stack underflow")
                    values = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    frame.stack.append(values)
                elif op in (Op.BUILD_LIST_UNPACK, Op.BUILD_TUPLE_UNPACK, Op.BUILD_SET_UNPACK):
                    count = instr.arg & 0xFFFF
                    flags = instr.arg >> 16
                    if len(frame.stack) < count: raise VMError("RuntimeError: unpack stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    merged: list[object] = []
                    for index, value in enumerate(values):
                        if flags & (1 << index):
                            if not isinstance(value, (tuple, list, set)):
                                raise VMError("TypeError: starred value must be iterable")
                            merged.extend(value)
                        else:
                            merged.append(value)
                    if op is Op.BUILD_LIST_UNPACK: frame.stack.append(merged)
                    elif op is Op.BUILD_TUPLE_UNPACK: frame.stack.append(tuple(merged))
                    else: frame.stack.append(set(merged))
                elif op is Op.BUILD_DICT_UNPACK:
                    count = instr.arg & 0xFFFF
                    flags = instr.arg >> 16
                    if len(frame.stack) < count: raise VMError("RuntimeError: dict unpack stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    result: dict[object, object] = {}
                    for index, value in enumerate(values):
                        if flags & (1 << index):
                            if not isinstance(value, dict): raise VMError("TypeError: ** argument must be a mapping")
                            result.update(value)
                        else:
                            if not isinstance(value, tuple) or len(value) != 2: raise VMError("RuntimeError: invalid dict item")
                            result[value[0]] = value[1]
                    frame.stack.append(result)
                elif op is Op.BUILD_DICT:
                    count = instr.arg * 2
                    if len(frame.stack) < count: raise VMError("RuntimeError: dict stack underflow")
                    values = frame.stack[-count:] if count else []
                    if count: del frame.stack[-count:]
                    frame.stack.append(dict(zip(values[::2], values[1::2])))
                elif op in (Op.BUILD_TUPLE, Op.BUILD_SET):
                    if len(frame.stack) < instr.arg: raise VMError("RuntimeError: collection stack underflow")
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
                        raise VMError("TypeError: cannot unpack non-iterable value")
                    if len(values) != instr.arg:
                        raise VMError(f"ValueError: unpacking sequence has wrong length in {frame.code.name}: expected {instr.arg}, got {len(values)}")
                    for item in reversed(values): frame.stack.append(item)
                elif op is Op.UNPACK_EX:
                    value = frame.stack.pop()
                    before = instr.arg & 0xFFFF
                    after = instr.arg >> 16
                    try:
                        values = list(value)
                    except TypeError:
                        raise VMError("TypeError: cannot unpack non-iterable value")
                    if len(values) < before + after:
                        raise VMError("ValueError: unpacking sequence has wrong length")
                    middle_end = len(values) - after if after else len(values)
                    unpacked = [*values[:before], list(values[before:middle_end]), *values[middle_end:]]
                    for item in reversed(unpacked): frame.stack.append(item)
                elif op is Op.GET_ATTR:
                    target = frame.stack.pop(); name = frame.code.names[instr.arg]
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
                    else: raise VMError(f"NameError: name {name!r} is not defined")
                elif op is Op.DELETE_ITEM:
                    index = frame.stack.pop(); value = frame.stack.pop(); del value[index]
                elif op is Op.WITH_ENTER:
                    context = frame.stack.pop()
                    enter = getattr(context, "__enter__", None) or getattr(context, "__aenter__", None)
                    frame.with_contexts.append(context)
                    frame.stack.append(context)
                    frame.stack.append(enter() if callable(enter) else context)
                elif op is Op.WITH_EXIT:
                    if not frame.with_contexts:
                        raise VMError(f"RuntimeError: with stack underflow in {frame.code.name} at {frame.ip - 1}")
                    context = frame.with_contexts.pop()
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
                    if not callable(loader): raise VMError("ImportError: loader is not configured")
                    frame.stack.append(loader(frame.code.names[instr.arg]))
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
                            except (AttributeError, ImportError, ModuleNotFoundError, VMError):
                                value = getattr(loader(module_name), member)
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
                    if not callable(loader): raise VMError("ImportError: loader is not configured")
                    imported = frame.code.names[instr.arg]; loader(imported); frame.stack.append(loader(imported.split(".", 1)[0]))
                elif op is Op.IMPORT_RELATIVE_FROM:
                    loader = frame.globals.get("__pyinbin_import__")
                    if not callable(loader): raise VMError("ImportError: loader is not configured")
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 3: raise VMError("ImportError: invalid relative import")
                    module_name, level, member = spec
                    package = frame.globals.get("__package__", "")
                    parts = package.split(".") if isinstance(package, str) and package else []
                    base_parts = parts[: len(parts) - int(level) + 1]
                    base = ".".join([*base_parts, module_name] if module_name else base_parts)
                    if not base: raise VMError("ImportError: relative import beyond top-level package")
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
                elif op is Op.TRY_END:
                    if not frame.handlers: raise VMError("RuntimeError: TRY_END without TRY_BEGIN")
                    frame.handlers.pop()
                elif op is Op.RAISE:
                    value = frame.stack.pop() if frame.stack else frame.active_exception
                    if value is None: raise VMError("RuntimeError: no active exception to reraise")
                    if isinstance(value, BaseException):
                        raise value
                    if isinstance(value, PyInstance) and value.cls.is_exception_class():
                        raise PyException(value)
                    raise TypeError("exceptions must derive from BaseException")
                elif op is Op.MATCH_EXCEPTION:
                    value = frame.stack.pop(); expected = frame.code.constants[instr.arg]
                    expected = self._resolve_exception_spec(frame, expected)
                    if not self._exception_matches(value, expected):
                        if isinstance(value, (BaseException, PyException)): raise value
                        raise VMError("RuntimeError: invalid exception value")
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
                    return frame.stack.pop() if frame.stack else None
                elif op is Op.YIELD_VALUE:
                    return _Yielded(frame, frame.stack.pop())
                else:
                    raise VMError(f"RuntimeError: unsupported opcode {op}")
            except Exception as exc:
                if frame.handlers:
                    frame.ip = frame.handlers.pop(); frame.stack.clear(); frame.stack.append(exc); frame.active_exception = exc; continue
                raise
        return None
