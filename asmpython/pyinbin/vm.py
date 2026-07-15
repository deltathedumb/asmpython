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


@dataclass
class Function:
    code: CodeObject
    globals: dict[str, object]
    defaults: list[object] = field(default_factory=list)
    kw_defaults: dict[str, object] = field(default_factory=dict)


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


class PyClass:
    def __init__(self, vm: "VirtualMachine", name: str, attributes: dict[str, object], bases: list[object]) -> None:
        self.vm = vm
        self.__name__ = name
        self.attributes = attributes
        self.bases = [base for base in bases if isinstance(base, PyClass)]

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
            raise VMError(f"TypeError: {self.__name__}() takes 0 argument(s), got {len(args)}")
        return instance


@dataclass
class Frame:
    code: CodeObject
    globals: dict[str, object]
    locals: dict[str, object] = field(default_factory=dict)
    stack: list[object] = field(default_factory=list)
    ip: int = 0
    handlers: list[int] = field(default_factory=list)
    active_exception: Exception | None = None


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
        if name in frame.globals:
            return frame.globals[name]
        raise VMError(f"NameError: name {name!r} is not defined")

    def _call(self, target: object, args: list[object], kwargs: dict[str, object] | None = None) -> object:
        kwargs = kwargs or {}
        if isinstance(target, Function):
            total = len(target.code.arg_names)
            required = total - len(target.defaults)
            if len(args) > total and target.code.vararg_name is None:
                raise VMError(
                    f"TypeError: {target.code.name}() takes {required} to {total} argument(s), got {len(args)}"
                )
            positional = list(args[:total])
            if len(positional) < total:
                positional.extend(target.defaults[len(positional) - required:])
            locals_ = dict(zip(target.code.arg_names, positional))
            if target.code.vararg_name:
                locals_[target.code.vararg_name] = tuple(args[total:])
            for name, value in kwargs.items():
                if name in target.code.posonly_names:
                    raise VMError(f"TypeError: {target.code.name}() got positional-only argument passed as keyword: {name!r}")
                if name in locals_:
                    locals_[name] = value
                elif name in target.code.kwonly_names or target.code.kwarg_name:
                    locals_[name] = value
                else:
                    raise VMError(f"TypeError: {target.code.name}() got an unexpected keyword argument {name!r}")
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
            return self._run_frame(
                Frame(code=target.code, globals=target.globals, locals=locals_)
            ) if not target.code.is_generator else GeneratorObject(
                self, Frame(code=target.code, globals=target.globals, locals=locals_)
            )
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
                    frame.locals[frame.code.names[instr.arg]] = frame.stack.pop()
                elif op is Op.STORE_GLOBAL:
                    frame.globals[frame.code.names[instr.arg]] = frame.stack.pop()
                elif op is Op.POP_TOP:
                    frame.stack.pop()
                elif op is Op.DUP_TOP:
                    frame.stack.append(frame.stack[-1])
                elif op is Op.SWAP:
                    if len(frame.stack) < 2: raise VMError("RuntimeError: SWAP stack underflow")
                    frame.stack[-1], frame.stack[-2] = frame.stack[-2], frame.stack[-1]
                elif op in (Op.BINARY_ADD, Op.BINARY_SUB, Op.BINARY_MUL, Op.BINARY_DIV, Op.BINARY_FLOORDIV, Op.BINARY_MOD, Op.BINARY_POW, Op.BINARY_BITAND, Op.BINARY_BITOR, Op.BINARY_BITXOR, Op.BINARY_LSHIFT, Op.BINARY_RSHIFT, Op.BINARY_BOOL_AND):
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
                    nested.validate(); frame.stack.append(Function(nested, frame.globals, defaults, kw_defaults))
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
                    frame.stack.append(self._call(frame.stack.pop(), args))
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
                    frame.stack.append(iter(frame.stack.pop()))
                elif op is Op.FOR_ITER:
                    try: frame.stack.append(next(frame.stack[-1]))
                    except StopIteration: frame.stack.pop(); frame.ip = instr.arg
                elif op is Op.UNPACK_SEQUENCE:
                    value = frame.stack.pop()
                    if not isinstance(value, (tuple, list)) or len(value) != instr.arg:
                        raise VMError("ValueError: unpacking sequence has wrong length")
                    for item in reversed(value): frame.stack.append(item)
                elif op is Op.GET_ATTR:
                    frame.stack.append(getattr(frame.stack.pop(), frame.code.names[instr.arg]))
                elif op is Op.SET_ATTR:
                    value = frame.stack.pop(); setattr(frame.stack.pop(), frame.code.names[instr.arg], value)
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
                    enter = getattr(context, "__enter__", None)
                    frame.stack.append(context)
                    frame.stack.append(enter() if callable(enter) else context)
                elif op is Op.WITH_EXIT:
                    context = frame.stack.pop()
                    exit_method = getattr(context, "__exit__", None)
                    if callable(exit_method): exit_method(None, None, None)
                elif op is Op.ASSERT:
                    message = frame.stack.pop() if instr.arg else None
                    if not frame.stack.pop(): raise AssertionError(message)
                elif op is Op.LIST_APPEND:
                    value = frame.stack.pop(); target = frame.stack.pop(); target.append(value); frame.stack.append(target)
                elif op is Op.IMPORT_NAME:
                    loader = frame.globals.get("__pyinbin_import__")
                    if not callable(loader): raise VMError("ImportError: loader is not configured")
                    frame.stack.append(loader(frame.code.names[instr.arg]))
                elif op is Op.IMPORT_FROM:
                    module = frame.stack.pop(); frame.stack.append(getattr(module, frame.code.names[instr.arg]))
                elif op is Op.IMPORT_STAR:
                    module = frame.stack.pop()
                    values = getattr(module, "__dict__", {})
                    for name, value in values.items():
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
                        frame.stack.append(getattr(loader(base), member))
                    else:
                        frame.stack.append(loader(f"{base}.{member}"))
                elif op is Op.UNARY_NEGATIVE:
                    frame.stack.append(-frame.stack.pop())
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
                    if isinstance(expected, int) and 0 <= expected < len(frame.code.names):
                        expected = self._lookup(frame, frame.code.names[expected])
                    if not isinstance(value, BaseException) or not isinstance(expected, type) or not isinstance(value, expected):
                        if isinstance(value, BaseException): raise value
                        raise VMError("RuntimeError: invalid exception value")
                    frame.stack.append(value)
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
