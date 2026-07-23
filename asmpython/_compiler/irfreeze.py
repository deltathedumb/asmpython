"""Target-independent IR freezing for ASMPython.

``bin`` uses a versioned APIR container with a marshal payload for fast cache
loading. ``json`` stores the same object graph in structured form for inspection
and development tooling.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import marshal
import struct
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

from .. import __version__

MAGIC = b"APIR\x00"
FORMAT_VERSION = 1
_CODEC_MARSHAL = 1
_HEADER = struct.Struct("<5sHBBII32s")
_ALLOWED_CLASS_PREFIXES = ("asmpython.",)


@dataclass(frozen=True)
class FrozenIR:
    module: Any
    metadata: dict[str, Any]


def _symbol_path(obj: Any) -> str:
    return f"{obj.__module__}:{obj.__qualname__}"


def _resolve_symbol(path: str) -> Any:
    module_name, separator, qualname = path.partition(":")
    if not separator:
        raise ValueError(f"invalid symbol path in frozen IR: {path!r}")
    value: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        value = getattr(value, part)
    return value


def _iter_slots(obj: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if hasattr(obj, "__dict__"):
        attributes.update(vars(obj))
    for cls in type(obj).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in ("__dict__", "__weakref__") or name in attributes:
                continue
            if hasattr(obj, name):
                attributes[name] = getattr(obj, name)
    return attributes


def to_data(root: Any) -> Any:
    """Convert an ASMPython object graph to primitive, reference-aware data."""

    memo: dict[int, int] = {}
    next_id = 1

    def visit(obj: Any) -> Any:
        nonlocal next_id
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, bytes):
            return {"$kind": "bytes", "data": base64.b64encode(obj).decode("ascii")}
        if isinstance(obj, Path):
            return {"$kind": "path", "value": str(obj)}
        if isinstance(obj, Enum):
            return {"$kind": "enum", "class": _symbol_path(type(obj)), "name": obj.name}
        if isinstance(obj, type):
            return {"$kind": "class-ref", "class": _symbol_path(obj)}
        if isinstance(obj, ModuleType):
            return {"$kind": "module-ref", "module": obj.__name__}
        if callable(obj) and hasattr(obj, "__module__") and hasattr(obj, "__qualname__"):
            return {"$kind": "symbol-ref", "symbol": _symbol_path(obj)}

        identity = id(obj)
        if identity in memo:
            return {"$ref": memo[identity]}
        node_id = next_id
        next_id += 1
        memo[identity] = node_id

        if isinstance(obj, list):
            return {"$id": node_id, "$kind": "list", "items": [visit(value) for value in obj]}
        if isinstance(obj, tuple):
            return {"$id": node_id, "$kind": "tuple", "items": [visit(value) for value in obj]}
        if isinstance(obj, set):
            return {"$id": node_id, "$kind": "set", "items": [visit(value) for value in obj]}
        if isinstance(obj, frozenset):
            return {"$id": node_id, "$kind": "frozenset", "items": [visit(value) for value in obj]}
        if isinstance(obj, dict):
            return {
                "$id": node_id,
                "$kind": "dict",
                "items": [[visit(key), visit(value)] for key, value in obj.items()],
            }

        attributes = _iter_slots(obj)
        if not attributes:
            raise TypeError(
                f"cannot freeze IR object {type(obj).__module__}.{type(obj).__qualname__}: "
                "no serializable attributes"
            )
        return {
            "$id": node_id,
            "$kind": "object",
            "class": _symbol_path(type(obj)),
            "attrs": {name: visit(value) for name, value in attributes.items()},
        }

    return visit(root)


def from_data(root: Any) -> Any:
    memo: dict[int, Any] = {}

    def visit(node: Any) -> Any:
        if node is None or isinstance(node, (bool, int, float, str)):
            return node
        if not isinstance(node, dict):
            raise ValueError(f"invalid frozen IR node: {node!r}")
        if "$ref" in node:
            reference = int(node["$ref"])
            if reference not in memo:
                raise ValueError(f"forward/corrupt IR reference {reference}")
            return memo[reference]

        kind = node.get("$kind")
        if kind == "bytes":
            return base64.b64decode(node["data"])
        if kind == "path":
            return Path(node["value"])
        if kind == "enum":
            cls = _resolve_symbol(node["class"])
            return cls[node["name"]]
        if kind == "class-ref":
            return _resolve_symbol(node["class"])
        if kind == "module-ref":
            return importlib.import_module(node["module"])
        if kind == "symbol-ref":
            return _resolve_symbol(node["symbol"])

        node_id = int(node["$id"])
        if kind == "list":
            result: list[Any] = []
            memo[node_id] = result
            result.extend(visit(value) for value in node["items"])
            return result
        if kind == "dict":
            result_dict: dict[Any, Any] = {}
            memo[node_id] = result_dict
            for key, value in node["items"]:
                result_dict[visit(key)] = visit(value)
            return result_dict
        if kind == "set":
            result_set: set[Any] = set()
            memo[node_id] = result_set
            result_set.update(visit(value) for value in node["items"])
            return result_set
        if kind == "tuple":
            result_tuple = tuple(visit(value) for value in node["items"])
            memo[node_id] = result_tuple
            return result_tuple
        if kind == "frozenset":
            result_frozen = frozenset(visit(value) for value in node["items"])
            memo[node_id] = result_frozen
            return result_frozen
        if kind == "object":
            class_path = str(node["class"])
            module_name = class_path.partition(":")[0]
            if not module_name.startswith(_ALLOWED_CLASS_PREFIXES):
                raise ValueError(f"refusing to load non-ASMPython IR class {class_path!r}")
            cls = _resolve_symbol(class_path)
            obj = cls.__new__(cls)
            memo[node_id] = obj
            for name, value in node["attrs"].items():
                object.__setattr__(obj, name, visit(value))
            return obj
        raise ValueError(f"unknown frozen IR node kind {kind!r}")

    return visit(root)


def _canonical_hash(value: Any) -> str:
    encoded = to_data(value)
    raw = json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def component_hashes(module: Any) -> dict[str, str]:
    hashes: dict[str, str] = {}
    hashes["<module>"] = _canonical_hash(getattr(module, "body", []))
    for function in getattr(module, "funcs", []):
        hashes[f"function:{function.name}"] = _canonical_hash(function)
    for cls in getattr(module, "classes", []):
        shell = {
            "name": getattr(cls, "name", ""),
            "parent": getattr(cls, "parent", None),
            "class_vars": getattr(cls, "class_vars", []),
            "decorators": getattr(cls, "decorators", []),
        }
        hashes[f"class:{cls.name}"] = _canonical_hash(shell)
        for method in getattr(cls, "methods", []):
            hashes[f"method:{cls.name}.{method.name}"] = _canonical_hash(method)
    return hashes


def compile_ir(
    source: str,
    source_path: Path,
    *,
    stage: str = "optimized",
    whole_program: bool = True,
    all_errors: bool = False,
) -> FrozenIR:
    if stage not in {"parsed", "typed", "optimized"}:
        raise ValueError("IR stage must be parsed, typed, or optimized")

    source_path = source_path.resolve()
    if stage == "parsed":
        if whole_program:
            from .program import load_program
            module = load_program(source, source_path)
        else:
            from .lexer import Lexer
            from .parser import Parser
            module = Parser(Lexer(source).tokenize()).parse()
        passes: list[str] = []
    else:
        from .driver import _compile_program
        module = _compile_program(
            source,
            source_dir=source_path.parent,
            entry_path=source_path,
            whole_program=whole_program,
            all_errors=all_errors,
        )
        passes = ["semantic-analysis"]
        if stage == "optimized":
            passes.append("canonical-typed-ir")

    metadata: dict[str, Any] = {
        "format": "asmpython-ir",
        "format_version": FORMAT_VERSION,
        "compiler_version": __version__,
        "python_cache_tag": sys.implementation.cache_tag,
        "marshal_version": marshal.version,
        "stage": stage,
        "source_path": str(source_path),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "passes": passes,
        "components": component_hashes(module),
    }
    return FrozenIR(module=module, metadata=metadata)


def dump_ir(frozen: FrozenIR, path: Path, *, output: str = "bin") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = to_data(frozen.module)
    if output == "json":
        document = {
            "format": "asmpython-ir",
            "format_version": FORMAT_VERSION,
            "metadata": frozen.metadata,
            "ir": encoded,
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        return path
    if output != "bin":
        raise ValueError("IR output must be bin or json")

    metadata_bytes = json.dumps(
        frozen.metadata, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = marshal.dumps(encoded, marshal.version)
    digest = hashlib.sha256(payload).digest()
    header = _HEADER.pack(
        MAGIC, FORMAT_VERSION, _CODEC_MARSHAL, 0,
        len(metadata_bytes), len(payload), digest,
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(header + metadata_bytes + payload)
    temporary.replace(path)
    return path


def inspect_ir(path: Path) -> dict[str, Any]:
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(MAGIC):
        if len(raw) < _HEADER.size:
            raise ValueError(f"truncated ASMPython IR file: {path}")
        magic, version, codec, flags, metadata_len, payload_len, digest = _HEADER.unpack_from(raw)
        start = _HEADER.size
        metadata_raw = raw[start:start + metadata_len]
        payload = raw[start + metadata_len:start + metadata_len + payload_len]
        if magic != MAGIC or version != FORMAT_VERSION:
            raise ValueError(f"unsupported ASMPython IR version {version}")
        if codec != _CODEC_MARSHAL:
            raise ValueError(f"unsupported ASMPython IR codec {codec}")
        if len(payload) != payload_len or hashlib.sha256(payload).digest() != digest:
            raise ValueError(f"corrupt ASMPython IR payload: {path}")
        metadata = json.loads(metadata_raw.decode("utf-8"))
        return {
            "path": str(path), "encoding": "bin", "flags": flags,
            "payload_bytes": payload_len, "metadata": metadata,
        }
    document = json.loads(raw.decode("utf-8"))
    if document.get("format") != "asmpython-ir":
        raise ValueError(f"not an ASMPython IR document: {path}")
    return {
        "path": str(path), "encoding": "json",
        "payload_bytes": len(raw), "metadata": document.get("metadata", {}),
    }


def load_ir(path: Path) -> FrozenIR:
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(MAGIC):
        if len(raw) < _HEADER.size:
            raise ValueError(f"truncated ASMPython IR file: {path}")
        magic, version, codec, _flags, metadata_len, payload_len, digest = _HEADER.unpack_from(raw)
        if magic != MAGIC or version != FORMAT_VERSION:
            raise ValueError(f"unsupported ASMPython IR version {version}")
        if codec != _CODEC_MARSHAL:
            raise ValueError(f"unsupported ASMPython IR codec {codec}")
        start = _HEADER.size
        metadata_raw = raw[start:start + metadata_len]
        payload = raw[start + metadata_len:start + metadata_len + payload_len]
        if len(payload) != payload_len or hashlib.sha256(payload).digest() != digest:
            raise ValueError(f"corrupt ASMPython IR payload: {path}")
        metadata = json.loads(metadata_raw.decode("utf-8"))
        cache_tag = metadata.get("python_cache_tag")
        if cache_tag and cache_tag != sys.implementation.cache_tag:
            raise ValueError(
                f"binary frozen IR was written for {cache_tag}, not "
                f"{sys.implementation.cache_tag}; use JSON IR for cross-Python interchange"
            )
        return FrozenIR(module=from_data(marshal.loads(payload)), metadata=metadata)

    document = json.loads(raw.decode("utf-8"))
    if document.get("format") != "asmpython-ir":
        raise ValueError(f"not an ASMPython IR document: {path}")
    version = int(document.get("format_version", 0))
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported ASMPython IR version {version}")
    return FrozenIR(module=from_data(document["ir"]), metadata=document["metadata"])


def freeze_source(
    source: str,
    source_path: Path,
    output_path: Path,
    *,
    stage: str = "optimized",
    output: str = "bin",
    whole_program: bool = True,
    all_errors: bool = False,
) -> FrozenIR:
    frozen = compile_ir(
        source, source_path, stage=stage,
        whole_program=whole_program, all_errors=all_errors,
    )
    dump_ir(frozen, output_path, output=output)
    return frozen
