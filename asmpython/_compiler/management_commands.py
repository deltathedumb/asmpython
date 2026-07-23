"""Management command dispatch for the public ASMPython CLI."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import cache_manager
from .profiles import (
    ProfileError,
    SCOPES,
    delete_profile,
    get_profile,
    list_profiles,
    modify_profile,
    parse_assignment,
    profile_path,
    profile_to_argv,
    resolve_profile,
    save_profile,
)

MANAGEMENT_COMMANDS = frozenset({"backends", "ir", "cache", "profile", "test"})
REMOVED_COMMANDS = {
    "invalidate": "asmpython cache clear",
    "irbuild": "asmpython ir <irname>",
}


def _backend_record(name: str, backend: object, aliases: list[str]) -> dict[str, Any]:
    spec = getattr(backend, "spec", None)
    scaffold = bool(getattr(backend, "is_scaffold", False))
    status = "scaffold" if scaffold else str(getattr(backend, "status", "registered"))
    return {
        "name": name,
        "display_name": getattr(spec, "display_name", name),
        "status": status,
        "implemented": bool(getattr(backend, "implemented", not scaffold)),
        "category": getattr(spec, "category", getattr(backend, "category", "compiler")),
        "aliases": sorted(aliases),
        "planned_parameters": list(getattr(backend, "planned_parameters", ())),
        "requested_args": list(getattr(backend, "requested_args", [])),
        "default_linker": getattr(backend, "default_linker", None),
        "notes": getattr(spec, "notes", ""),
        "module": type(backend).__module__,
    }


def _backend_records() -> list[dict[str, Any]]:
    from asmpython import _backends

    aliases = _backends.registered_aliases()
    inverse: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        inverse.setdefault(canonical, []).append(alias)

    records = [
        {
            "name": "legacy", "display_name": "Legacy NASM", "status": "legacy",
            "implemented": True, "category": "cpu", "aliases": [],
            "planned_parameters": [], "requested_args": [], "default_linker": "gcc",
            "notes": "Compatibility and inline-assembly backend.",
            "module": "asmpython._compiler.codegen",
        },
        {
            "name": "x86-64", "display_name": "x86-64", "status": "production",
            "implemented": True, "category": "cpu", "aliases": ["x64", "amd64"],
            "planned_parameters": [], "requested_args": [], "default_linker": "builtin",
            "notes": "Production SSA IR native backend.",
            "module": "asmpython._backends.x86_64",
        },
        {
            "name": "ternary", "display_name": "Ternary", "status": "experimental",
            "implemented": True, "category": "virtual-machine", "aliases": [],
            "planned_parameters": [], "requested_args": [], "default_linker": None,
            "notes": "Experimental uASM-related ternary target.",
            "module": "asmpython._backends.ternary",
        },
    ]
    for name in _backends.registered_names():
        backend = _backends.get_backend(name)
        if backend is not None:
            records.append(_backend_record(name, backend, inverse.get(name, [])))
    return records


def backends_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="asmpython backends",
        description="List backends or show detailed information about one backend.",
    )
    parser.add_argument("backend", nargs="?", default="list")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    records = _backend_records()

    if args.backend == "list":
        if args.json:
            print(json.dumps(records, indent=2, sort_keys=True))
            return 0
        print(f"{'BACKEND':<20} {'STATUS':<13} {'CATEGORY':<22} ALIASES")
        for record in records:
            print(
                f"{record['name']:<20} {record['status']:<13} "
                f"{record['category']:<22} {', '.join(record['aliases']) or '-'}"
            )
        return 0

    selected = None
    for record in records:
        if args.backend == record["name"] or args.backend in record["aliases"]:
            selected = record
            break
    if selected is None:
        print(f"asmpython: unknown backend {args.backend!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(selected, indent=2, sort_keys=True))
        return 0
    print(f"Backend: {selected['display_name']} ({selected['name']})")
    print(f"status: {selected['status']}")
    print(f"implemented: {'yes' if selected['implemented'] else 'no'}")
    print(f"category: {selected['category']}")
    print(f"aliases: {', '.join(selected['aliases']) or '-'}")
    print(f"default linker: {selected['default_linker'] or '-'}")
    print(f"module: {selected['module']}")
    parameters = selected["planned_parameters"]
    if parameters:
        print("parameters:")
        for parameter in parameters:
            print(f"  {parameter}")
    requested = selected["requested_args"]
    if requested:
        print("registered arguments:")
        for argument in requested:
            print(f"  {argument}")
    if selected["notes"]:
        print(f"notes: {selected['notes']}")
    return 0


def cache_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="asmpython cache")
    parser.add_argument("action", nargs="?", choices=["status", "clear", "verify", "prune", "path"], default="status")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--max-age", type=float, default=None, metavar="DAYS")
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.cache_dir or cache_manager.default_cache_dir()
    try:
        if args.action == "path":
            print(root)
            return 0
        if args.action == "clear":
            removed = cache_manager.clear_cache(root, source=args.source, key=args.key)
            print(f"asmpython: removed {removed} cache entr{'y' if removed == 1 else 'ies'}")
            return 0
        if args.action == "verify":
            valid, invalid = cache_manager.verify_cache(root, repair=args.repair)
            result = {"path": str(root), "valid": valid, "invalid": invalid, "repaired": args.repair and invalid}
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"cache: {root}")
                print(f"valid: {valid}")
                print(f"invalid: {invalid}")
                if args.repair:
                    print(f"removed invalid: {invalid}")
            return 0 if invalid == 0 or args.repair else 1
        if args.action == "prune":
            removed, reclaimed = cache_manager.prune_cache(
                root, max_age_days=args.max_age, max_bytes=args.max_bytes
            )
            print(f"asmpython: pruned {removed} entries; reclaimed {cache_manager.format_size(reclaimed)}")
            return 0

        entries = cache_manager.scan_cache(root)
        total = sum(entry.size for entry in entries)
        invalid = sum(not entry.valid for entry in entries)
        result = {
            "path": str(root), "entries": len(entries), "bytes": total,
            "invalid": invalid,
            "items": [
                {
                    "key": entry.path.name, "bytes": entry.size,
                    "modified": entry.modified, "valid": entry.valid,
                    "error": entry.error,
                    "kind": (entry.manifest or {}).get("kind"),
                    "source": (entry.manifest or {}).get("source_path"),
                }
                for entry in entries
            ],
        }
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"cache: {root}")
            print(f"entries: {len(entries)}")
            print(f"size: {cache_manager.format_size(total)}")
            print(f"invalid: {invalid}")
            for entry in entries:
                age = max(0, time.time() - entry.modified) if entry.modified else 0
                print(
                    f"  {entry.path.name:<34} {cache_manager.format_size(entry.size):>10} "
                    f"{age / 86400:7.1f}d {'ok' if entry.valid else entry.error}"
                )
        return 0
    except (OSError, CacheError) as exc:
        print(f"asmpython: cache: {exc}", file=sys.stderr)
        return 1


def _assignments(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        key, parsed = parse_assignment(value)
        result[key] = parsed
    return result


def profile_main(argv: list[str]) -> int:
    raw = list(argv)
    if not raw:
        raw = ["list"]
    elif raw[0] not in {"list", "show", "create", "modify", "delete", "path"}:
        raw.insert(0, "show")

    parser = argparse.ArgumentParser(prog="asmpython profile")
    sub = parser.add_subparsers(dest="action", required=True)
    list_p = sub.add_parser("list")
    list_p.add_argument("--scope", choices=SCOPES, default=None)
    list_p.add_argument("--directory", type=Path, default=None)
    list_p.add_argument("--json", action="store_true")

    show_p = sub.add_parser("show")
    show_p.add_argument("name")
    show_p.add_argument("--directory", type=Path, default=None)
    show_p.add_argument("--json", action="store_true")

    for action in ("create", "modify"):
        item = sub.add_parser(action)
        item.add_argument("name")
        item.add_argument("--scope", choices=SCOPES, default="directory")
        item.add_argument("--directory", type=Path, default=None)
        item.add_argument("--set", dest="sets", action="append", default=[])
        item.add_argument("--unset", action="append", default=[])
        item.add_argument("--extends", default=None)
        item.add_argument("--description", default=None)

    delete_p = sub.add_parser("delete")
    delete_p.add_argument("name")
    delete_p.add_argument("--scope", choices=SCOPES, default="directory")
    delete_p.add_argument("--directory", type=Path, default=None)

    path_p = sub.add_parser("path")
    path_p.add_argument("--scope", choices=SCOPES, default="directory")
    path_p.add_argument("--directory", type=Path, default=None)

    args = parser.parse_args(raw)
    try:
        if args.action == "path":
            print(profile_path(args.scope, args.directory))
            return 0
        if args.action == "list":
            profiles = list_profiles(scope=args.scope, directory=args.directory)
            if args.json:
                print(json.dumps({name: [location.scope for location in locations] for name, locations in profiles.items()}, indent=2, sort_keys=True))
            else:
                if not profiles:
                    print("No ASMPython profiles are defined.")
                for name, locations in sorted(profiles.items()):
                    print(f"{name:<24} {', '.join(location.scope for location in locations)}")
            return 0
        if args.action == "show":
            resolved = resolve_profile(args.name, directory=args.directory)
            scopes = {
                scope: value for scope in SCOPES
                if (value := get_profile(args.name, scope=scope, directory=args.directory)) is not None
            }
            result = {"name": args.name, "resolved": resolved, "scopes": scopes}
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"Profile: {args.name}")
                print(f"defined in: {', '.join(scopes)}")
                for key, value in sorted(resolved.items()):
                    print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            return 0
        if args.action == "delete":
            path = delete_profile(args.name, scope=args.scope, directory=args.directory)
            print(f"asmpython: deleted profile {args.name!r} from {path}")
            return 0

        values = _assignments(args.sets)
        if args.extends is not None:
            values["extends"] = args.extends
        if args.description is not None:
            values["description"] = args.description
        if args.action == "create":
            if args.unset:
                raise ProfileError("--unset is only valid with profile modify")
            path = save_profile(
                args.name, values, scope=args.scope, directory=args.directory,
                create_only=True,
            )
            print(f"asmpython: created profile {args.name!r} in {path}")
            return 0
        path = modify_profile(
            args.name, values, unset=args.unset,
            scope=args.scope, directory=args.directory,
        )
        print(f"asmpython: modified profile {args.name!r} in {path}")
        return 0
    except (OSError, ProfileError) as exc:
        print(f"asmpython: profile: {exc}", file=sys.stderr)
        return 1


def extract_profile_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Remove repeated ``--profile`` flags and return their names."""
    remaining: list[str] = []
    names: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--profile":
            if index + 1 >= len(argv):
                raise ProfileError("--profile requires a name")
            names.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("--profile="):
            names.append(token.split("=", 1)[1])
            index += 1
            continue
        remaining.append(token)
        index += 1
    return remaining, names


def apply_build_profiles(argv: list[str]) -> list[str]:
    remaining, names = extract_profile_args(argv)
    if not names:
        return remaining
    first = remaining[0] if remaining else ""
    is_build = first == "build" or first not in MANAGEMENT_COMMANDS | {"package", "pypi", "pyinbin", "project"}
    if not is_build:
        raise ProfileError("--profile is currently valid for build invocations")
    injected: list[str] = []
    for name in names:
        injected.extend(profile_to_argv(resolve_profile(name)))
    if remaining and remaining[0] == "build":
        return ["build", *injected, *remaining[1:]]
    return [*injected, *remaining]


def dispatch(argv: list[str]) -> int | None:
    if not argv:
        return None
    command, rest = argv[0], argv[1:]
    if command == "backends":
        return backends_main(rest)
    if command == "cache":
        return cache_main(rest)
    if command == "profile":
        return profile_main(rest)
    if command == "ir":
        from .ir_command import command_main
        return command_main(rest)
    if command == "test":
        from .test_runner import command_main
        return command_main(rest)
    return None
