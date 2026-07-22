"""Capability-aware discovery commands for backends and linkers."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .capability_negotiation import (
    component_contract,
    dependency_status,
    resolve_backend,
    resolve_linker,
)


def _backend_names() -> list[str]:
    from asmpython import _backends
    return ["legacy", "x86-64", "ternary", *_backends.registered_names()]


def _linker_names() -> list[str]:
    from asmpython import _linkers
    return ["builtin", "gcc", *_linkers.registered_names()]


def _aliases(name: str) -> list[str]:
    from asmpython import _backends
    return sorted(alias for alias, canonical in _backends.registered_aliases().items() if canonical == name)


def _record(kind: str, name: str) -> dict[str, Any]:
    component = resolve_backend(name) if kind == "backend" else resolve_linker(name)
    if component is None:
        return {"kind": kind, "name": name, "available": False}
    capabilities = component_contract(component)
    statuses = [dependency_status(item) for item in capabilities.dependencies]
    scaffold = bool(getattr(component, "is_scaffold", False))
    production = bool(getattr(component, "production_suitable", not scaffold))
    status = str(
        getattr(
            component,
            "status",
            "scaffold" if scaffold else "production" if production else "experimental",
        )
    )
    return {
        "kind": kind,
        "name": name,
        "display_name": getattr(getattr(component, "spec", None), "display_name", name),
        "available": True,
        "implemented": bool(getattr(component, "implemented", not scaffold)),
        "production_suitable": production,
        "status": status,
        "aliases": _aliases(name) if kind == "backend" else [],
        "default_linker": getattr(component, "default_linker", None) if kind == "backend" else None,
        "module": type(component).__module__,
        "capabilities": capabilities.as_dict(),
        "dependencies": [item.as_dict() for item in statuses],
        "dependencies_ready": all(item.available or item.dependency.optional for item in statuses),
    }


def records(kind: str) -> list[dict[str, Any]]:
    names = _backend_names() if kind == "backend" else _linker_names()
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(_record(kind, name))
    return result


def command_main(kind: str, argv: list[str]) -> int:
    plural = "backends" if kind == "backend" else "linkers"
    parser = argparse.ArgumentParser(prog=f"asmpython {plural}")
    parser.add_argument("component", nargs="?", default="list")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    items = records(kind)
    if args.component == "list":
        if args.json:
            print(json.dumps(items, indent=2, sort_keys=True, ensure_ascii=False))
            return 0
        print(f"{'NAME':<20} {'STATUS':<13} {'PRODUCTION':<11} DEPENDENCIES")
        for item in items:
            print(
                f"{item['name']:<20} {item['status']:<13} "
                f"{('yes' if item['production_suitable'] else 'no'):<11} "
                f"{'ready' if item['dependencies_ready'] else 'missing'}"
            )
        return 0
    selected = next(
        (
            item
            for item in items
            if args.component == item["name"] or args.component in item.get("aliases", [])
        ),
        None,
    )
    if selected is None:
        print(f"asmpython: unknown {kind} {args.component!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(selected, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    print(f"{kind.title()}: {selected['display_name']} ({selected['name']})")
    print(f"status: {selected['status']}")
    print(f"implemented: {'yes' if selected['implemented'] else 'no'}")
    print(f"production suitable: {'yes' if selected['production_suitable'] else 'no'}")
    if kind == "backend":
        print(f"aliases: {', '.join(selected['aliases']) or '-'}")
        print(f"default linker: {selected['default_linker'] or '-'}")
    capabilities = selected["capabilities"]
    print(f"targets: {', '.join(capabilities.get('targets', [])) or '-'}")
    print(f"output types: {', '.join(capabilities.get('output_types', [])) or '-'}")
    print(f"sanitizers: {', '.join(capabilities.get('sanitizers', [])) or '-'}")
    print(f"debug formats: {', '.join(capabilities.get('debug_formats', [])) or '-'}")
    print(f"features: {', '.join(capabilities.get('features', [])) or '-'}")
    dependencies = selected["dependencies"]
    if dependencies:
        print("dependencies:")
        for item in dependencies:
            state = "available" if item["available"] else "MISSING"
            optional = " optional" if item.get("optional") else ""
            version = f" {item['version']}" if item.get("version") else ""
            print(f"  {item['kind']}:{item['name']}{version} — {state}{optional}")
            if item.get("location"):
                print(f"    {item['location']}")
    return 0 if selected["dependencies_ready"] else 1


__all__ = ["command_main", "records"]
