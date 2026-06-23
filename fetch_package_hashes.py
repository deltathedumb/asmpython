#!/usr/bin/env python3
"""fetch_package_hashes.py — maintainer tool: verify (and optionally fill in)
the `sha` field for every version entry in package-repository.json.

For each package/version, downloads the `url`, computes its sha256, and
compares it against the recorded `sha`:

  - missing `sha`           -> warning, with the computed hash shown so it
                                can be pasted into the registry
  - `sha` present, mismatch -> loud warning (corrupted upload, or the
                                registry's hash is simply wrong)
  - `sha` present, match    -> OK

Usage:
    python fetch_package_hashes.py                  # audit only
    python fetch_package_hashes.py --write           # fill in missing hashes
    python fetch_package_hashes.py --write --force   # also overwrite mismatches
    python fetch_package_hashes.py path/to/other.json

Exit code is 0 only if every version had a matching sha (after any --write).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "package-repository.json"


def _sha256_of_url(url: str, *, timeout: int = 120) -> str:
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "registry",
        type=Path,
        nargs="?",
        default=DEFAULT_PATH,
        help=f"registry JSON to audit (default: {DEFAULT_PATH.name})",
    )
    ap.add_argument(
        "--write", action="store_true", help="fill in missing `sha` fields in place"
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="with --write, also overwrite mismatched `sha` fields",
    )
    args = ap.parse_args(argv)

    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"fetch_package_hashes: cannot read {args.registry}: {e}", file=sys.stderr)
        return 2

    problems = 0
    changed = False

    for pkg_name, pkg in registry.items():
        versions = pkg.get("versions") or {}
        for version, info in versions.items():
            label = f"{pkg_name} {version}"
            url = info.get("url")
            if not url:
                print(f"WARN     {label}: no url, skipping")
                problems += 1
                continue
            try:
                actual = _sha256_of_url(url)
            except (urllib.error.URLError, OSError) as e:
                print(f"ERROR    {label}: download failed ({e})")
                problems += 1
                continue

            expected = info.get("sha")
            if not expected:
                print(f"WARN     {label}: missing sha — computed {actual}")
                problems += 1
                if args.write:
                    info["sha"] = actual
                    changed = True
            elif expected.lower() != actual.lower():
                print(f"MISMATCH {label}: recorded {expected}, computed {actual}")
                problems += 1
                if args.write and args.force:
                    info["sha"] = actual
                    changed = True
            else:
                print(f"OK       {label}")

    if changed:
        args.registry.write_text(json.dumps(registry, indent=4) + "\n", encoding="utf-8")
        print(f"\nUpdated {args.registry}")

    if problems:
        print(f"\n{problems} issue(s) found.")
        return 1
    print("\nAll versions have matching checksums.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
