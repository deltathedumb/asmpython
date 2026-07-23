"""Native ABI manifests and compatibility checks."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_verify import verify_artifact


ABI_FORMAT = "asmpython.abi"
ABI_VERSION = 1


class AbiToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class AbiSymbol:
    name: str
    kind: str = "unknown"
    binding: str = "global"
    calling_convention: str | None = None
    signature: str | None = None
    ownership: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "binding": self.binding,
            "calling_convention": self.calling_convention,
            "signature": self.signature,
            "ownership": self.ownership,
        }


def _run(command: list[str]) -> str:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise AbiToolError(f"failed to run {command[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise AbiToolError(proc.stderr.strip() or f"{command[0]} exited {proc.returncode}")
    return proc.stdout


def _symbols_from_nm(path: Path) -> list[AbiSymbol]:
    nm = shutil.which("llvm-nm") or shutil.which("nm")
    if nm is None:
        return []
    output = _run([nm, "-g", "--defined-only", str(path)])
    symbols: dict[str, AbiSymbol] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if len(parts) >= 3:
            code, name = parts[-2], parts[-1]
        else:
            code, name = "?", parts[-1]
        if name.startswith((".", "$")):
            continue
        kind = "function" if code.upper() in {"T", "W"} else "data"
        symbols[name] = AbiSymbol(name=name, kind=kind)
    return [symbols[name] for name in sorted(symbols)]


def _symbols_from_dumpbin(path: Path) -> list[AbiSymbol]:
    dumpbin = shutil.which("dumpbin")
    if dumpbin is None:
        return []
    output = _run([dumpbin, "/nologo", "/exports", str(path)])
    symbols: dict[str, AbiSymbol] = {}
    active = False
    for line in output.splitlines():
        if "ordinal" in line.lower() and "name" in line.lower():
            active = True
            continue
        if not active:
            continue
        match = re.match(r"\s*\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+(\S+)", line)
        if match:
            name = match.group(1)
            symbols[name] = AbiSymbol(name=name, kind="function")
    return [symbols[name] for name in sorted(symbols)]


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    candidates = [
        path if path.suffix == ".json" else path.with_suffix(path.suffix + ".abi.json"),
        path.with_suffix(".abi.json"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AbiToolError(f"cannot read ABI manifest {candidate}: {exc}") from exc
        if payload.get("format") != ABI_FORMAT:
            continue
        return payload
    return None


def dump_abi(path: Path) -> dict[str, Any]:
    existing = _load_sidecar(path)
    if existing is not None:
        return existing
    verification = verify_artifact(path)
    if not verification.valid:
        raise AbiToolError("cannot inspect invalid artifact: " + "; ".join(verification.errors))
    symbols = _symbols_from_dumpbin(path) if verification.format == "pe" else _symbols_from_nm(path)
    if not symbols:
        raise AbiToolError(
            "no exported symbols were found; install llvm-nm/nm or dumpbin, or provide an .abi.json sidecar"
        )
    return {
        "format": ABI_FORMAT,
        "format_version": ABI_VERSION,
        "artifact": str(path),
        "artifact_format": verification.format,
        "architecture": verification.architecture,
        "abi_version": "1",
        "symbols": [symbol.as_dict() for symbol in symbols],
        "types": [],
    }


def write_abi(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _symbol_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []):
        if isinstance(item, dict) and item.get("name"):
            result[str(item["name"])] = item
    return result


def diff_abi(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_symbols = _symbol_map(old)
    new_symbols = _symbol_map(new)
    removed = sorted(set(old_symbols) - set(new_symbols))
    added = sorted(set(new_symbols) - set(old_symbols))
    changed: list[dict[str, Any]] = []
    for name in sorted(set(old_symbols) & set(new_symbols)):
        before = old_symbols[name]
        after = new_symbols[name]
        differences = {
            key: {"old": before.get(key), "new": after.get(key)}
            for key in sorted(set(before) | set(after)) - {"name"}
            if before.get(key) != after.get(key)
        }
        if differences:
            changed.append({"name": name, "changes": differences})
    breaking = bool(removed or changed)
    return {
        "compatible": not breaking,
        "breaking": breaking,
        "removed": removed,
        "added": added,
        "changed": changed,
        "old_abi_version": old.get("abi_version"),
        "new_abi_version": new.get("abi_version"),
    }


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="asmpython abi")
    sub = parser.add_subparsers(dest="action", required=True)
    dump = sub.add_parser("dump")
    dump.add_argument("artifact", type=Path)
    dump.add_argument("--output", type=Path, default=None)
    dump.add_argument("--json", action="store_true")
    diff = sub.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    diff.add_argument("--json", action="store_true")
    check = sub.add_parser("check")
    check.add_argument("artifact", type=Path)
    check.add_argument("--against", required=True, type=Path)
    check.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "dump":
            payload = dump_abi(args.artifact)
            output = args.output or args.artifact.with_suffix(args.artifact.suffix + ".abi.json")
            write_abi(output, payload)
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
            else:
                print(f"asmpython: wrote ABI manifest {output}")
                print(f"symbols: {len(payload.get('symbols', []))}")
            return 0
        if args.action == "diff":
            result = diff_abi(dump_abi(args.old), dump_abi(args.new))
        else:
            result = diff_abi(dump_abi(args.against), dump_abi(args.artifact))
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(f"ABI compatible: {'yes' if result['compatible'] else 'NO'}")
            for name in result["removed"]:
                print(f"  removed: {name}")
            for item in result["changed"]:
                print(f"  changed: {item['name']}")
            for name in result["added"]:
                print(f"  added: {name}")
        return 0 if result["compatible"] else 1
    except (OSError, AbiToolError) as exc:
        print(f"asmpython: abi: {exc}", file=sys.stderr)
        return 1


__all__ = ["ABI_FORMAT", "AbiToolError", "command_main", "diff_abi", "dump_abi", "write_abi"]
