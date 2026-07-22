"""Artifact format, integrity, resource, signature, and ABI verification."""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embedded_data import EmbeddedDataError, read_resources


@dataclass
class VerificationResult:
    path: Path
    format: str
    architecture: str | None = None
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": self.format,
            "architecture": self.architecture,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }


def _identify(data: bytes, path: Path) -> tuple[str, str | None]:
    if data.startswith(b"MZ"):
        architecture = None
        if len(data) >= 0x40:
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if offset + 6 <= len(data) and data[offset : offset + 4] == b"PE\0\0":
                machine = struct.unpack_from("<H", data, offset + 4)[0]
                architecture = {
                    0x014C: "x86",
                    0x8664: "x86_64",
                    0x01C0: "arm",
                    0xAA64: "aarch64",
                    0x5064: "riscv64",
                }.get(machine, f"machine-0x{machine:04x}")
        return "pe", architecture
    if data.startswith(b"\x7fELF"):
        architecture = None
        if len(data) >= 20:
            endian = "<" if data[5:6] == b"\x01" else ">"
            machine = struct.unpack_from(endian + "H", data, 18)[0]
            architecture = {
                3: "x86",
                62: "x86_64",
                40: "arm",
                183: "aarch64",
                243: "riscv",
                8: "mips",
                20: "powerpc",
                21: "powerpc64",
            }.get(machine, f"machine-{machine}")
        return "elf", architecture
    if data[:4] in {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    }:
        return "mach-o", None
    if data.startswith(b"\x00asm"):
        return "webassembly", "wasm"
    if data.startswith(b"!<arch>\n"):
        return "archive", None
    if data.startswith(b"PK\x03\x04"):
        if path.suffix.lower() == ".jar":
            return "jar", "jvm"
        if path.suffix.lower() == ".apext":
            return "apext", None
        return "zip", None
    if path.suffix.lower() == ".pyc" and len(data) >= 16:
        return "python-bytecode", "cpython-vm"
    return "unknown", None


def verify_artifact(path: Path) -> VerificationResult:
    path = path.expanduser()
    if not path.is_file():
        return VerificationResult(path, "missing", valid=False, errors=["artifact does not exist"])
    try:
        data = path.read_bytes()
    except OSError as exc:
        return VerificationResult(path, "unreadable", valid=False, errors=[str(exc)])
    format_name, architecture = _identify(data, path)
    result = VerificationResult(path, format_name, architecture)
    result.details["bytes"] = len(data)

    if format_name == "unknown":
        result.error("unrecognized executable/library/container format")
    elif format_name == "pe":
        if len(data) < 0x40:
            result.error("truncated DOS/PE header")
        else:
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if offset + 24 > len(data) or data[offset : offset + 4] != b"PE\0\0":
                result.error("missing or truncated PE signature")
            else:
                sections = struct.unpack_from("<H", data, offset + 6)[0]
                result.details["sections"] = sections
    elif format_name == "elf":
        if len(data) < 64:
            result.error("truncated ELF header")
        else:
            result.details["class"] = 64 if data[4] == 2 else 32
            result.details["endianness"] = "little" if data[5] == 1 else "big"
    elif format_name == "webassembly":
        if len(data) < 8 or data[4:8] != b"\x01\x00\x00\x00":
            result.error("unsupported or truncated WebAssembly header")
    elif format_name in {"zip", "jar", "apext"}:
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                result.details["members"] = len(archive.infolist())
                if bad:
                    result.error(f"ZIP member failed CRC verification: {bad}")
                if format_name == "jar" and "META-INF/MANIFEST.MF" not in archive.namelist():
                    result.warnings.append("JAR has no META-INF/MANIFEST.MF")
        except (OSError, zipfile.BadZipFile) as exc:
            result.error(f"invalid ZIP container: {exc}")
        if format_name == "apext" and result.valid:
            try:
                from .extension_packages import read_manifest
                manifest = read_manifest(path, verify=True)
                result.details["extension"] = {
                    "id": manifest.get("id"),
                    "version": manifest.get("version"),
                    "api_version": manifest.get("api_version"),
                }
            except Exception as exc:
                result.error(f"invalid .apext manifest: {exc}")

    try:
        resources = read_resources(path)
    except EmbeddedDataError as exc:
        result.error(str(exc))
    else:
        result.details["embedded"] = {
            "files": len(resources),
            "bytes": sum(len(value) for value in resources.values()),
            "names": sorted(resources),
        }

    signature = path.with_suffix(path.suffix + ".apsig")
    if signature.is_file():
        try:
            from .package_signing import verify_signature
            verified = verify_signature(path, signature)
            result.details["signature"] = verified
            if not verified.get("valid", False):
                result.error("detached package signature is invalid")
        except Exception as exc:
            result.error(f"cannot verify detached signature: {exc}")

    abi_sidecar = path.with_suffix(path.suffix + ".abi.json")
    if abi_sidecar.is_file():
        try:
            abi = json.loads(abi_sidecar.read_text(encoding="utf-8"))
            result.details["abi"] = {
                "format_version": abi.get("format_version"),
                "symbols": len(abi.get("symbols", [])),
            }
        except (OSError, ValueError) as exc:
            result.error(f"malformed ABI sidecar: {exc}")
    return result


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="asmpython verify")
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = [verify_artifact(path) for path in args.artifacts]
    if args.json:
        payload: Any = [item.as_dict() for item in results]
        if len(payload) == 1:
            payload = payload[0]
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for item in results:
            state = "valid" if item.valid else "INVALID"
            arch = f"/{item.architecture}" if item.architecture else ""
            print(f"{item.path}: {state} {item.format}{arch}")
            for warning in item.warnings:
                print(f"  warning: {warning}")
            for error in item.errors:
                print(f"  error: {error}", file=sys.stderr)
            embedded = item.details.get("embedded", {})
            if embedded.get("files"):
                print(
                    f"  embedded: {embedded['files']} files, {embedded['bytes']} bytes"
                )
    return 0 if all(item.valid for item in results) else 1


__all__ = ["VerificationResult", "command_main", "verify_artifact"]
