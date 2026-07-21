"""Target-independent IR freezing for ASMPython.

The compiler currently uses its typed AST as the stable target-independent IR.
This module serializes that graph in two forms:

* ``bin``: a versioned APIR container with a fast ``marshal`` payload.
* ``json``: a structured, human-readable representation for tooling/debugging.

The container deliberately stores compiler metadata and component hashes so it
can also serve as the front-end cache used by fast compilation.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import marshal
import struct
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
    module_name, sep, qualname = path.partition(":")
    if not sep:
        raise ValueError(f"invalid symbol path in frozen IR: {path!r}")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in qualname.split("."):
        value = getattr(value, part)
    return value


def _iter_slots(obj: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if hasattr(obj, "__dict__"):
        attrs.update(vars(obj))
    for cls in type(obj).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in ("__dict__", "__weakref__") or name in attrs:
                continue
            if hasattr(obj, name):
                attrs[name] = getattr(obj, name)
    return attrs


def _to_data(root: Any) -> Any:
    """Convert an arbitrary ASMPython IR object graph to primitive data.

    ``$id``/``$ref`` entries preserve shared references and cycles. Object type
    names are explicit, making JSON useful for IR inspection and schema tools.
    """

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

        oid = id(obj)
        if oid in memo:
            return {"$ref": memo[oid]}
        node_id = next_id
        next_id += 1
        memo[oid] = node_id

        if isinstance(obj, list):
            return {"$id": node_id, "$kind": "list", "items": [visit(v) for v in obj]}
        if isinstance(obj, tuple):
            return {"$id": node_id, "$kind": "tuple", "items": [visit(v) for v in obj]}
        if isinstance(obj, set):
            return {"$id": node_id, "$kind": "set", "items": [visit(v) for v in obj]}
        if isinstance(obj, frozenset):
            return {"$id": node_id, "$kind": "frozenset", "items": [visit(v) for v in obj]}
        if isinstance(obj, dict):
            return {
                "$id": node_id,
                "$kind": "dict",
                "items": [[visit(k), visit(v)] for k, v in obj.items()],
            }

        attrs = _iter_slots(obj)
        if not attrs:
            raise TypeError(
                f"cannot freeze IR object {type(obj).__module__}.{type(obj).__qualname__}: "
                "no serializable attributes"
            )
        return {
            "$id": node_id,
            "$kind": "object",
            "class": _symbol_path(type(obj)),
            "attrs": {name: visit(value) for name, value in attrs.items()},
        }

    return visit(root)


def _from_data(root: Any) -> Any:
    memo: dict[int, Any] = {}

    def visit(node: Any) -> Any:
        if node is None or isinstance(node, (bool, int, float, str)):
            return node
        if not isinstance(node, dict):
            raise ValueError(f"invalid frozen IR node: {node!r}")
        if "$ref" in node:
            ref = int(node["$ref"])
            if ref not in memo:
                raise ValueError(f"forward/corrupt IR reference {ref}")
            return memo[ref]

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
            out: list[Any] = []
            memo[node_id] = out
            out.extend(visit(v) for v in node["items"])
            return out
        if kind == "dict":
            out_dict: dict[Any, Any] = {}
            memo[node_id] = out_dict
            for key, value in node["items"]:
                out_dict[visit(key)] = visit(value)
            return out_dict
        if kind == "set":
            out_set: set[Any] = set()
            memo[node_id] = out_set
            out_set.update(visit(v) for v in node["items"])
            return out_set
        if kind == "tuple":
            out_tuple = tuple(visit(v) for v in node["items"])
            memo[node_id] = out_tuple
            return out_tuple
        if kind == "frozenset":
            out_frozen = frozenset(visit(v) for v in node["items"])
            memo[node_id] = out_frozen
            return out_frozen
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
    encoded = _to_data(value)
    raw = json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def component_hashes(module: Any) -> dict[str, str]:
    """Hash independently replaceable IR components for cache diagnostics."""

    hashes: dict[str, str] = {}
    hashes["<module>"] = _canonical_hash(getattr(module, "body", []))
    for func in getattr(module, "funcs", []):
        hashes[f"function:{func.name}"] = _canonical_hash(func)
    for cls in getattr(module, "classes", []):
        class_shell = {
            "name": getattr(cls, "name", ""),
            "parent": getattr(cls, "parent", None),
            "class_vars": getattr(cls, "class_vars", []),
            "decorators": getattr(cls, "decorators", []),
        }
        hashes[f"class:{cls.name}"] = _canonical_hash(class_shell)
        for method in getattr(cls, "methods", []):
            hashes[f"method:{cls.name}.{method.name}"] = _canonical_hash(method)
    return hashes


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compile_ir(
    source: str,
    source_path: Path,
    *,
    stage: str = "optimized",
    whole_program: bool = True,
    all_errors: bool = False,
) -> FrozenIR:
    """Run the front end to the requested target-independent stage."""

    if stage not in {"parsed", "typed", "optimized"}:
        raise ValueError("--ir-stage must be parsed, typed, or optimized")

    source_path = source_path.resolve()
    source_dir = source_path.parent
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
            source_dir=source_dir,
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
        "stage": stage,
        "source_path": str(source_path),
        "source_sha256": _source_hash(source),
        "passes": passes,
        "components": component_hashes(module),
    }
    return FrozenIR(module=module, metadata=metadata)


def dump_ir(frozen: FrozenIR, path: Path, *, output: str = "bin") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _to_data(frozen.module)
    if output == "json":
        document = {
            "format": "asmpython-ir",
            "format_version": FORMAT_VERSION,
            "metadata": frozen.metadata,
            "ir": data,
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        return path
    if output != "bin":
        raise ValueError("--ir-output must be bin or json")

    metadata_bytes = json.dumps(
        frozen.metadata, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = marshal.dumps(data, marshal.version)
    digest = hashlib.sha256(payload).digest()
    header = _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        _CODEC_MARSHAL,
        0,
        len(metadata_bytes),
        len(payload),
        digest,
    )
    path.write_bytes(header + metadata_bytes + payload)
    return path


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
        metadata_raw = raw[start : start + metadata_len]
        payload = raw[start + metadata_len : start + metadata_len + payload_len]
        if len(payload) != payload_len or hashlib.sha256(payload).digest() != digest:
            raise ValueError(f"corrupt ASMPython IR payload: {path}")
        metadata = json.loads(metadata_raw.decode("utf-8"))
        data = marshal.loads(payload)
        return FrozenIR(module=_from_data(data), metadata=metadata)

    document = json.loads(raw.decode("utf-8"))
    if document.get("format") != "asmpython-ir":
        raise ValueError(f"not an ASMPython IR document: {path}")
    version = int(document.get("format_version", 0))
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported ASMPython IR version {version}")
    return FrozenIR(module=_from_data(document["ir"]), metadata=document["metadata"])


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
        source,
        source_path,
        stage=stage,
        whole_program=whole_program,
        all_errors=all_errors,
    )
    dump_ir(frozen, output_path, output=output)
    return frozen
