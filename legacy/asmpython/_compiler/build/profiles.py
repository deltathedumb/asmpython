"""Scoped ASMPython build profiles."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE_SCHEMA = 1
SCOPES = ("system", "user", "directory")
SUPPORTED_KEYS = frozenset({
    "target", "output", "output_type", "type", "backend", "linker",
    "bundle_mode", "use_runtime_lib", "no_pyinbin_fallback", "keep",
    "keep_assembly", "emit_asm", "icon", "nasm", "gcc", "apm",
    "speedy_lossy", "bleach", "sanitize", "sanitizers", "report",
    "fastcomp", "debug", "debug_format", "embed", "locked", "lockfile",
    "graphonly", "graph_format", "graph_output",
})


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileLocation:
    scope: str
    path: Path


def _system_profile_path() -> Path:
    override = os.environ.get("ASMPYTHON_SYSTEM_PROFILES")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return root / "ASMPython" / "profiles.json"
    return Path("/etc/asmpython/profiles.json")


def _user_profile_path() -> Path:
    override = os.environ.get("ASMPYTHON_USER_PROFILES")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "ASMPython" / "profiles.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "asmpython" / "profiles.json"


def _directory_profile_path(directory: Path | None = None) -> Path:
    return (directory or Path.cwd()).resolve() / ".asmpython" / "profiles.json"


def profile_path(scope: str, directory: Path | None = None) -> Path:
    if scope == "system":
        return _system_profile_path()
    if scope == "user":
        return _user_profile_path()
    if scope == "directory":
        return _directory_profile_path(directory)
    raise ProfileError(f"unknown profile scope {scope!r}; choose system, user, or directory")


def _empty_document() -> dict[str, Any]:
    return {"schema": PROFILE_SCHEMA, "profiles": {}}


def _load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_document()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"cannot read profile store {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != PROFILE_SCHEMA:
        raise ProfileError(f"unsupported profile document in {path}")
    if not isinstance(document.get("profiles"), dict):
        raise ProfileError(f"profile store {path} has no object-valued 'profiles' field")
    return document


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ProfileError(f"cannot write profile store {path}: {exc}") from exc


def _validate_name(name: str) -> None:
    if not name or name in {".", ".."} or any(ch in name for ch in "/\\\0"):
        raise ProfileError("invalid profile name")


def _validate_values(values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - SUPPORTED_KEYS - {"extends", "description"})
    if unknown:
        raise ProfileError("unsupported profile key(s): " + ", ".join(unknown))
    return dict(values)


def list_profiles(*, scope: str | None = None, directory: Path | None = None) -> dict[str, list[ProfileLocation]]:
    found: dict[str, list[ProfileLocation]] = {}
    for item_scope in ((scope,) if scope is not None else SCOPES):
        path = profile_path(item_scope, directory)
        for name in _load_document(path)["profiles"]:
            found.setdefault(name, []).append(ProfileLocation(item_scope, path))
    return found


def get_profile(name: str, *, scope: str, directory: Path | None = None) -> dict[str, Any] | None:
    _validate_name(name)
    path = profile_path(scope, directory)
    value = _load_document(path)["profiles"].get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProfileError(f"profile {name!r} in {path} is not an object")
    return dict(value)


def save_profile(
    name: str,
    values: dict[str, Any],
    *,
    scope: str,
    directory: Path | None = None,
    create_only: bool = False,
    modify_only: bool = False,
) -> Path:
    _validate_name(name)
    values = _validate_values(values)
    path = profile_path(scope, directory)
    document = _load_document(path)
    exists = name in document["profiles"]
    if create_only and exists:
        raise ProfileError(f"profile {name!r} already exists at {scope} scope")
    if modify_only and not exists:
        raise ProfileError(f"profile {name!r} does not exist at {scope} scope")
    document["profiles"][name] = values
    _write_document(path, document)
    return path


def modify_profile(
    name: str,
    updates: dict[str, Any],
    *,
    unset: list[str] | None = None,
    scope: str,
    directory: Path | None = None,
) -> Path:
    current = get_profile(name, scope=scope, directory=directory)
    if current is None:
        raise ProfileError(f"profile {name!r} does not exist at {scope} scope")
    for key in unset or []:
        current.pop(key, None)
    current.update(updates)
    return save_profile(name, current, scope=scope, directory=directory, modify_only=True)


def delete_profile(name: str, *, scope: str, directory: Path | None = None) -> Path:
    _validate_name(name)
    path = profile_path(scope, directory)
    document = _load_document(path)
    if name not in document["profiles"]:
        raise ProfileError(f"profile {name!r} does not exist at {scope} scope")
    del document["profiles"][name]
    _write_document(path, document)
    return path


def resolve_profile(name: str, *, directory: Path | None = None) -> dict[str, Any]:
    def resolve(current: str, stack: tuple[str, ...]) -> dict[str, Any]:
        if current in stack:
            raise ProfileError("profile inheritance cycle: " + " -> ".join((*stack, current)))
        merged: dict[str, Any] = {}
        seen = False
        for scope in SCOPES:
            value = get_profile(current, scope=scope, directory=directory)
            if value is None:
                continue
            seen = True
            parent = value.pop("extends", None)
            if parent is not None:
                if not isinstance(parent, str):
                    raise ProfileError(f"profile {current!r}: extends must be a profile name")
                merged.update(resolve(parent, (*stack, current)))
            merged.update(value)
        if not seen:
            raise ProfileError(f"unknown profile {current!r}")
        return merged
    return resolve(name, ())


def parse_assignment(text: str) -> tuple[str, Any]:
    key, separator, raw = text.partition("=")
    if not separator or not key:
        raise ProfileError(f"expected KEY=VALUE, got {text!r}")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    _validate_values({key: value})
    return key, value


def _string_list(value: Any, *, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProfileError(f"{key} must be a string or list of strings")
    return value


def profile_to_argv(profile: dict[str, Any]) -> list[str]:
    result: list[str] = []

    def add_value(flag: str, value: Any) -> None:
        if value is not None:
            result.extend((flag, str(value)))

    target = profile.get("target")
    if isinstance(target, list):
        if len(target) != 3:
            raise ProfileError("target arrays must contain platform, system, and ABI")
        target = "-".join(str(item) for item in target)
    add_value("--target", target)
    add_value("-o", profile.get("output"))
    add_value("--type", profile.get("output_type", profile.get("type")))
    add_value("--backend", profile.get("backend"))
    add_value("--linker", profile.get("linker"))
    add_value("--icon", profile.get("icon"))
    add_value("--nasm", profile.get("nasm"))
    add_value("--gcc", profile.get("gcc"))
    add_value("--report", profile.get("report"))
    add_value("--debug-format", profile.get("debug_format"))
    add_value("--lockfile", profile.get("lockfile"))
    add_value("--graph-format", profile.get("graph_format"))
    add_value("--graph-output", profile.get("graph_output"))

    bundle = profile.get("bundle_mode")
    if bundle == "onefile":
        result.append("--onefile")
    elif bundle == "onedir":
        result.append("--onedir")
    elif bundle is not None:
        raise ProfileError("bundle_mode must be 'onefile' or 'onedir'")

    boolean_flags = {
        "use_runtime_lib": "--use-runtime-lib",
        "no_pyinbin_fallback": "--no-pyinbin-fallback",
        "keep": "--keep",
        "keep_assembly": "--keep-assembly",
        "emit_asm": "--emit-asm",
        "speedy_lossy": "--speedy-lossy",
        "bleach": "--bleach",
        "fastcomp": "--fastcomp",
        "debug": "--debug",
        "locked": "--locked",
        "graphonly": "--graphonly",
    }
    for key, flag in boolean_flags.items():
        if profile.get(key) is True:
            result.append(flag)

    for item in _string_list(profile.get("sanitizers", profile.get("sanitize")), key="sanitizers"):
        add_value("--sanitize", item)
    for item in _string_list(profile.get("embed"), key="embed"):
        add_value("--embed", item)
    for item in _string_list(profile.get("apm"), key="apm"):
        add_value("--apm", item)
    return result
