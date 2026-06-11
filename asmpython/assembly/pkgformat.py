"""The ``.asmpkg`` assembly-package format and its loader.

An assembly package is the unit `include("name")` pulls in. It bundles a chunk
of hand-written NASM together with a manifest describing what it exports, so the
compiler can wire calls to it and the assembler/linker can resolve the symbols.

Layout
------
A package is a directory named ``<name>.asmpkg`` (a sibling of the source file,
or under a directory on the include path)::

    mathx.asmpkg/
        manifest.txt        # required: metadata + exported symbols
        mathx.asm           # one or more NASM source files

A single-file form ``<name>.asmpkg`` (a plain text file) is also accepted: it is
read as a manifest whose ``asm:`` lines carry inline NASM. Directories are the
normal case; the single-file form is handy for tiny packages.

Manifest grammar
----------------
Line-oriented, ``#`` starts a comment, blank lines ignored. Recognised keys::

    name:        mathx                 # package identity (should match dir)
    version:     0.1.0                 # informational
    freestanding: false               # true => no libc assumed (foundation
                                       #         for asmpython --freestanding)
    asm:         mathx.asm             # a NASM file to assemble (repeatable);
                                       #   relative to the package directory
    export:      isqrt(int) -> int     # an exported symbol + its signature
    export:      memzero(int, int)     # no `-> T` means returns int/void

`export` signatures use the same scalar vocabulary as the rest of asmpython
(`int`, `float`, `str`); they let the compiler type call sites and pick the
right ABI registers. The symbol name is the NASM label the package defines.

This module only *parses and locates* packages. Emitting their asm into the
final program is the codegen's job; resolving a name to a path is done here so
sema and codegen share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


ASMPKG_SUFFIX = ".asmpkg"


@dataclass
class AsmExport:
    """One symbol an assembly package makes callable from asmpython code."""

    symbol: str
    arg_types: tuple[str, ...] = ()
    ret_type: str = "int"


@dataclass
class AsmPackage:
    """A parsed ``.asmpkg``: its metadata, its NASM, and what it exports."""

    name: str
    path: Path
    version: str = "0.0.0"
    freestanding: bool = False
    exports: dict[str, AsmExport] = field(default_factory=dict)
    # Concatenated NASM text from every `asm:` file plus inline `asm:` blocks.
    asm_text: str = ""


class AsmPkgError(Exception):
    """Raised when a package can't be found or its manifest is malformed."""


def _parse_signature(rest: str) -> AsmExport:
    """Parse an `export:` value like ``isqrt(int) -> int`` into an AsmExport."""
    sig = rest.strip()
    ret = "int"
    if "->" in sig:
        sig, ret = sig.split("->", 1)
        ret = ret.strip() or "int"
    sig = sig.strip()
    if "(" not in sig:
        # Bare symbol name, no parens: zero-arg, int return.
        return AsmExport(symbol=sig, arg_types=(), ret_type=ret)
    name, _, after = sig.partition("(")
    args_part = after.rsplit(")", 1)[0].strip()
    if args_part:
        arg_types = tuple(a.strip() for a in args_part.split(",") if a.strip())
    else:
        arg_types = ()
    return AsmExport(symbol=name.strip(), arg_types=arg_types, ret_type=ret)


def find_package(name: str, search_dirs: list[Path]) -> Path:
    """Locate ``<name>.asmpkg`` (dir or file) on the search path.

    Raises AsmPkgError listing the searched directories if nothing matches.
    """
    tried: list[str] = []
    for d in search_dirs:
        for cand in (d / f"{name}{ASMPKG_SUFFIX}",):
            if cand.exists():
                return cand
            tried.append(str(cand))
    raise AsmPkgError(
        f"assembly package {name!r} not found. Looked for: " + ", ".join(tried)
    )


def load_package(pkg_path: Path) -> AsmPackage:
    """Parse a located ``.asmpkg`` directory or single file into an AsmPackage."""
    if pkg_path.is_dir():
        manifest_path = pkg_path / "manifest.txt"
        if not manifest_path.exists():
            raise AsmPkgError(f"{pkg_path}: missing manifest.txt")
        base = pkg_path
        manifest_text = manifest_path.read_text(encoding="utf-8")
    else:
        base = pkg_path.parent
        manifest_text = pkg_path.read_text(encoding="utf-8")

    pkg = AsmPackage(name=pkg_path.stem, path=pkg_path)
    asm_chunks: list[str] = []

    for raw in manifest_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise AsmPkgError(f"{pkg_path}: bad manifest line: {raw!r}")
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "name":
            pkg.name = val
        elif key == "version":
            pkg.version = val
        elif key == "freestanding":
            pkg.freestanding = val.lower() in ("1", "true", "yes", "on")
        elif key == "export":
            exp = _parse_signature(val)
            pkg.exports[exp.symbol] = exp
        elif key == "asm":
            asm_file = base / val
            if not asm_file.exists():
                raise AsmPkgError(f"{pkg_path}: asm file not found: {val}")
            asm_chunks.append(
                f"; ---- from {pkg.name}: {val} ----\n"
                + asm_file.read_text(encoding="utf-8")
            )
        else:
            raise AsmPkgError(f"{pkg_path}: unknown manifest key {key!r}")

    pkg.asm_text = "\n".join(asm_chunks)
    return pkg
