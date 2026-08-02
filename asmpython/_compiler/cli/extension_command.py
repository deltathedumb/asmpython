"""CLI for packaging and managing ``.apext`` extensions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..packaging.extension_packages import (
    ExtensionPackageError,
    SCOPES,
    get_extension,
    install_extension,
    list_installed,
    package_extension,
    scope_path,
    uninstall_extension,
)


def _add_scope_flags(parser: argparse.ArgumentParser, *, default: str | None) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--system", action="store_const", const="system", dest="scope")
    group.add_argument("--user", action="store_const", const="user", dest="scope")
    group.add_argument("--local", action="store_const", const="local", dest="scope")
    parser.set_defaults(scope=default)


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="asmpython extension",
        description="Package, install, download, inspect, or remove ASMPython extensions.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    install_p = sub.add_parser("install")
    install_p.add_argument("package", type=Path)
    _add_scope_flags(install_p, default="user")
    install_p.add_argument("--directory", type=Path, default=None)
    install_p.add_argument("--json", action="store_true")

    package_p = sub.add_parser("package")
    package_p.add_argument("target", help="extension descriptor in module:object form")
    package_p.add_argument("-o", "--output", type=Path, default=None)
    package_p.add_argument("--root", type=Path, default=None)

    uninstall_p = sub.add_parser("uninstall")
    uninstall_p.add_argument("id")
    _add_scope_flags(uninstall_p, default=None)
    uninstall_p.add_argument("--directory", type=Path, default=None)

    get_p = sub.add_parser("get")
    get_p.add_argument("url")
    _add_scope_flags(get_p, default="user")
    get_p.add_argument("--directory", type=Path, default=None)
    get_p.add_argument("--sha256", default=None)
    get_p.add_argument("--allow-http", action="store_true")
    get_p.add_argument("--json", action="store_true")

    list_p = sub.add_parser("list")
    list_p.add_argument("--directory", type=Path, default=None)
    list_p.add_argument("--json", action="store_true")

    path_p = sub.add_parser("path")
    path_p.add_argument("scope", choices=SCOPES)
    path_p.add_argument("--directory", type=Path, default=None)

    args = parser.parse_args(argv)
    try:
        if args.action == "package":
            output = package_extension(args.target, root=args.root, output=args.output)
            print(output)
            return 0
        if args.action == "install":
            installed = install_extension(
                args.package, scope=args.scope, directory=args.directory
            )
            result = {
                "id": installed.id,
                "version": installed.version,
                "scope": installed.scope,
                "path": str(installed.path),
                "production_suitable": installed.production_suitable,
            }
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(
                    f"asmpython: installed extension {installed.id} "
                    f"{installed.version} ({installed.scope}) -> {installed.path}"
                )
            return 0
        if args.action == "get":
            installed = get_extension(
                args.url,
                scope=args.scope,
                directory=args.directory,
                expected_sha256=args.sha256,
                allow_http=args.allow_http,
            )
            result = {
                "id": installed.id,
                "version": installed.version,
                "scope": installed.scope,
                "path": str(installed.path),
                "production_suitable": installed.production_suitable,
            }
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(
                    f"asmpython: downloaded and installed extension {installed.id} "
                    f"{installed.version} ({installed.scope}) -> {installed.path}"
                )
            return 0
        if args.action == "uninstall":
            removed = uninstall_extension(
                args.id, scope=args.scope, directory=args.directory
            )
            if not removed:
                print(f"asmpython: extension {args.id!r} is not installed", file=sys.stderr)
                return 1
            for path in removed:
                print(f"asmpython: removed {path}")
            return 0
        if args.action == "path":
            print(scope_path(args.scope, args.directory))
            return 0
        installed = list_installed(args.directory)
        records = [
            {
                "id": item.id,
                "version": item.version,
                "scope": item.scope,
                "path": str(item.path),
                "production_suitable": item.production_suitable,
            }
            for item in installed
        ]
        if args.json:
            print(json.dumps(records, indent=2, sort_keys=True))
        else:
            print(f"{'ID':<28} {'VERSION':<12} {'SCOPE':<8} PRODUCTION")
            for item in installed:
                print(
                    f"{item.id:<28} {item.version:<12} {item.scope:<8} "
                    f"{'yes' if item.production_suitable else 'no'}"
                )
        return 0
    except (OSError, ExtensionPackageError, ValueError) as exc:
        print(f"asmpython: extension: {exc}", file=sys.stderr)
        return 1


__all__ = ["command_main"]
