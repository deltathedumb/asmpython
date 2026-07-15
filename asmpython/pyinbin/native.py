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
import contextlib as _bootstrap_contextlib
import gc as _bootstrap_gc
import unicodedata as _bootstrap_unicodedata
import binascii as _bootstrap_binascii
import heapq as _bootstrap_heapq
import marshal as _bootstrap_marshal
import os as _bootstrap_os
import pickle as _bootstrap_pickle
import re as _bootstrap_re
import select as _bootstrap_select
import struct as _bootstrap_struct
import time as _bootstrap_time
import _string as _bootstrap_string
import _sysconfig as _bootstrap_sysconfig
import zlib as _bootstrap_zlib
import _socket as _bootstrap_socket
import _ssl as _bootstrap_ssl
import _overlapped as _bootstrap_overlapped
import _ctypes as _bootstrap_ctypes
import _bz2 as _bootstrap_bz2
import _lzma as _bootstrap_lzma
import _zstd as _bootstrap_zstd
import _queue as _bootstrap_queue
import _multibytecodec as _bootstrap_multibytecodec
import _csv as _bootstrap_csv
import array as _bootstrap_array
import bisect as _bootstrap_bisect
import math as _bootstrap_math
import cmath as _bootstrap_cmath
import datetime as _bootstrap_datetime
import calendar as _bootstrap_calendar
import errno as _bootstrap_errno
import typing as _bootstrap_typing
import _typing as _bootstrap_typing_native


class _TemplateInterpolation:
    def __init__(self, value: object, expression: str = "", conversion: object = None,
                 format_spec: object = "") -> None:
        self.value = value
        self.expression = expression
        self.conversion = conversion
        self.format_spec = "" if format_spec is None else format_spec

    def __repr__(self) -> str:
        return "Interpolation(" + repr(self.value) + ", " + repr(self.expression) + ")"


class _Template:
    def __init__(self, *parts: object) -> None:
        strings: list[str] = [""]
        interpolations: list[_TemplateInterpolation] = []
        for part in parts:
            if isinstance(part, str):
                strings[-1] += part
            else:
                interpolations.append(part)
                strings.append("")
        self.strings = tuple(strings)
        self.interpolations = tuple(interpolations)
        self._parts = tuple(parts)

    def __iter__(self):
        return iter(self._parts)

    def __str__(self) -> str:
        out = ""
        for part in self._parts:
            if isinstance(part, str):
                out += part
            else:
                value = part.value
                if part.conversion == "r":
                    value = repr(value)
                elif part.conversion == "s":
                    value = str(value)
                elif part.conversion == "a":
                    value = ascii(value)
                out += format(value, part.format_spec) if part.format_spec else str(value)
        return out
import _random as _bootstrap_random
import random as _bootstrap_random_module


class _MemoryTextIO:
    def __init__(self, initial: str = "", *args: object, **kwargs: object) -> None:
        self._value = initial
        self._position = 0
        self.encoding = kwargs.get("encoding") or "utf-8"
        self.errors = kwargs.get("errors") or "strict"
        self.newlines = None
        self.mode = kwargs.get("mode", "r")

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

    def flush(self) -> None:
        return None

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._position = offset
        elif whence == 1:
            self._position += offset
        elif whence == 2:
            self._position = len(self._value) + offset
        else:
            raise ValueError("invalid whence")
        return self._position

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("stream has no file descriptor")

    def close(self) -> None:
        return None

    def __enter__(self) -> "_MemoryTextIO":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        self.close()
        return False


class _WindowsVersion(tuple):
    def __new__(cls) -> "_WindowsVersion":
        return tuple.__new__(cls, (10, 0, 0, 2, "", (10, 0, 0)))

    major = 10
    minor = 0
    build = 0
    platform = 2
    service_pack = ""
    platform_version = (10, 0, 0)


class _VersionInfo(tuple):
    major = 3
    minor = 14
    micro = 0
    releaselevel = "final"
    serial = 0
    n_fields = 5

    def __new__(cls):
        return tuple.__new__(cls, (3, 14, 0, "final", 0))

def _open_compat(file: object, mode: object = "r", *args: object, **kwargs: object) -> object:
    if isinstance(mode, int):
        fd = _bootstrap_os.open(file, mode, 0o666)
        text_mode = "r+b" if mode & _bootstrap_os.O_RDWR else "wb" if mode & _bootstrap_os.O_WRONLY else "rb"
        return _bootstrap_os.fdopen(fd, text_mode)
    return open(file, mode, *args, **kwargs)


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


class _SREPattern:
    """Import-safe placeholder for the future native regex engine."""

    flags = 0
    groups = 0
    groupindex: dict[str, int] = {}

    def match(self, string: object, pos: int = 0, endpos: int | None = None) -> None:
        return None

    search = match
    fullmatch = match

    def findall(self, string: object, pos: int = 0, endpos: int | None = None) -> list[object]:
        return []

    def finditer(self, string: object, pos: int = 0, endpos: int | None = None):
        return iter(())

    def split(self, string: object, maxsplit: int = 0) -> list[object]:
        return [string]

    def sub(self, repl: object, string: object, count: int = 0) -> object:
        return string

    subn = sub


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


def _pack_uint16(value: int) -> bytes:
    value &= 0xFFFF
    return bytes((value & 255, (value >> 8) & 255))


def _unpack_uint16(value: bytes) -> int:
    return sum(byte << (index * 8) for index, byte in enumerate(value[:2]))


def _pack_uint64(value: int) -> bytes:
    return int(value).to_bytes(8, "little", signed=False)


def _unpack_uint64(value: bytes) -> int:
    return int.from_bytes(value[:8], "little", signed=False)


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


def _permutations(iterable, r=None):
    values = tuple(iterable)
    width = len(values) if r is None else r
    def build(prefix, remaining):
        if len(prefix) == width:
            yield prefix
            return
        for index, value in enumerate(remaining):
            yield from build(prefix + (value,), remaining[:index] + remaining[index + 1:])
    return build((), values)


def _combinations(iterable, r: int):
    values = tuple(iterable)
    def build(start, prefix):
        if len(prefix) == r:
            yield prefix
            return
        for index in range(start, len(values)):
            yield from build(index + 1, prefix + (values[index],))
    return build(0, ())


def _product(*iterables, repeat=1):
    pools = [tuple(item) for item in iterables] * repeat
    result = [()]
    for pool in pools:
        result = [prefix + (value,) for prefix in result for value in pool]
    return iter(result)


class _BootstrapModule(SimpleNamespace):
    def __getattr__(self, name: str) -> object:
        module_name = self.__dict__.get("__name__")
        if module_name == "nt" and name.startswith("_path_"):
            return lambda *args, **kwargs: False
        if module_name == "_winapi" and name.isupper():
            return 0
        if module_name == "_winapi" and name and name[0].isupper():
            return lambda *args, **kwargs: 0
        if module_name == "_ssl" and name.startswith("RAND_"):
            return lambda *args, **kwargs: None
        if module_name == "_asyncio" and name.startswith("_"):
            return lambda *args, **kwargs: None
        if module_name == "_opcode" and name.startswith("get_"):
            return lambda *args, **kwargs: ()
        if module_name == "builtins" and name == "__import__":
            return lambda module, *args, **kwargs: None
        raise AttributeError(name)


def _module(name: str, values: dict[str, object]) -> SimpleNamespace:
    module = _BootstrapModule(__name__=name, __package__="", __file__=f"<pyinbin:{name}>")
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
            "meta_path": [],
            "path_hooks": [],
            "path_importer_cache": {},
            "argv": ["pyinbin"],
            "warnoptions": [],
            "platform": "win32",
            "base_prefix": "",
            "prefix": "",
            "base_exec_prefix": "",
            "exec_prefix": "",
            "platlibdir": "lib",
            "_vpath": "",
            "_jit": SimpleNamespace(is_enabled=lambda: False, is_active=lambda: False),
            "monitoring": SimpleNamespace(
                DEBUGGER_ID=0, PROFILER_ID=1, COVERAGE_ID=2,
                events=SimpleNamespace(
                    PY_START=1, PY_RESUME=2, PY_RETURN=4, PY_YIELD=8,
                    PY_THROW=16, PY_UNWIND=32, PY_CALL=64, PY_LINE=128,
                    PY_INSTRUCTION=256, PY_RESUME_LINE=512, LINE=128,
                    JUMP=1024, BRANCH=2048, CALL=4096, RETURN=8192, RAISE=16384,
                    STOP_ITERATION=32768, C_RAISE=65536, C_RETURN=131072,
                    INSTRUCTION=256,
                ),
                use_tool_id=lambda *args: None,
                register_callback=lambda *args: None,
                set_events=lambda *args: None,
                get_events=lambda *args: 0,
                restart_events=lambda *args: None,
            ),
            "executable": "",
            "flags": SimpleNamespace(
                debug=0, inspect=0, interactive=0, optimize=0, dont_write_bytecode=0,
                no_user_site=0, no_site=0, ignore_environment=0, verbose=0, bytes_warning=0,
                quiet=0, hash_randomization=1, isolated=0, dev_mode=False, utf8_mode=0,
                warn_default_encoding=0, safe_path=False, int_max_str_digits=4300,
                context_aware_warnings=0,
            ),
            "maxsize": (1 << 63) - 1,
            "int_info": SimpleNamespace(bits_per_digit=30, sizeof_digit=4),
            "byteorder": "little",
            "version": "pyinbin 2.0.0-preview",
            "version_info": _VersionInfo(),
            "builtin_module_names": ("sys", "_io", "_abc", "_locale", "itertools", "math", "nt", "_thread"),
            "implementation": _module("sys.implementation", {"name": "pyinbin"}),
            "hash_info": SimpleNamespace(width=64, modulus=(1 << 61) - 1, inf=314159, nan=0, imag=1000003, algorithm="siphash13", hash_bits=64, seed_bits=128),
            "float_info": _module("sys.float_info", {
                "max": 1.7976931348623157e308, "min": 2.2250738585072014e-308,
                "epsilon": 2.220446049250313e-16, "mant_dig": 53,
                "dig": 15, "max_exp": 1024, "min_exp": -1021,
                "max_10_exp": 308, "min_10_exp": -307, "radix": 2,
            }),
            "getrecursionlimit": lambda: 1000,
            "exit": lambda code=0: None,
            "getfilesystemencoding": lambda: "utf-8", "getfilesystemencodeerrors": lambda: "surrogatepass",
            "getdefaultencoding": lambda: "utf-8",
            "getwindowsversion": lambda: _WindowsVersion(),
            "setrecursionlimit": lambda value: None,
            "is_finalizing": lambda: False,
            "intern": lambda value: value,
            "getsizeof": lambda value, default=0: default,
            "_clear_type_descriptors": lambda *args: None,
            "_getframe": lambda depth=0: _FrameProxy(),
            "_getframemodulename": lambda depth=0: "__main__",
            "stdout": _MemoryTextIO(),
            "stderr": _MemoryTextIO(),
            "stdin": _MemoryTextIO(),
            "__stdout__": _MemoryTextIO(),
            "__stderr__": _MemoryTextIO(),
            "__stdin__": _MemoryTextIO(),
            "exc_info": lambda: (None, None, None),
            "exception": lambda: None,
            "excepthook": lambda exc_type, exc_value, traceback: None,
            "unraisablehook": lambda unraisable: None,
            "audit": lambda *args, **kwargs: None,
        })
    if name == "builtins":
        return _module(name, dict(builtins))
    if name == "_abc":
        return _module(name, {
            "get_cache_token": lambda: 0,
            "_abc_init": lambda cls: None,
            "_have_functions": True,
            "_abc_register": lambda cls, subclass: None,
            "_abc_instancecheck": lambda cls, instance: isinstance(instance, cls),
            "_abc_subclasscheck": lambda cls, subclass: issubclass(subclass, cls),
            "_abc_caches_clear": lambda cls: None,
            "_abc_registry_clear": lambda cls: None,
            "_get_dump": lambda cls: (set(), set(), set(), set()),
            "_reset_caches": lambda cls: None,
            "_reset_registry": lambda cls: None,
        })
    if name == "_warnings":
        filters: list[object] = []
        registry: dict[object, object] = {}
        class WarningContext:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_value, traceback): return False
        return _module(name, {
            "filters": filters, "onceregistry": registry, "_onceregistry": registry,
            "_defaultaction": "default", "warn": lambda *args, **kwargs: None,
            "warn_explicit": lambda *args, **kwargs: None,
            "simplefilter": lambda *args, **kwargs: None,
            "_acquire_lock": lambda: None, "_release_lock": lambda: None,
            "_filters_mutated_lock_held": lambda: None,
            "_warnings_context": WarningContext,
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
            "getencoding": lambda: "UTF-8",
            "CODESET": 14,
            "setlocale": lambda category, locale=None: "C",
            "localeconv": lambda: {"decimal_point": ".", "thousands_sep": ""},
            "RADIXCHAR": ".", "THOUSEP": "",
            "nl_langinfo": lambda item: "UTF-8",
        })
    if name == "_imp":
        return _module(name, {
            "pyc_magic_number_token": 0,
            "source_hash": lambda key, source: 0,
            "lock_held": lambda: False,
            "acquire_lock": lambda: None,
            "release_lock": lambda: None,
            "is_builtin": lambda module_name: 0,
            "is_frozen": lambda module_name: 0,
            "extension_suffixes": lambda: [],
            "_override_frozen_modules_for_tests": lambda *args: None,
        })
    if name == "typing":
        values = {key: getattr(_bootstrap_typing, key) for key in dir(_bootstrap_typing)}
        values.setdefault("Match", _bootstrap_re.Match)
        values.setdefault("Pattern", _bootstrap_re.Pattern)
        return _module(name, values)
    if name == "contextlib":
        return _module(name, {
            key: getattr(_bootstrap_contextlib, key)
            for key in dir(_bootstrap_contextlib)
            if not key.startswith("__")
        })
    if name == "_typing":
        values = {key: getattr(_bootstrap_typing_native, key) for key in dir(_bootstrap_typing_native)}
        values.setdefault("_idfunc", lambda value: value)
        values.setdefault("NoDefault", getattr(_bootstrap_typing, "NoDefault", None))
        return _module(name, values)
    if name == "_random":
        class RandomBase:
            @staticmethod
            def seed(value=None):
                return None
            @staticmethod
            def getrandbits(bits):
                return 0
            @staticmethod
            def random():
                return 0.5
        return _module(name, {
            "Random": RandomBase,
            "seed": lambda *args, **kwargs: None,
        })
    if name == "_types":
        class _TypePlaceholder:
            pass
        placeholder = _TypePlaceholder
        return _module(name, {
            "NoneType": type(None), "EllipsisType": type(Ellipsis), "NotImplementedType": type(NotImplemented),
            "SimpleNamespace": SimpleNamespace,
            "GenericAlias": placeholder, "UnionType": placeholder,
            "MappingProxyType": lambda mapping: mapping,
            "DynamicClassAttribute": property,
            "FunctionType": placeholder, "MethodType": placeholder,
            "BuiltinFunctionType": placeholder, "BuiltinMethodType": placeholder,
            "MethodWrapperType": placeholder, "WrapperDescriptorType": placeholder,
            "ClassMethodDescriptorType": placeholder, "MethodDescriptorType": placeholder,
            "MemberDescriptorType": placeholder, "ModuleType": SimpleNamespace,
            "ModuleType": placeholder, "CodeType": placeholder,
            "FrameType": placeholder, "TracebackType": placeholder,
            "CellType": placeholder, "WrapperDescriptorType": placeholder,
            "GetSetDescriptorType": placeholder,
            "CoroutineType": placeholder, "GeneratorType": placeholder,
            "AsyncGeneratorType": placeholder,
        })
    if name == "_frozen_importlib":
        def resolve_name(module_name, package, level):
            if level <= 0:
                return module_name
            parts = package.rsplit(".", level - 1)
            if len(parts) < level:
                raise ImportError("attempted relative import beyond top-level package")
            base = parts[0]
            return f"{base}.{module_name}" if module_name else base
        def module_from_spec(spec):
            module_name = getattr(spec, "name", "")
            return SimpleNamespace(__name__=module_name, __spec__=spec)
        def spec_from_loader(name, loader, *, origin=None, is_package=None):
            return SimpleNamespace(name=name, loader=loader, origin=origin, submodule_search_locations=[] if is_package else None)
        return _module(name, {
            "BuiltinImporter": object, "FrozenImporter": object,
            "PathFinder": object, "ModuleSpec": object,
            "module_from_spec": module_from_spec,
            "spec_from_loader": spec_from_loader,
            "_find_spec": lambda name, path=None, target=None: None,
            "_resolve_name": resolve_name,
            "_gcd_import": lambda module_name, package=None, level=0: None,
            "_init_module_attrs": lambda *args, **kwargs: None,
        })
    if name == "_frozen_importlib_external":
        def all_suffixes():
            return [".py", ".pyc"]
        def cache_from_source(path, debug_override=None, *, optimization=None):
            return f"{path}c"
        def source_from_cache(path):
            return path[:-1] if path.endswith("c") else path
        def spec_from_file_location(name, location=None, *, loader=None, submodule_search_locations=None):
            return SimpleNamespace(name=name, loader=loader, origin=location, submodule_search_locations=submodule_search_locations)
        return _module(name, {
            "PathFinder": object, "FileFinder": object,
            "_LoaderBasics": object, "FileLoader": object, "SourceLoader": object,
            "SourceFileLoader": object, "SourcelessFileLoader": object,
            "ExtensionFileLoader": object, "AppleFrameworkLoader": object,
            "NamespaceLoader": object, "WindowsRegistryFinder": object,
            "ModuleSpec": object, "_pack_uint32": _pack_uint32, "_unpack_uint32": _unpack_uint32,
            "_pack_uint16": _pack_uint16, "_unpack_uint16": _unpack_uint16,
            "_pack_uint64": _pack_uint64, "_unpack_uint64": _unpack_uint64,
            "open_code": open,
            "SOURCE_SUFFIXES": [".py"], "BYTECODE_SUFFIXES": [".pyc"],
            "DEBUG_BYTECODE_SUFFIXES": [".pyc"], "OPTIMIZED_BYTECODE_SUFFIXES": [".pyc"],
            "EXTENSION_SUFFIXES": [], "FILE_EXTENSION": ".py",
            "MAGIC_NUMBER": b"pyin",
            "path_sep": "\\",
            "path_separators": ["\\", "/"],
            "cache_from_source": cache_from_source,
            "source_from_cache": source_from_cache,
            "spec_from_file_location": spec_from_file_location,
            "decode_source": lambda data: data.decode("utf-8") if isinstance(data, bytes) else data,
            "all_suffixes": all_suffixes,
        })
    if name == "_stat":
        return _module(name, {
            "S_IFMT": lambda mode: mode & 0o170000,
            "S_IFREG": 0o100000, "S_IFDIR": 0o040000, "S_IFLNK": 0o120000,
            "S_ISREG": lambda mode: (mode & 0o170000) == 0o100000,
            "S_ISDIR": lambda mode: (mode & 0o170000) == 0o040000,
            "S_ISLNK": lambda mode: (mode & 0o170000) == 0o120000,
        })
    if name == "_queue":
        return _module(name, {
            "Empty": _bootstrap_queue.Empty,
            "SimpleQueue": _bootstrap_queue.SimpleQueue,
        })
    if name == "_wmi":
        class _WMIUnavailable(_BootstrapModule):
            def __bool__(self) -> bool:
                return False
        module = _WMIUnavailable(__name__=name, __package__="", __file__=f"<pyinbin:{name}>")
        module.exec_query = lambda *args, **kwargs: ""
        return module
    if name == "winreg":
        class _WinRegError(OSError):
            pass
        return _module(name, {
            "HKEY_LOCAL_MACHINE": object(), "HKEY_CURRENT_USER": object(),
            "KEY_READ": 0x20019, "KEY_WOW64_32KEY": 0x200,
            "OpenKey": lambda *args, **kwargs: (_ for _ in ()).throw(_WinRegError("not supported")),
            "OpenKeyEx": lambda *args, **kwargs: (_ for _ in ()).throw(_WinRegError("not supported")),
            "ConnectRegistry": lambda *args, **kwargs: (_ for _ in ()).throw(_WinRegError("not supported")),
            "QueryValueEx": lambda *args, **kwargs: (_ for _ in ()).throw(_WinRegError("not supported")),
            "CloseKey": lambda *args, **kwargs: None,
            "error": _WinRegError,
        })
    if name == "pwd":
        return _module(name, {
            "getpwnam": lambda name: None,
            "getpwuid": lambda uid: None,
            "getpwall": lambda: [],
        })
    if name == "grp":
        return _module(name, {
            "getgrnam": lambda name: None,
            "getgrgid": lambda gid: None,
            "getgrall": lambda: [],
        })
    if name == "array":
        return _module(name, {
            "array": _bootstrap_array.array,
            "typecodes": _bootstrap_array.typecodes,
            "_array_reconstructor": _bootstrap_array._array_reconstructor,
        })
    if name == "math":
        values = {
            key: getattr(_bootstrap_math, key)
            for key in dir(_bootstrap_math)
            if not key.startswith("__")
        }
        values["gcd"] = _gcd
        values["float_info"] = _module("sys.float_info", {
            "max": 1.7976931348623157e308, "min": 2.2250738585072014e-308,
            "epsilon": 2.220446049250313e-16, "mant_dig": 53,
            "dig": 15, "max_exp": 1024, "min_exp": -1021,
            "max_10_exp": 308, "min_10_exp": -307, "radix": 2,
        })
        return _module(name, values)
    if name == "cmath":
        return _module(name, {
            key: getattr(_bootstrap_cmath, key)
            for key in dir(_bootstrap_cmath) if not key.startswith("__")
        })
    if name == "datetime":
        return _module(name, {
            key: getattr(_bootstrap_datetime, key)
            for key in dir(_bootstrap_datetime) if not key.startswith("__")
        })
    if name == "calendar":
        return _module(name, {
            key: getattr(_bootstrap_calendar, key)
            for key in dir(_bootstrap_calendar) if not key.startswith("__")
        })
    if name == "gc":
        return _module(name, {
            "collect": lambda generation=2: 0, "disable": lambda: None,
            "enable": lambda: None, "isenabled": lambda: True,
            "get_threshold": lambda: (700, 10, 10), "set_threshold": lambda *args: None,
            "get_count": lambda: (0, 0, 0), "freeze": lambda: None,
            "unfreeze": lambda: None, "is_tracked": lambda value: False,
            "is_finalized": lambda value: False,
        })
    if name == "unicodedata":
        return _module(name, {
            key: getattr(_bootstrap_unicodedata, key)
            for key in dir(_bootstrap_unicodedata) if not key.startswith("__")
        })
    if name == "errno":
        return _module(name, {
            key: getattr(_bootstrap_errno, key)
            for key in dir(_bootstrap_errno) if key.isupper()
        } | {
            "EPERM": 1, "ENOENT": 2, "EIO": 5, "EBADF": 9,
            "EAGAIN": 11, "ENOMEM": 12, "EACCES": 13, "EEXIST": 17,
            "ENOTDIR": 20, "EINVAL": 22, "ENOSPC": 28, "EPIPE": 32,
            "EWOULDBLOCK": 11, "ECONNRESET": 10054, "ETIMEDOUT": 10060,
        })
    if name == "time":
        return _module(name, {
            "time": _bootstrap_time.time, "monotonic": _bootstrap_time.monotonic,
            "time_ns": _bootstrap_time.time_ns, "monotonic_ns": _bootstrap_time.monotonic_ns,
            "perf_counter_ns": _bootstrap_time.perf_counter_ns, "process_time_ns": _bootstrap_time.process_time_ns,
            "perf_counter": _bootstrap_time.perf_counter, "process_time": _bootstrap_time.process_time,
            "sleep": _bootstrap_time.sleep, "ctime": _bootstrap_time.ctime,
            "gmtime": _bootstrap_time.gmtime, "localtime": _bootstrap_time.localtime,
            "strftime": _bootstrap_time.strftime, "strptime": _bootstrap_time.strptime,
            "struct_time": _bootstrap_time.struct_time, "timezone": _bootstrap_time.timezone,
            "altzone": getattr(_bootstrap_time, "altzone", 0), "daylight": _bootstrap_time.daylight,
            "tzname": _bootstrap_time.tzname,
        })
    if name == "_struct":
        return _module(name, {
            "Struct": _bootstrap_struct.Struct, "pack": _bootstrap_struct.pack,
            "unpack": _bootstrap_struct.unpack, "unpack_from": _bootstrap_struct.unpack_from,
            "pack_into": _bootstrap_struct.pack_into, "calcsize": _bootstrap_struct.calcsize,
            "iter_unpack": _bootstrap_struct.iter_unpack, "error": _bootstrap_struct.error,
            "_clearcache": lambda: None,
        })
    if name == "_string":
        return _module(name, {
            key: getattr(_bootstrap_string, key)
            for key in dir(_bootstrap_string)
            if not key.startswith("__")
        })
    if name == "_interpreters":
        class InterpreterError(Exception):
            pass
        class InterpreterNotFoundError(InterpreterError):
            pass
        class NotShareableError(InterpreterError):
            pass
        return _module(name, {
            "InterpreterID": int,
            "WHENCE_UNKNOWN": 0,
            "WHENCE_RUNTIME": 1,
            "WHENCE_LEGACY_CAPI": 2,
            "WHENCE_CAPI": 3,
            "WHENCE_XI": 4,
            "WHENCE_STDLIB": 5,
            "InterpreterError": InterpreterError,
            "InterpreterNotFoundError": InterpreterNotFoundError,
            "NotShareableError": NotShareableError,
            "is_shareable": lambda obj: True,
            "create": lambda: 0,
            "destroy": lambda interpreter: None,
            "decref": lambda interpreter: None,
            "incref": lambda interpreter: None,
            "run_string": lambda interpreter, script, shared=None: None,
            "list_all": lambda *args, **kwargs: [(0, 1)],
            "get_current": lambda: (0, 1),
            "get_main": lambda: (0, 1),
            "whence": lambda interpreter: 1,
            "get_info": lambda interpreter: SimpleNamespace(id=interpreter, whence=1),
            "is_running": lambda interpreter: False,
        })
    if name == "_interpqueues":
        class QueueError(Exception):
            pass
        class QueueNotFoundError(QueueError):
            pass
        return _module(name, {
            "QueueError": QueueError,
            "QueueNotFoundError": QueueNotFoundError,
            "create": lambda: 0,
            "destroy": lambda queue: None,
            "send": lambda *args, **kwargs: None,
            "recv": lambda *args, **kwargs: None,
            "list_all": lambda: [0],
            "_register_heap_types": lambda *args, **kwargs: None,
        })
    if name == "_sysconfig":
        return _module(name, {"config_vars": _bootstrap_sysconfig.config_vars})
    if name == "zlib":
        return _module(name, {
            key: getattr(_bootstrap_zlib, key)
            for key in dir(_bootstrap_zlib)
            if not key.startswith("__")
        })
    if name == "_asyncio":
        return _module(name, {
            "Future": object,
            "Task": object,
            "FutureIter": object,
            "future_add_to_awaited_by": lambda *args: None,
            "future_discard_from_awaited_by": lambda *args: None,
            "get_running_loop": lambda: None,
            "get_event_loop": lambda: None,
            "current_task": lambda loop=None: None,
            "all_tasks": lambda loop=None: set(),
            "_get_running_loop": lambda: None,
            "_set_running_loop": lambda loop: None,
        })
    if name == "_socket":
        return _module(name, {
            key: getattr(_bootstrap_socket, key)
            for key in dir(_bootstrap_socket)
            if not key.startswith("__")
        })
    if name == "_ssl":
        values = {
            key: getattr(_bootstrap_ssl, key)
            for key in dir(_bootstrap_ssl)
            if not key.startswith("__")
        }
        values.setdefault("_SSLMethod", int)
        return _module(name, values)
    if name == "_overlapped":
        return _module(name, {
            key: getattr(_bootstrap_overlapped, key)
            for key in dir(_bootstrap_overlapped)
            if not key.startswith("__")
        })
    if name == "_ctypes":
        # The Python ctypes layer requires a matching native ABI; exposing
        # host interpreter type objects here creates invalid pyinbin layouts.
        return None
    if name == "_bz2":
        return _module(name, {
            key: getattr(_bootstrap_bz2, key)
            for key in dir(_bootstrap_bz2)
            if not key.startswith("__")
        })
    if name == "_lzma":
        return _module(name, {
            key: getattr(_bootstrap_lzma, key)
            for key in dir(_bootstrap_lzma)
            if not key.startswith("__")
        })
    if name == "_zstd":
        values = {
            key: getattr(_bootstrap_zstd, key)
            for key in dir(_bootstrap_zstd)
            if not key.startswith("__")
        }
        values["set_parameter_types"] = lambda *args: None
        return _module(name, values)
    if name == "select":
        return _module(name, {
            key: getattr(_bootstrap_select, key)
            for key in dir(_bootstrap_select)
            if not key.startswith("__")
        })
    if name == "fcntl":
        return _module(name, {
            "ioctl": lambda *args, **kwargs: 0,
            "fcntl": lambda *args, **kwargs: 0,
            "flock": lambda *args, **kwargs: None,
        })
    if name == "msvcrt":
        return _module(name, {
            "getwch": lambda: "",
            "getwche": lambda: "",
            "kbhit": lambda: False,
            "setmode": lambda fd, mode: None,
            "get_osfhandle": lambda fd: fd,
            "open_osfhandle": lambda handle, flags: handle,
        })
    if name == "_pickle":
        return _module(name, {
            "Pickler": _bootstrap_pickle.Pickler, "Unpickler": _bootstrap_pickle.Unpickler,
            "dump": _bootstrap_pickle.dump, "dumps": _bootstrap_pickle.dumps,
            "load": _bootstrap_pickle.load, "loads": _bootstrap_pickle.loads,
            "PickleBuffer": getattr(_bootstrap_pickle, "PickleBuffer", object),
            "PickleError": getattr(_bootstrap_pickle, "PickleError", Exception),
            "PicklingError": getattr(_bootstrap_pickle, "PicklingError", Exception),
            "UnpicklingError": getattr(_bootstrap_pickle, "UnpicklingError", Exception),
            "HIGHEST_PROTOCOL": _bootstrap_pickle.HIGHEST_PROTOCOL,
            "DEFAULT_PROTOCOL": _bootstrap_pickle.DEFAULT_PROTOCOL,
        })
    if name == "_heapq":
        return _module(name, {
            "heapify": _bootstrap_heapq.heapify,
            "heappush": _bootstrap_heapq.heappush,
            "heappop": _bootstrap_heapq.heappop,
            "heapreplace": _bootstrap_heapq.heapreplace,
            "heappushpop": _bootstrap_heapq.heappushpop,
            "nlargest": _bootstrap_heapq.nlargest,
            "nsmallest": _bootstrap_heapq.nsmallest,
        })
    if name == "_bisect":
        return _module(name, {
            "bisect_left": _bootstrap_bisect.bisect_left,
            "bisect_right": _bootstrap_bisect.bisect_right,
            "insort_left": _bootstrap_bisect.insort_left,
            "insort_right": _bootstrap_bisect.insort_right,
        })
    if name == "marshal":
        return _module(name, {
            key: getattr(_bootstrap_marshal, key)
            for key in dir(_bootstrap_marshal)
            if not key.startswith("__")
        })
    if name == "binascii":
        return _module(name, {
            key: getattr(_bootstrap_binascii, key)
            for key in dir(_bootstrap_binascii)
            if not key.startswith("__")
        })
    if name == "posix":
        values = {
            key: getattr(_bootstrap_os, key)
            for key in dir(_bootstrap_os)
            if not key.startswith("__")
        }
        values.setdefault("_path_splitroot_ex", getattr(_bootstrap_os.path, "splitroot", lambda path: ("", "", path)))
        values.setdefault("_path_normpath", _bootstrap_os.path.normpath)
        return _module(name, values)
    if name == "re":
        return _module(name, {
            key: getattr(_bootstrap_re, key)
            for key in dir(_bootstrap_re)
            if not key.startswith("__")
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
            "unregister": lambda search_function: None,
            "_unregister_error": lambda name: None,
        })
    if name == "_multibytecodec":
        return _module(name, {
            key: getattr(_bootstrap_multibytecodec, key)
            for key in dir(_bootstrap_multibytecodec) if not key.startswith("__")
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
            "open": _open_compat, "open_code": _open_compat,
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
            "STD_INPUT_HANDLE": -10, "STD_OUTPUT_HANDLE": -11, "STD_ERROR_HANDLE": -12,
            "STARTF_FORCEONFEEDBACK": 0x40, "STARTF_FORCEOFFFEEDBACK": 0x80, "STARTF_USEPOSITION": 0x4,
            "STARTF_USESIZE": 0x2, "STARTF_USECOUNTCHARS": 0x8,
            "STARTF_USEFILLATTRIBUTE": 0x10, "STARTF_USEHOTKEY": 0x80,
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
            "ENABLE_SPECIALIZATION": False, "ENABLE_SPECIALIZATION_FT": False,
            "stack_effect": lambda opcode, oparg=None, *, jump=None: 0,
            "get_executor": lambda code, offset: None,
            "get_intrinsic1_descs": lambda: (),
            "get_intrinsic2_descs": lambda: (),
            "has_arg": lambda opcode: opcode >= 90,
            "has_const": lambda opcode: True, "has_name": lambda opcode: True,
            "has_jump": lambda opcode: True,
            "has_local": lambda opcode: False,
            "has_exc": lambda opcode: False,
            "has_free": lambda opcode: True,
        })
    if name == "_csv":
        return _module(name, {
            key: getattr(_bootstrap_csv, key)
            for key in dir(_bootstrap_csv) if not key.startswith("__")
        })
    if name == "_sre":
        # Bootstrap surface used while importing ``re``. Pattern execution
        # remains a VM-native milestone; these values keep source modules
        # importable without loading a host extension.
        return _module(name, {
            "MAGIC": 20230612, "CODESIZE": 2, "MAXREPEAT": (1 << 32) - 1,
            "MAXGROUPS": 100, "MAXGROUPREF": 100,
            "SRE_FLAG_TEMPLATE": 1, "SRE_FLAG_IGNORECASE": 2, "SRE_FLAG_LOCALE": 4,
            "SRE_FLAG_MULTILINE": 8, "SRE_FLAG_DOTALL": 16, "SRE_FLAG_UNICODE": 32,
            "SRE_FLAG_VERBOSE": 64, "SRE_FLAG_DEBUG": 128, "SRE_FLAG_ASCII": 256,
            "SRE_FLAG_BYTES": 512,
            "compile": lambda *args, **kwargs: _SREPattern(),
            "getcodesize": lambda: 2,
        })
    if name == "_ast":
        values = {key: value for key, value in vars(_bootstrap_ast).items() if not key.startswith("_")}
        values.update({"PyCF_ONLY_AST": 1024, "PyCF_TYPE_COMMENTS": 4096, "PyCF_ALLOW_TOP_LEVEL_AWAIT": 8192})
        return _module(name, values)
    if name == "_thread":
        class Lock:
            def __init__(self) -> None:
                self._locked = False
                self._owner = None
                self._count = 0
            def acquire(self, waitflag=True, timeout=-1):
                owner = 1
                if self._locked:
                    if self._owner == owner:
                        self._count += 1
                        return True
                    return False if waitflag is False else False
                self._locked, self._owner, self._count = True, owner, 1
                return True
            def release(self):
                if not self._locked or self._owner != 1:
                    raise RuntimeError("cannot release un-acquired lock")
                self._count -= 1
                if self._count <= 0:
                    self._locked, self._owner = False, None
            def locked(self): return self._locked
            def _is_owned(self): return self._locked and self._owner == 1
            def _release_save(self):
                if not self._is_owned(): raise RuntimeError("cannot release un-acquired lock")
                state = (self._count, self._owner)
                self._locked, self._owner, self._count = False, None, 0
                return state
            def _acquire_restore(self, state):
                self._locked, self._count, self._owner = True, state[0], state[1]
            def _at_fork_reinit(self):
                self._locked, self._owner, self._count = False, None, 0
            def __enter__(self): self.acquire(); return self
            def __exit__(self, exc_type, exc_value, traceback): self.release(); return False
        class local:
            pass
        class ExceptHookArgs(tuple):
            def __new__(cls, *args): return tuple.__new__(cls, args)
            @property
            def exc_type(self): return self[0] if len(self) > 0 else None
            @property
            def exc_value(self): return self[1] if len(self) > 1 else None
            @property
            def exc_traceback(self): return self[2] if len(self) > 2 else None
            @property
            def thread(self): return self[3] if len(self) > 3 else None
        class ThreadHandle:
            def __init__(self, result=None): self.result, self.done = result, True
            def join(self, timeout=-1): return None
            def is_done(self): return self.done
            def is_alive(self): return not self.done
            def get_exitcode(self): return 0
            def _set_done(self): self.done = True
        def start_joinable_thread(function, *args, **kwargs):
            call_args = kwargs.pop("args", ()) or (args[0] if args else ())
            result = function(*call_args)
            return ThreadHandle(result)
        return _module(name, {
            "LockType": Lock, "RLock": Lock, "allocate_lock": Lock,
            "local": local, "_local": local,
            "get_ident": lambda: 1, "get_native_id": lambda: 1,
            "_get_main_thread_ident": lambda: 1, "_is_main_interpreter": lambda: True,
            "set_name": lambda ident, name: None, "error": RuntimeError,
            "_excepthook": lambda *args, **kwargs: None,
            "_ExceptHookArgs": ExceptHookArgs,
            "_set_sentinel": lambda: Lock(),
            "stack_size": lambda size=0: 0,
            "start_new_thread": lambda function, args, kwargs=None: function(*args, **(kwargs or {})) or 1,
            "start_joinable_thread": start_joinable_thread,
            "ThreadHandle": ThreadHandle,
            "_ThreadHandle": ThreadHandle,
            "_make_thread_handle": lambda ident: ThreadHandle(),
            "_shutdown": lambda: None,
            "daemon_threads_allowed": lambda: True,
            "TIMEOUT_MAX": 1e9,
        })
    if name == "_signal":
        return _module(name, {
            "SIGINT": 2, "SIGTERM": 15, "SIGABRT": 22, "SIGBREAK": 21,
            "SIG_DFL": 0, "SIG_IGN": 1,
            "signal": lambda signum, handler: handler,
            "getsignal": lambda signum: 0,
            "set_wakeup_fd": lambda fd, *, warn_on_full_buffer=True: -1,
            "pthread_sigmask": lambda how, mask: set(),
            "sigpending": lambda: set(), "sigwait": lambda sigset: 0,
        })
    if name == "_contextvars":
        missing = object()
        current: dict[object, object] = {}
        class Token:
            MISSING = missing
            def __init__(self, var, old_value=missing):
                self.var, self.old_value, self.used = var, old_value, False
        class ContextVar:
            def __init__(self, name, *, default=missing):
                self.name, self.default = name, default
            def get(self, default=missing):
                value = current.get(self, missing)
                if value is not missing: return value
                if default is not missing: return default
                if self.default is not missing: return self.default
                raise LookupError(self.name)
            def set(self, value):
                old = current.get(self, missing)
                current[self] = value
                return Token(self, old)
            def reset(self, token):
                if token.used or token.var is not self: raise ValueError("Token has already been used")
                token.used = True
                if token.old_value is missing: current.pop(self, None)
                else: current[self] = token.old_value
        class Context:
            def __init__(self, values=None): self.values = dict(values or current)
            def copy(self): return Context(self.values)
            def run(self, callable_obj, *args, **kwargs):
                previous = dict(current); current.clear(); current.update(self.values)
                try: return callable_obj(*args, **kwargs)
                finally: self.values = dict(current); current.clear(); current.update(previous)
            def __getitem__(self, var): return self.values[var]
            def __iter__(self): return iter(self.values)
            def __len__(self): return len(self.values)
        return _module(name, {
            "Context": Context, "ContextVar": ContextVar, "Token": Token,
            "copy_context": lambda: Context(current),
        })
    if name == "_tokenize":
        class TokenizerIter:
            def __init__(self, source, encoding=None, extra_tokens=False):
                self.source = source
            def __iter__(self): return iter(())
        return _module(name, {"TokenizerIter": TokenizerIter})
    if name == "nt":
        return _module(name, {
            "name": "nt", "sep": "\\", "altsep": "/", "pathsep": ";",
            "_have_functions": (),
            "_create_environ": lambda: {}, "_exit": lambda status=0: None,
            "defpath": ".", "devnull": "NUL", "curdir": ".", "pardir": "..", "extsep": ".",
            "getcwd": lambda: ".", "getcwdb": lambda: b".", "listdir": lambda path=".": [],
            "open": lambda path, flags, mode=0o777: _bootstrap_os.open(path, flags, mode),
            "getpid": lambda: 1, "getppid": lambda: 0,
            "get_osfhandle": lambda fd: fd,
            "O_RDONLY": _bootstrap_os.O_RDONLY, "O_WRONLY": _bootstrap_os.O_WRONLY,
            "O_RDWR": _bootstrap_os.O_RDWR, "O_CREAT": _bootstrap_os.O_CREAT,
            "O_EXCL": _bootstrap_os.O_EXCL, "O_TRUNC": _bootstrap_os.O_TRUNC,
            "O_BINARY": getattr(_bootstrap_os, "O_BINARY", 0),
            "stat_result": _bootstrap_os.stat_result,
            "terminal_size": _bootstrap_os.terminal_size,
            "getwindowsversion": lambda: _WindowsVersion(),
            "cpu_count": lambda: 1, "process_cpu_count": lambda: 1,
            "_getvolumepathname": lambda path: path,
            "_path_normpath": lambda path: path,
            "_path_isdir": lambda path: False,
            "_path_isfile": lambda path: False,
            "_path_islink": lambda path: False,
            "_path_isjunction": lambda path: False,
            "_path_exists": lambda path: False,
            "_path_lexists": lambda path: False,
            "_getfullpathname": lambda path: path,
            "_findfirstfile": lambda path: -1,
            "_getfinalpathname": lambda path: path,
            "readlink": lambda path: path,
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
        chain.from_iterable = lambda iterable: chain(*iterable)
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
            "permutations": lambda iterable, r=None: _permutations(iterable, r),
            "combinations": lambda iterable, r: _combinations(iterable, r),
            "product": lambda *iterables, repeat=1: _product(*iterables, repeat=repeat),
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
            "_remove_dead_weakref": lambda *args: None,
        })
    if name == "_functools":
        class Placeholder:
            def __repr__(self): return "Placeholder"
        class Partial:
            __pyinbin_partial__ = True
            def __init__(self, function, args=(), kwargs=None):
                self.function, self.args, self.kwargs = function, tuple(args), dict(kwargs or {})
            def __call__(self, *args, **kwargs):
                return self.function(*self.args, *args, **{**self.kwargs, **kwargs})
            @property
            def func(self):
                return self.function
            @property
            def keywords(self):
                return self.kwargs
        class LRUCacheWrapper:
            __pyinbin_lru_cache__ = True
        def reduce(function, iterable, initial=None):
            iterator = iter(iterable)
            if initial is None:
                value = next(iterator)
            else:
                value = initial
            for item in iterator: value = function(value, item)
            return value
        reduce.__pyinbin_reduce__ = True
        return _module(name, {
            "reduce": reduce, "partial": lambda function, *args, **kwargs: Partial(function, args, kwargs),
            "cmp_to_key": lambda comparator: (lambda value: value),
            "_lru_cache_wrapper": LRUCacheWrapper,
            "Placeholder": Placeholder(), "_PlaceholderType": Placeholder,
        })
    return None
