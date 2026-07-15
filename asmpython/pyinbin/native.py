"""Explicit bootstrap modules used before the native pyinbin runtime exists.

These are deliberately registered by name instead of discovered through the
host import system.  They provide the small process/object-model surface that
pure-Python standard-library modules expect; target runtimes can replace each
provider without changing import semantics.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable
import ast as _bootstrap_ast


class _MemoryTextIO:
    def __init__(self, initial: str = "") -> None:
        self._value = initial
        self._position = 0

    def write(self, value: object) -> int:
        text = str(value)
        self._value += text
        self._position = len(self._value)
        return len(text)

    def read(self, size: int = -1) -> str:
        if size < 0:
            result = self._value[self._position:]
            self._position = len(self._value)
        else:
            result = self._value[self._position:self._position + size]
            self._position += len(result)
        return result

    def getvalue(self) -> str:
        return self._value

    def close(self) -> None:
        return None

    def __enter__(self) -> "_MemoryTextIO":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        self.close()
        return False


class _MemoryBytesIO:
    def __init__(self, initial: bytes = b"") -> None:
        self._value = bytes(initial)
        self._position = 0

    def write(self, value: bytes) -> int:
        value = bytes(value)
        self._value += value
        self._position = len(self._value)
        return len(value)

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = self._value[self._position:]
            self._position = len(self._value)
        else:
            result = self._value[self._position:self._position + size]
            self._position += len(result)
        return result

    def getvalue(self) -> bytes:
        return self._value

    def close(self) -> None:
        return None

    def __enter__(self) -> "_MemoryBytesIO":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        self.close()
        return False


class _IOPlaceholder:
    """Mutable bootstrap base used by the pure-Python ``io`` facade."""

    pass


class _FrameProxy:
    def __init__(self) -> None:
        self.f_globals: dict[str, object] = {}
        self.f_locals: dict[str, object] = self.f_globals
        self.f_back: "_FrameProxy | None" = None
        self.f_code = SimpleNamespace(co_name="<pyinbin>")


def _pack_uint32(value: int) -> bytes:
    value &= 0xFFFFFFFF
    return bytes((value & 255, (value >> 8) & 255, (value >> 16) & 255, (value >> 24) & 255))


def _unpack_uint32(value: bytes) -> int:
    return sum(byte << (index * 8) for index, byte in enumerate(value[:4]))


def _zip_longest(iterables: tuple[object, ...], fillvalue: object):
    iterators = [iter(item) for item in iterables]
    active = len(iterators)
    while active:
        row = []
        for index, iterator in enumerate(iterators):
            try: row.append(next(iterator))
            except StopIteration:
                row.append(fillvalue)
                iterators[index] = iter(())
                active -= 1
        if active or any(value is not fillvalue for value in row):
            yield tuple(row)


def _pairwise(iterable):
    iterator = iter(iterable)
    try: previous = next(iterator)
    except StopIteration: return
    for value in iterator:
        yield previous, value
        previous = value


def _batched(iterable, size: int):
    if size < 1: raise ValueError("n must be at least one")
    iterator = iter(iterable)
    while True:
        batch = []
        for _ in range(size):
            try: batch.append(next(iterator))
            except StopIteration: break
        if not batch: return
        yield tuple(batch)


def _module(name: str, values: dict[str, object]) -> SimpleNamespace:
    module = SimpleNamespace(__name__=name, __package__="", __file__=f"<pyinbin:{name}>")
    module.__dict__.update(values)
    return module


def _gcd(left: int, right: int) -> int:
    left, right = abs(left), abs(right)
    while right:
        left, right = right, left % right
    return left


def create_builtin_module(
    name: str,
    module_cache: dict[str, object],
    builtins: dict[str, object],
) -> SimpleNamespace | None:
    """Return an explicitly supported bootstrap module, if one exists."""
    if name == "sys":
        return _module(name, {
            "modules": module_cache,
            "path": [],
            "argv": [],
            "platform": "win32",
            "maxsize": (1 << 63) - 1,
            "byteorder": "little",
            "version": "pyinbin 2.0.0-preview",
            "builtin_module_names": ("sys", "_io", "_abc", "_locale", "itertools", "math", "nt", "_thread"),
            "implementation": _module("sys.implementation", {"name": "pyinbin"}),
            "getrecursionlimit": lambda: 1000,
            "setrecursionlimit": lambda value: None,
            "is_finalizing": lambda: False,
            "intern": lambda value: value,
            "getsizeof": lambda value, default=0: default,
            "_getframe": lambda depth=0: _FrameProxy(),
            "_getframemodulename": lambda depth=0: "__main__",
            "stdout": _MemoryTextIO(),
            "stderr": _MemoryTextIO(),
            "stdin": _MemoryTextIO(),
            "__stdout__": _MemoryTextIO(),
            "__stderr__": _MemoryTextIO(),
            "__stdin__": _MemoryTextIO(),
            "exc_info": lambda: (None, None, None),
        })
    if name == "builtins":
        return _module(name, dict(builtins))
    if name == "_abc":
        return _module(name, {
            "get_cache_token": lambda: 0,
            "_abc_init": lambda cls: None,
            "_abc_register": lambda cls, subclass: None,
            "_abc_instancecheck": lambda cls, instance: isinstance(instance, cls),
            "_abc_subclasscheck": lambda cls, subclass: issubclass(subclass, cls),
            "_abc_caches_clear": lambda cls: None,
            "_abc_registry_clear": lambda cls: None,
            "_get_dump": lambda cls: (set(), set(), set(), set()),
            "_reset_caches": lambda cls: None,
            "_reset_registry": lambda cls: None,
        })
    if name == "_locale":
        return _module(name, {
            "Error": ValueError,
            "LC_CTYPE": 0,
            "LC_NUMERIC": 1,
            "LC_TIME": 2,
            "LC_COLLATE": 3,
            "LC_MONETARY": 4,
            "LC_MESSAGES": 5,
            "LC_ALL": 6,
            "getpreferredencoding": lambda do_setlocale=True: "UTF-8",
            "setlocale": lambda category, locale=None: "C",
            "localeconv": lambda: {"decimal_point": ".", "thousands_sep": ""},
            "RADIXCHAR": ".", "THOUSEP": "",
            "nl_langinfo": lambda item: "UTF-8",
        })
    if name == "_imp":
        return _module(name, {
            "lock_held": lambda: False,
            "acquire_lock": lambda: None,
            "release_lock": lambda: None,
            "is_builtin": lambda module_name: 0,
            "is_frozen": lambda module_name: 0,
            "extension_suffixes": lambda: [],
        })
    if name == "_types":
        placeholder = object
        return _module(name, {
            "NoneType": type(None), "EllipsisType": type(Ellipsis),
            "GenericAlias": placeholder, "UnionType": placeholder,
            "MappingProxyType": lambda mapping: mapping,
            "DynamicClassAttribute": property,
            "FunctionType": placeholder, "MethodType": placeholder,
            "ModuleType": placeholder, "CodeType": placeholder,
            "FrameType": placeholder, "TracebackType": placeholder,
            "CellType": placeholder, "WrapperDescriptorType": placeholder,
            "CoroutineType": placeholder, "GeneratorType": placeholder,
            "AsyncGeneratorType": placeholder,
        })
    if name == "_frozen_importlib":
        return _module(name, {
            "BuiltinImporter": object, "FrozenImporter": object,
            "PathFinder": object, "ModuleSpec": object,
        })
    if name == "_frozen_importlib_external":
        return _module(name, {
            "PathFinder": object, "FileFinder": object,
            "SourceFileLoader": object, "SourcelessFileLoader": object,
            "ModuleSpec": object, "_pack_uint32": _pack_uint32, "_unpack_uint32": _unpack_uint32,
            "open_code": open,
        })
    if name == "_stat":
        return _module(name, {
            "S_IFMT": lambda mode: mode & 0o170000,
            "S_IFREG": 0o100000, "S_IFDIR": 0o040000, "S_IFLNK": 0o120000,
            "S_ISREG": lambda mode: (mode & 0o170000) == 0o100000,
            "S_ISDIR": lambda mode: (mode & 0o170000) == 0o040000,
            "S_ISLNK": lambda mode: (mode & 0o170000) == 0o120000,
        })
    if name == "math":
        def floor(value: float) -> int:
            integer = int(value)
            return integer if integer <= value else integer - 1
        def ceil(value: float) -> int:
            integer = int(value)
            return integer if integer >= value else integer + 1
        return _module(name, {
            "pi": 3.141592653589793, "e": 2.718281828459045,
            "tau": 6.283185307179586, "inf": float("inf"), "nan": float("nan"),
            "sqrt": lambda value: value ** 0.5, "floor": floor, "ceil": ceil,
            "fabs": abs, "isfinite": lambda value: value == value and value not in (float("inf"), float("-inf")),
            "isinf": lambda value: value in (float("inf"), float("-inf")),
            "isnan": lambda value: value != value, "trunc": int,
            "gcd": lambda left, right: _gcd(left, right),
        })
    if name == "errno":
        return _module(name, {
            "EPERM": 1, "ENOENT": 2, "EIO": 5, "EBADF": 9,
            "EAGAIN": 11, "ENOMEM": 12, "EACCES": 13, "EEXIST": 17,
            "ENOTDIR": 20, "EINVAL": 22, "ENOSPC": 28, "EPIPE": 32,
            "EWOULDBLOCK": 11, "ECONNRESET": 10054, "ETIMEDOUT": 10060,
        })
    if name == "_operator":
        return _module(name, {
            "add": lambda left, right: left + right, "sub": lambda left, right: left - right,
            "mul": lambda left, right: left * right, "truediv": lambda left, right: left / right,
            "floordiv": lambda left, right: left // right, "mod": lambda left, right: left % right,
            "pow": lambda left, right: left ** right, "matmul": lambda left, right: left @ right,
            "and_": lambda left, right: left & right, "or_": lambda left, right: left | right,
            "xor": lambda left, right: left ^ right, "lshift": lambda left, right: left << right,
            "rshift": lambda left, right: left >> right, "neg": lambda value: -value,
            "pos": lambda value: +value, "invert": lambda value: ~value,
            "eq": lambda left, right: left == right, "ne": lambda left, right: left != right,
            "lt": lambda left, right: left < right, "le": lambda left, right: left <= right,
            "gt": lambda left, right: left > right, "ge": lambda left, right: left >= right,
            "contains": lambda container, value: value in container,
            "getitem": lambda value, key: value[key], "setitem": lambda value, key, item: value.__setitem__(key, item),
            "delitem": lambda value, key: value.__delitem__(key), "index": int,
            "length_hint": lambda value, default=0: len(value) if hasattr(value, "__len__") else default,
        })
    if name == "_codecs":
        def encode(value: str, errors: str = "strict") -> tuple[bytes, int]:
            encoded = value.encode("utf-8", errors)
            return encoded, len(value)
        def decode(value: bytes, errors: str = "strict") -> tuple[str, int]:
            decoded = bytes(value).decode("utf-8", errors)
            return decoded, len(value)
        return _module(name, {
            "utf_8_encode": encode, "utf_8_decode": decode,
            "ascii_encode": lambda value, errors="strict": (value.encode("ascii", errors), len(value)),
            "ascii_decode": lambda value, errors="strict": (bytes(value).decode("ascii", errors), len(value)),
            "latin_1_encode": lambda value, errors="strict": (value.encode("latin-1", errors), len(value)),
            "latin_1_decode": lambda value, errors="strict": (bytes(value).decode("latin-1", errors), len(value)),
            "lookup": lambda encoding: None,
            "lookup_error": lambda name: None,
            "register": lambda search_function: None,
            "register_error": lambda name, handler: None,
        })
    if name == "atexit":
        callbacks: list[tuple[Callable[..., object], tuple[object, ...], dict[str, object]]] = []
        def register(func: Callable[..., object], *args: object, **kwargs: object) -> Callable[..., object]:
            callbacks.append((func, args, kwargs))
            return func
        def unregister(func: Callable[..., object]) -> None:
            callbacks[:] = [entry for entry in callbacks if entry[0] is not func]
        def run_exitfuncs() -> None:
            for func, args, kwargs in reversed(callbacks):
                func(*args, **kwargs)
        return _module(name, {
            "register": register,
            "unregister": unregister,
            "_run_exitfuncs": run_exitfuncs,
            "_clear": callbacks.clear,
            "_ncallbacks": lambda: len(callbacks),
        })
    if name == "_io":
        return _module(name, {
            "StringIO": _MemoryTextIO,
            "BytesIO": _MemoryBytesIO,
            "FileIO": _MemoryBytesIO, "BufferedReader": _MemoryBytesIO,
            "BufferedWriter": _MemoryBytesIO, "BufferedRandom": _MemoryBytesIO,
            "BufferedRWPair": _MemoryBytesIO,
            "TextIOWrapper": _MemoryTextIO, "IncrementalNewlineDecoder": _IOPlaceholder,
            "_WindowsConsoleIO": _MemoryBytesIO,
            "text_encoding": lambda encoding, stacklevel=2, /: encoding or "locale",
            "IOBase": _IOPlaceholder,
            "_IOBase": _IOPlaceholder, "_RawIOBase": _IOPlaceholder,
            "_BufferedIOBase": _IOPlaceholder, "_TextIOBase": _IOPlaceholder,
            "RawIOBase": _IOPlaceholder, "BufferedIOBase": _IOPlaceholder,
            "TextIOBase": _IOPlaceholder,
            "BlockingIOError": BlockingIOError,
            "UnsupportedOperation": OSError,
            "DEFAULT_BUFFER_SIZE": 8192,
            "open": open, "open_code": open,
        })
    if name == "_collections":
        class deque(list):
            def __init__(self, iterable=(), maxlen=None):
                super().__init__(iterable)
                self.maxlen = maxlen
            def appendleft(self, value): self.insert(0, value)
            def popleft(self):
                if not self: raise IndexError("pop from an empty deque")
                return self.pop(0)
            def extendleft(self, iterable):
                for value in iterable: self.appendleft(value)
            def rotate(self, count=1):
                if self:
                    count %= len(self)
                    self[:] = self[-count:] + self[:-count]
        class defaultdict(dict):
            def __init__(self, default_factory=None, *args, **kwargs):
                self.default_factory = default_factory
                super().__init__(*args, **kwargs)
            def __missing__(self, key):
                if self.default_factory is None: raise KeyError(key)
                value = self.default_factory()
                self[key] = value
                return value
        def count_elements(mapping, iterable):
            for value in iterable:
                mapping[value] = mapping.get(value, 0) + 1
        return _module(name, {
            "deque": deque, "defaultdict": defaultdict, "OrderedDict": dict,
            "_tuplegetter": lambda index, doc=None: property(lambda value: value[index]),
            "_deque_iterator": object, "_deque_reverse_iterator": object,
            "_odict_iterator": object, "_odict_keys": object, "_odict_values": object,
            "_odict_items": object,
            "_count_elements": count_elements,
        })
    if name == "_winapi":
        return _module(name, {
            "CREATE_NEW_CONSOLE": 0x10, "CREATE_NEW_PROCESS_GROUP": 0x200,
            "CREATE_NO_WINDOW": 0x8000000, "DETACHED_PROCESS": 8,
            "STARTF_USESTDHANDLES": 1, "STARTF_USESHOWWINDOW": 1,
            "SW_HIDE": 0, "INFINITE": 0xFFFFFFFF, "WAIT_OBJECT_0": 0,
            "WAIT_TIMEOUT": 258, "PIPE_ACCESS_INBOUND": 1, "PIPE_ACCESS_OUTBOUND": 2,
            "PIPE_TYPE_BYTE": 0, "PIPE_WAIT": 0, "DUPLICATE_SAME_ACCESS": 2,
            "GetVersion": lambda: 0, "GetCurrentProcess": lambda: 1,
            "LCMapStringEx": lambda locale, flags, value, *args: value,
            "LOCALE_NAME_INVARIANT": "",
            "LCMAP_LOWERCASE": 0x100, "_getvolumepathname": lambda path: path,
            "GetCurrentProcessId": lambda: 1, "GetLastError": lambda: 0,
            "CloseHandle": lambda handle: None, "DuplicateHandle": lambda *args: None,
            "CreatePipe": lambda *args: (0, 0), "CreateProcess": lambda *args: (0, 0, 0, 0),
            "WaitForSingleObject": lambda *args: 0, "TerminateProcess": lambda *args: None,
            "GetStdHandle": lambda handle: 0, "SetHandleInformation": lambda *args: None,
        })
    if name == "_opcode":
        return _module(name, {
            "stack_effect": lambda opcode, oparg=None, *, jump=None: 0,
            "get_executor": lambda code, offset: None,
            "has_arg": lambda opcode: opcode >= 90,
            "has_const": lambda opcode: True, "has_name": lambda opcode: True,
            "has_jump": lambda opcode: True,
            "has_free": lambda opcode: True,
        })
    if name == "_sre":
        # Bootstrap surface used while importing ``re``. Pattern execution
        # remains a VM-native milestone; these values keep source modules
        # importable without loading a host extension.
        return _module(name, {
            "MAGIC": 20230612, "CODESIZE": 2, "MAXREPEAT": (1 << 32) - 1,
            "MAXGROUPS": 100, "MAXGROUPREF": 100,
            "compile": lambda *args, **kwargs: None,
            "getcodesize": lambda: 2,
        })
    if name == "_ast":
        values = {key: value for key, value in vars(_bootstrap_ast).items() if not key.startswith("_")}
        values.update({"PyCF_ONLY_AST": 1024, "PyCF_TYPE_COMMENTS": 4096, "PyCF_ALLOW_TOP_LEVEL_AWAIT": 8192})
        return _module(name, values)
    if name == "_thread":
        class Lock:
            def __init__(self) -> None: self._locked = False
            def acquire(self, waitflag=True, timeout=-1):
                self._locked = True
                return True
            def release(self): self._locked = False
            def locked(self): return self._locked
            def __enter__(self): self.acquire(); return self
            def __exit__(self, exc_type, exc_value, traceback): self.release(); return False
        return _module(name, {
            "LockType": Lock, "RLock": Lock, "allocate_lock": Lock,
            "get_ident": lambda: 1, "get_native_id": lambda: 1,
            "stack_size": lambda size=0: 0,
            "start_new_thread": lambda function, args, kwargs=None: function(*args, **(kwargs or {})) or 1,
            "TIMEOUT_MAX": 1e9,
        })
    if name == "nt":
        return _module(name, {
            "name": "nt", "sep": "\\", "altsep": "/", "pathsep": ";",
            "defpath": ".", "devnull": "NUL", "curdir": ".", "pardir": "..", "extsep": ".",
            "getcwd": lambda: ".", "getcwdb": lambda: b".", "listdir": lambda path=".": [],
            "_getvolumepathname": lambda path: path,
            "_path_normpath": lambda path: path,
            "_getfullpathname": lambda path: path,
            "_findfirstfile": lambda path: -1,
            "_getfinalpathname": lambda path: path,
            "scandir": lambda path=".": iter(()), "mkdir": lambda path, mode=0o777: None,
            "makedirs": lambda path, mode=0o777: None, "rmdir": lambda path: None,
            "unlink": lambda path: None, "remove": lambda path: None,
            "rename": lambda source, target: None, "replace": lambda source, target: None,
            "stat": lambda path: SimpleNamespace(st_mode=0o100000),
            "lstat": lambda path: SimpleNamespace(st_mode=0o100000),
            "getenv": lambda key, default=None: default, "putenv": lambda key, value: None,
            "unsetenv": lambda key: None, "environ": {}, "supports_bytes_environ": False,
            "fsencode": lambda value: value.encode() if isinstance(value, str) else value,
            "fsdecode": lambda value: value.decode() if isinstance(value, bytes) else value,
            "urandom": lambda size: bytes(size), "open": open, "close": lambda fd: None,
            "read": lambda fd, size: b"", "write": lambda fd, data: len(data),
            "access": lambda path, mode: False, "F_OK": 0, "R_OK": 4, "W_OK": 2, "X_OK": 1,
            "_exit": lambda status=0: None,
            "_path_splitroot_ex": lambda path: ("", "", path),
        })
    if name == "itertools":
        def count(start=0, step=1):
            value = start
            while True:
                yield value
                value += step
        def repeat(value, times=None):
            if times is None:
                while True: yield value
            else:
                for _ in range(times): yield value
        def chain(*iterables):
            for iterable in iterables:
                for value in iterable: yield value
        def cycle(iterable):
            saved = []
            for value in iterable:
                saved.append(value)
                yield value
            while saved:
                for value in saved: yield value
        def islice(iterable, start, stop=None, step=1):
            if stop is None: start, stop = 0, start
            for index, value in enumerate(iterable):
                if index >= stop: break
                if index >= start and (index - start) % step == 0: yield value
        def accumulate(iterable, func=None, *, initial=None):
            iterator = iter(iterable)
            if initial is None:
                try: total = next(iterator)
                except StopIteration: return
            else:
                total = initial
                yield total
            yield total
            for value in iterator:
                total = total + value if func is None else func(total, value)
                yield total
        def compress(data, selectors):
            for value, selected in zip(data, selectors):
                if selected: yield value
        def filterfalse(predicate, iterable):
            predicate = predicate or bool
            for value in iterable:
                if not predicate(value): yield value
        return _module(name, {
            "count": count, "repeat": repeat, "chain": chain, "cycle": cycle,
            "islice": islice, "accumulate": accumulate, "compress": compress,
            "filterfalse": filterfalse, "starmap": lambda function, iterable: (function(*args) for args in iterable),
            "zip_longest": lambda *iterables, fillvalue=None: _zip_longest(iterables, fillvalue),
            "pairwise": lambda iterable: _pairwise(iterable),
            "batched": lambda iterable, n: _batched(iterable, n),
        })
    if name == "_weakref":
        class ref:
            def __init__(self, value, callback=None): self._value, self._callback = value, callback
            def __call__(self): return self._value
        return _module(name, {
            "ref": ref, "ReferenceType": ref, "ProxyType": object,
            "CallableProxyType": object, "getweakrefcount": lambda value: 0,
            "getweakrefs": lambda value: [], "proxy": lambda value, callback=None: value,
        })
    if name == "_functools":
        class Placeholder:
            def __repr__(self): return "Placeholder"
        class Partial:
            __pyinbin_partial__ = True
            def __init__(self, function, args=(), kwargs=None):
                self.function, self.args, self.kwargs = function, tuple(args), dict(kwargs or {})
        def reduce(function, iterable, initial=None):
            iterator = iter(iterable)
            if initial is None:
                value = next(iterator)
            else:
                value = initial
            for item in iterator: value = function(value, item)
            return value
        return _module(name, {
            "reduce": reduce, "partial": lambda function, *args, **kwargs: Partial(function, args, kwargs),
            "cmp_to_key": lambda comparator: (lambda value: value),
            "_lru_cache_wrapper": object,
            "Placeholder": Placeholder(), "_PlaceholderType": Placeholder,
        })
    return None
