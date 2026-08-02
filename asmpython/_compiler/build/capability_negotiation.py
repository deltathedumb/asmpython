"""Preflight compatibility and dependency checks for backends and linkers."""
from __future__ import annotations

import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from asmpython.capabilities import CapabilitySet, Dependency
from .build_options import active_sanitizers, speedy_lossy_enabled


SUPPORTED_COMPONENT_API = 1


class CapabilityNegotiationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DependencyStatus:
    dependency: Dependency
    available: bool
    location: str | None = None
    detected_version: str | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.dependency.as_dict(),
            "available": self.available,
            "location": self.location,
            "detected_version": self.detected_version,
            "message": self.message,
        }


@dataclass(frozen=True)
class ComponentResult:
    kind: str
    name: str
    capabilities: CapabilitySet
    dependencies: tuple[DependencyStatus, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "compatible": self.compatible,
            "capabilities": self.capabilities.as_dict(),
            "dependencies": [item.as_dict() for item in self.dependencies],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class NegotiationResult:
    backend: ComponentResult
    linker: ComponentResult | None
    target: str
    output_type: str
    sanitizers: tuple[str, ...]
    speedy_lossy: bool

    @property
    def errors(self) -> tuple[str, ...]:
        items = list(self.backend.errors)
        if self.linker is not None:
            items.extend(self.linker.errors)
        return tuple(items)

    @property
    def warnings(self) -> tuple[str, ...]:
        items = list(self.backend.warnings)
        if self.linker is not None:
            items.extend(self.linker.warnings)
        return tuple(items)

    @property
    def compatible(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "target": self.target,
            "output_type": self.output_type,
            "sanitizers": list(self.sanitizers),
            "speedy_lossy": self.speedy_lossy,
            "backend": self.backend.as_dict(),
            "linker": None if self.linker is None else self.linker.as_dict(),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _option_value(argv: list[str], flag: str) -> str | None:
    value: str | None = None
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith(flag + "="):
            value = token.split("=", 1)[1]
    return value


def _matches(value: str, patterns: Iterable[str]) -> bool:
    patterns = tuple(patterns)
    return not patterns or "*" in patterns or any(
        fnmatch.fnmatchcase(value.lower(), pattern.lower()) for pattern in patterns
    )


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text)
    return tuple(int(item) for item in numbers[:4])


def _version_satisfies(detected: str | None, requirement: str | None) -> bool:
    if requirement is None or not requirement.strip():
        return True
    if detected is None:
        return False
    requirement = requirement.strip()
    match = re.fullmatch(r"(>=|<=|==|>|<)?\s*([0-9][0-9A-Za-z._-]*)", requirement)
    if match is None:
        return detected == requirement
    operator = match.group(1) or "=="
    wanted = _version_tuple(match.group(2))
    actual = _version_tuple(detected)
    if operator == ">=":
        return actual >= wanted
    if operator == "<=":
        return actual <= wanted
    if operator == ">":
        return actual > wanted
    if operator == "<":
        return actual < wanted
    return actual == wanted


def _probe_executable(path: str, dependency: Dependency) -> str | None:
    command = list(dependency.probe) if dependency.probe else [path, "--version"]
    if command and command[0] == dependency.name:
        command[0] = path
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0] if text else None


def dependency_status(dependency: Dependency) -> DependencyStatus:
    kind = dependency.kind
    name = dependency.name
    location: str | None = None
    detected_version: str | None = None
    available = False

    if kind == "executable":
        location = shutil.which(name)
        available = location is not None
        if available and dependency.version:
            detected_version = _probe_executable(location, dependency)
    elif kind == "python-module":
        available = importlib.util.find_spec(name) is not None
        if available and dependency.version:
            try:
                module = __import__(name)
                detected_version = str(getattr(module, "__version__", "")) or None
            except Exception:
                detected_version = None
    elif kind == "environment":
        value = os.environ.get(name)
        available = bool(value)
        location = value
    elif kind == "file":
        path = Path(name).expanduser()
        available = path.exists()
        location = str(path.resolve()) if available else str(path)
    elif kind == "extension":
        from asmpython.extension import get_extension
        extension = get_extension(name)
        available = extension is not None
        if extension is not None:
            detected_version = extension.version
    elif kind == "backend":
        from asmpython import _backends
        available = _backends.get_backend(name) is not None or name in {
            "legacy", "x86-64", "ternary",
        }
    elif kind == "linker":
        from asmpython import _linkers
        available = _linkers.get_linker(name) is not None or name in {"gcc", "builtin"}

    version_ok = _version_satisfies(detected_version, dependency.version)
    if available and dependency.version and not version_ok:
        available = False
        message = (
            f"detected {detected_version or 'unknown version'}, requires {dependency.version}"
        )
    elif available:
        message = "available"
    else:
        message = "missing"
    return DependencyStatus(
        dependency=dependency,
        available=available,
        location=location,
        detected_version=detected_version,
        message=message,
    )


def component_contract(component: object) -> CapabilitySet:
    capabilities = getattr(component, "capabilities", None)
    dependencies = getattr(component, "dependencies", ())
    return CapabilitySet.from_value(capabilities, dependencies=dependencies)


def _special_backend(name: str) -> object | None:
    if name == "legacy":
        return SimpleNamespace(
            default_linker="gcc",
            capabilities=CapabilitySet(
                targets=("*",),
                output_types=("*",),
                sanitizers=("*",),
                debug_formats=("dwarf", "pdb"),
                features=("native", "assembly"),
                speedy_lossy=True,
                abi=True,
                ffi=True,
                dependencies=(
                    Dependency.executable("nasm", optional=True, reason="NASM code generation"),
                    Dependency.executable("gcc", optional=True, reason="default native linker"),
                ),
            ),
        )
    if name == "x86-64":
        return SimpleNamespace(
            default_linker="builtin",
            capabilities=CapabilitySet(
                targets=("windows-x64", "linux-x64", "*"),
                output_types=("executable", "object", "shared-library", "static-library", "*"),
                sanitizers=("*",),
                debug_formats=("dwarf", "pdb"),
                features=("native", "ssa-ir", "abi", "ffi"),
                speedy_lossy=True,
                abi=True,
                ffi=True,
            ),
        )
    if name == "ternary":
        return SimpleNamespace(
            default_linker=None,
            capabilities=CapabilitySet(
                targets=("ternary",),
                output_types=("object",),
                speedy_lossy=False,
            ),
        )
    return None


def _special_linker(name: str) -> object | None:
    if name == "gcc":
        return SimpleNamespace(
            capabilities=CapabilitySet(
                targets=("*",),
                output_types=("executable", "shared-library", "static-library", "*"),
                sanitizers=("address", "bounds", "integer", "leak", "memory", "thread", "undefined"),
                debug_formats=("dwarf", "pdb"),
                features=("native-link", "sanitizer-runtime"),
                speedy_lossy=True,
                abi=True,
                ffi=True,
                dependencies=(Dependency.executable("gcc", reason="native linker driver"),),
            ),
        )
    if name == "builtin":
        return SimpleNamespace(
            capabilities=CapabilitySet(
                targets=("windows-x64", "linux-x64", "*"),
                output_types=("executable", "*"),
                sanitizers=(),
                debug_formats=(),
                features=("native-link", "no-external-toolchain"),
                speedy_lossy=True,
                abi=True,
                ffi=True,
            ),
        )
    return None


def resolve_backend(name: str) -> object | None:
    special = _special_backend(name)
    if special is not None:
        return special
    from asmpython import _backends
    return _backends.get_backend(name)


def resolve_linker(name: str) -> object | None:
    special = _special_linker(name)
    if special is not None:
        return special
    from asmpython import _linkers
    return _linkers.get_linker(name)


def negotiate_component(
    kind: str,
    name: str,
    component: object | None,
    *,
    target: str,
    output_type: str,
    sanitizers: tuple[str, ...],
    speedy_lossy: bool,
) -> ComponentResult:
    if component is None:
        empty = CapabilitySet()
        return ComponentResult(
            kind, name, empty, (), (f"unknown {kind} {name!r}",), ()
        )
    capabilities = component_contract(component)
    errors: list[str] = []
    warnings: list[str] = []
    if capabilities.api_version > SUPPORTED_COMPONENT_API:
        errors.append(
            f"{kind} {name!r} requires component API {capabilities.api_version}; "
            f"this ASMPython supports {SUPPORTED_COMPONENT_API}"
        )
    if not _matches(target, capabilities.targets):
        errors.append(
            f"{kind} {name!r} does not support target {target!r}; supports "
            + ", ".join(capabilities.targets)
        )
    if not _matches(output_type, capabilities.output_types):
        errors.append(
            f"{kind} {name!r} cannot produce {output_type!r}; supports "
            + ", ".join(capabilities.output_types)
        )
    if sanitizers and "*" not in capabilities.sanitizers:
        missing = sorted(set(sanitizers) - set(capabilities.sanitizers))
        if missing:
            errors.append(
                f"{kind} {name!r} does not support sanitizer(s): " + ", ".join(missing)
            )
    if speedy_lossy and not capabilities.speedy_lossy:
        errors.append(f"{kind} {name!r} does not support --speedy-lossy")

    statuses = tuple(dependency_status(item) for item in capabilities.dependencies)
    for status in statuses:
        if status.available:
            continue
        text = (
            f"{kind} {name!r} dependency {status.dependency.kind}:"
            f"{status.dependency.name} is unavailable"
        )
        if status.dependency.version:
            text += f" ({status.message})"
        if status.dependency.reason:
            text += f" — {status.dependency.reason}"
        if status.dependency.optional:
            warnings.append(text)
        else:
            errors.append(text)
    return ComponentResult(
        kind=kind,
        name=name,
        capabilities=capabilities,
        dependencies=statuses,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def negotiate_build(argv: list[str]) -> NegotiationResult:
    backend_name = _option_value(argv, "--backend") or "legacy"
    backend = resolve_backend(backend_name)
    target = _option_value(argv, "--target") or "host"
    output_type = _option_value(argv, "--type") or "executable"
    sanitizers = active_sanitizers()
    speedy_lossy = speedy_lossy_enabled()
    backend_result = negotiate_component(
        "backend",
        backend_name,
        backend,
        target=target,
        output_type=output_type,
        sanitizers=sanitizers,
        speedy_lossy=speedy_lossy,
    )
    linker_name = _option_value(argv, "--linker")
    if linker_name is None and backend is not None:
        linker_name = getattr(backend, "default_linker", None)
    linker_result = None
    if linker_name:
        linker_result = negotiate_component(
            "linker",
            linker_name,
            resolve_linker(linker_name),
            target=target,
            output_type=output_type,
            sanitizers=sanitizers,
            speedy_lossy=speedy_lossy,
        )
    return NegotiationResult(
        backend=backend_result,
        linker=linker_result,
        target=target,
        output_type=output_type,
        sanitizers=sanitizers,
        speedy_lossy=speedy_lossy,
    )


def enforce_build_capabilities(argv: list[str]) -> NegotiationResult:
    result = negotiate_build(argv)
    if result.errors:
        raise CapabilityNegotiationError("\n".join(result.errors))
    return result


__all__ = [
    "CapabilityNegotiationError",
    "ComponentResult",
    "DependencyStatus",
    "NegotiationResult",
    "component_contract",
    "dependency_status",
    "enforce_build_capabilities",
    "negotiate_build",
    "negotiate_component",
    "resolve_backend",
    "resolve_linker",
]
