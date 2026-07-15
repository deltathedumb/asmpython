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


@dataclass
class _Yielded:
    frame: "Frame"
    value: object


class GeneratorObject:
    def __init__(self, vm: "VirtualMachine", frame: "Frame") -> None:
        self.vm = vm
        self.frame = frame

    def __iter__(self) -> "GeneratorObject":
        return self

    def __next__(self) -> object:
        result = self.vm._run_frame(self.frame)
        if isinstance(result, _Yielded):
            self.frame = result.frame
            return result.value
        raise StopIteration(result)


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


@dataclass
class Function:
    code: CodeObject
    globals: dict[str, object]
    defaults: list[object] = field(default_factory=list)
    kw_defaults: dict[str, object] = field(default_factory=dict)
    closure: dict[str, object] | None = None

    def __getattr__(self, name: str) -> object:
        if name == "__name__":
            return self.code.name.rsplit(".", 1)[-1]
        if name == "__qualname__":
            return self.code.name
        if name == "__module__":
            return self.globals.get("__name__", "__main__")
        if name == "__doc__":
            return None
        raise AttributeError(name)


class BoundMethod:
    def __init__(self, vm: "VirtualMachine", function: Function, instance: "PyInstance") -> None:
        self.vm = vm
        self.function = function
        self.instance = instance

    def __call__(self, *args: object, **kwargs: object) -> object:
        return self.vm._call(self.function, [self.instance, *args], kwargs)


class PyInstance:
    def __init__(self, cls: "PyClass") -> None:
        self.cls = cls
        self.attributes: dict[str, object] = {}

    def __getattr__(self, name: str) -> object:
        if name in self.attributes:
            return self.attributes[name]
        value = self.cls.lookup(name)
        if isinstance(value, Function):
            return BoundMethod(self.cls.vm, value, self)
        return value

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"cls", "attributes"}:
            object.__setattr__(self, name, value)
        else:
            self.attributes[name] = value

    def __len__(self) -> int:
        value = self.attributes.get("_value_")
        return len(value) if value is not None else 0


class PyClass:
    def __init__(self, vm: "VirtualMachine", name: str, attributes: dict[str, object], bases: list[object]) -> None:
        self.vm = vm
        self.__name__ = name
        self.attributes = attributes
        self.bases = [base for base in bases if isinstance(base, PyClass)]

    def __getattribute__(self, name: str) -> object:
        if name == "__dict__":
            return object.__getattribute__(self, "attributes")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        # Class-body attributes and dynamically-added members (notably enum
        # members) live in the VM namespace, while representation fields stay
        # on the host wrapper itself.
        if name in {"vm", "__name__", "attributes", "bases"} or "attributes" not in self.__dict__:
            object.__setattr__(self, name, value)
        else:
            self.attributes[name] = value

    def lookup(self, name: str) -> object:
        if name in self.attributes:
            return self.attributes[name]
        for base in self.bases:
            try:
                return base.lookup(name)
            except AttributeError:
                pass
        raise AttributeError(name)

    def __getattr__(self, name: str) -> object:
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
        return self.lookup(name)

    def __call__(self, *args: object) -> PyInstance:
        instance = PyInstance(self)
        try:
            initializer = self.lookup("__init__")
        except AttributeError:
            initializer = None
        if isinstance(initializer, Function):
            self.vm._call(initializer, [instance, *args])
        elif initializer is not None:
            raise VMError(f"TypeError: {self.__name__}.__init__ is not callable")
        elif args:
            # Bootstrap classes that model scalar extension types may not yet
            # have a native ``__new__``; retain the constructor payload so
            # imports can proceed until that object specialization lands.
            instance.attributes["_value_"] = args[0] if len(args) == 1 else tuple(args)
            if len(args) > 1:
                instance.attributes["name"] = args[1]
        return instance

    def __iter__(self):
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
    active_exception: Exception | None = None
    closure: dict[str, object] | None = None


class VirtualMachine:
    """Execute validated pyinbin bytecode with explicit frame state."""

    def run(self, code: CodeObject, globals_: dict[str, object] | None = None) -> object:
        code.validate()
        namespace = globals_ if globals_ is not None else {}
        # Module definitions and function globals must share one namespace.
        return self._run_frame(Frame(code=code, globals=namespace, locals=namespace))

    def _lookup(self, frame: Frame, name: str) -> object:
        if name in frame.locals:
            return frame.locals[name]
        if frame.closure is not None and name in frame.closure:
            return frame.closure[name]
        if name in frame.globals:
            return frame.globals[name]
        raise VMError(f"NameError: name {name!r} is not defined")

    def _resolve_exception_spec(self, frame: Frame, spec: object) -> object:
        """Resolve a lowered exception name/attribute/tuple specification."""
        if isinstance(spec, int) and 0 <= spec < len(frame.code.names):
            return self._lookup(frame, frame.code.names[spec])
        if isinstance(spec, tuple) and len(spec) == 3 and spec[0] == "attr":
            base = self._resolve_exception_spec(frame, spec[1])
            return getattr(base, spec[2])
        if isinstance(spec, tuple):
            return tuple(self._resolve_exception_spec(frame, item) for item in spec)
        return spec

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
        if getattr(target, "__pyinbin_eval__", False):
            from .frontend import compile_source
            namespace = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            code = compile_source(f"__pyinbin_result = ({args[0]})", "<eval>")
            self._run_frame(Frame(code=code, globals=namespace, locals=namespace))
            return namespace.get("__pyinbin_result")
        if getattr(target, "__pyinbin_exec__", False):
            from .frontend import compile_source
            namespace = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            self._run_frame(Frame(code=compile_source(str(args[0]), "<exec>"), globals=namespace, locals=namespace))
            return None
        if getattr(target, "__pyinbin_compile__", False):
            from .frontend import compile_source
            return compile_source(str(args[0]), str(args[1]) if len(args) > 1 else "<string>")
        if getattr(target, "__pyinbin_partial__", False):
            return self._call(target.function, [*target.args, *args], {**target.kwargs, **kwargs})
        if isinstance(target, Function):
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
            return self._run_frame(frame)
        # ``type(name, bases, namespace)`` is used by the stdlib to create
        # classes dynamically.  Route pyinbin classes through the VM object
        # model instead of asking host ``type`` to interpret them.
        if target is type and len(args) >= 3 and isinstance(args[0], str) and isinstance(args[2], dict):
            return PyClass(self, args[0], dict(args[2]), list(args[1]))
        if (getattr(target, "__name__", None) == "__new__"
                and getattr(target, "__self__", None) is object
                and args and isinstance(args[0], PyClass)):
            return PyInstance(args[0])
        if not callable(target):
            raise VMError("TypeError: object is not callable")
        return target(*args, **kwargs)

    def _run_frame(self, frame: Frame) -> object:
        instructions = frame.code.instructions
        while frame.ip < len(instructions):
            instr = instructions[frame.ip]
            frame.ip += 1
            op = instr.op
            try:
                if op is Op.LOAD_CONST:
                    frame.stack.append(frame.code.constants[instr.arg])
                elif op is Op.LOAD_NAME:
                    frame.stack.append(self._lookup(frame, frame.code.names[instr.arg]))
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
                    elif op is Op.BINARY_SUB: frame.stack.append(left - right)
                    elif op is Op.BINARY_MUL: frame.stack.append(left * right)
                    elif op is Op.BINARY_DIV: frame.stack.append(left / right)
                    elif op is Op.BINARY_FLOORDIV: frame.stack.append(left // right)
                    elif op is Op.BINARY_POW: frame.stack.append(left ** right)
                    elif op is Op.BINARY_BITAND: frame.stack.append(left & right)
                    elif op is Op.BINARY_BITOR: frame.stack.append(left | right)
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
                    nested.validate(); frame.stack.append(Function(nested, frame.globals, defaults, kw_defaults, frame.locals))
                elif op is Op.MAKE_CLASS:
                    spec = frame.code.constants[instr.arg]
                    if not isinstance(spec, tuple) or len(spec) != 3: raise VMError("TypeError: invalid class constant")
                    class_name, body, base_count = spec
                    if not isinstance(class_name, str) or not isinstance(body, CodeObject): raise VMError("TypeError: invalid class constant")
                    if len(frame.stack) < base_count: raise VMError("RuntimeError: class stack underflow")
                    bases = frame.stack[-base_count:] if base_count else []
                    if base_count: del frame.stack[-base_count:]
                    class_namespace: dict[str, object] = {"__name__": class_name}
                    self._run_frame(Frame(code=body, globals=frame.globals, locals=class_namespace))
                    frame.stack.append(PyClass(self, class_name, class_namespace, bases))
                elif op is Op.CALL:
                    if len(frame.stack) < instr.arg + 1: raise VMError("RuntimeError: CALL stack underflow")
                    args = frame.stack[-instr.arg:] if instr.arg else []
                    if instr.arg: del frame.stack[-instr.arg:]
                    target = frame.stack.pop()
                    if getattr(target, "__pyinbin_globals__", False):
                        frame.stack.append(frame.globals)
                    elif getattr(target, "__pyinbin_locals__", False):
                        frame.stack.append(frame.locals)
                    else:
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
                    if len(frame.stack) < 1 + positional_count + keyword_count: raise VMError("RuntimeError: CALL_KW stack underflow")
                    values = frame.stack[-keyword_count:] if keyword_count else []
                    if keyword_count: del frame.stack[-keyword_count:]
                    raw_positional = frame.stack[-positional_count:] if positional_count else []
                    if positional_count: del frame.stack[-positional_count:]
                    target = frame.stack.pop()
                    positional: list[object] = []
                    for is_starred, value in zip(positional_spec, raw_positional):
                        if is_starred:
                            if not isinstance(value, (tuple, list)): raise VMError("TypeError: * argument must be iterable")
                            positional.extend(value)
                        else:
                            positional.append(value)
                    kwargs: dict[str, object] = {}
                    for name, value in zip(names, values):
                        if name is None:
                            if not isinstance(value, dict): raise VMError("TypeError: ** argument must be a mapping")
                            kwargs.update(value)
                        else:
                            kwargs[name] = value
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
                    index = frame.stack.pop(); value = frame.stack.pop(); frame.stack.append(value[index])
                elif op is Op.SET_ITEM:
                    item = frame.stack.pop(); index = frame.stack.pop(); value = frame.stack.pop(); value[index] = item
                elif op is Op.GET_ITER:
                    value = frame.stack.pop()
                    if isinstance(value, dict):
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
                        raise VMError("ValueError: unpacking sequence has wrong length")
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
                    frame.stack.append(getattr(frame.stack.pop(), frame.code.names[instr.arg]))
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
                    frame.stack.append(context)
                    frame.stack.append(enter() if callable(enter) else context)
                elif op is Op.WITH_EXIT:
                    context = frame.stack.pop()
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
                    module = frame.stack.pop(); frame.stack.append(getattr(module, frame.code.names[instr.arg]))
                elif op is Op.IMPORT_STAR:
                    module = frame.stack.pop()
                    values = getattr(module, "__dict__", {})
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
                        frame.stack.append(module if member == "*" else getattr(module, member))
                    else:
                        frame.stack.append(loader(base) if member == "*" else loader(f"{base}.{member}"))
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
                    if isinstance(value, BaseException): raise value
                    raise TypeError("exceptions must derive from BaseException")
                elif op is Op.MATCH_EXCEPTION:
                    value = frame.stack.pop(); expected = frame.code.constants[instr.arg]
                    expected = self._resolve_exception_spec(frame, expected)
                    if not isinstance(value, BaseException) or not isinstance(expected, type) or not isinstance(value, expected):
                        if isinstance(value, BaseException): raise value
                        raise VMError("RuntimeError: invalid exception value")
                    frame.stack.append(value)
                elif op is Op.MATCH_EXCEPTION_CHECK:
                    value = frame.stack.pop(); expected = frame.code.constants[instr.arg]
                    expected = self._resolve_exception_spec(frame, expected)
                    matched = (
                        isinstance(value, BaseException)
                        and (isinstance(expected, type) or (isinstance(expected, tuple) and all(isinstance(item, type) for item in expected)))
                        and isinstance(value, expected)
                    )
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
