"""Turning artifacts into a program.

A backend produces *artifacts* -- assembly, C, object bytes. Something has to
turn those into a file you can run, and that something is not the backend: the
same x86-64 assembly is assembled by gas on Linux, by gas-for-COFF on Windows,
and linked by ld, lld, or a C driver standing in for either. Making the backend
own that would mean every backend growing a copy of the same toolchain search.

So a *toolchain* takes `{filename: bytes}` plus a target and produces an
executable. There are two shipped:

    cc        hands everything to a C compiler driver (gcc/clang). Assembles
              .s, compiles .c, links, and finds the system libraries -- which
              is the part that is genuinely hard to do by hand and the reason
              this is the default.
    none      writes the artifacts out and stops. What `--emit` wants.

WHY A REGISTRY RATHER THAN A FLAG. The set of ways to link is not fixed and
not ours: a bare-metal target links with a linker script and no libc, an
embedded one runs objcopy afterwards, a cross build uses a prefixed toolchain.
Each is a small, self-contained recipe. As a flag they would accumulate inside
the driver; as registrations they live beside the target they serve.

FAILURES ARE REPORTED, NOT RAISED. A missing assembler is an ordinary thing to
have on a machine, not an internal error, so it becomes a diagnostic naming
what was looked for and what to install.
"""
from __future__ import annotations

import abc
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..target import Target


class LinkError(Exception):
    """A toolchain could not produce a program. Carries a user-facing reason."""

    def __init__(self, message: str, *, detail: str = "",
                 help: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.help = help


@dataclass
class LinkRequest:
    """Everything a toolchain needs to produce one program."""

    #: What the backend produced: {filename: contents}.
    artifacts: dict[str, bytes]
    target: Target
    #: Where the program should end up. The suffix the target declares is
    #: applied by the driver, not here.
    output: Path
    #: Directory for intermediates. Kept if `keep_intermediates`.
    workdir: Path
    #: Extra objects, archives or `-l` names supplied by the caller.
    extra_inputs: tuple[str, ...] = ()
    #: Runtime source the frontend needs linked in (see `apc.link.runtime`).
    runtime_sources: tuple[Path, ...] = ()
    keep_intermediates: bool = False
    verbose: bool = False
    #: Commands actually run, for `--verbose` and for tests that assert what
    #: was invoked rather than parsing stdout.
    commands: list[list[str]] = field(default_factory=list)


class Toolchain(abc.ABC):
    """Turns backend artifacts into an executable."""

    name: str = ""
    description: str = ""

    @abc.abstractmethod
    def link(self, request: LinkRequest) -> Path:
        """Produce the program. Returns the path actually written.

        Raise `LinkError` -- not a bare exception -- for anything the user can
        act on: a missing tool, a failed assembler, an unsupported target.
        """

    def supports(self, target: Target) -> bool:
        """Whether this toolchain can produce a program for `target`."""
        return True

    def __repr__(self) -> str:
        return f"<toolchain {self.name}>"


# ── running external tools ──────────────────────────────────────────────────

def find_tool(names: tuple[str, ...], *, what: str,
              install: str = "") -> str:
    """First of `names` present on PATH.

    Raises LinkError naming everything tried. "gcc not found" is unhelpful
    when the code would equally have accepted cc or clang; the message says
    which ones would have worked.
    """
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    tried = ", ".join(names)
    raise LinkError(
        f"no {what} found",
        detail=f"looked for: {tried}",
        help=install or f"install one of {tried}, or put it on PATH")


def run(request: LinkRequest, argv: list[str], *, what: str) -> None:
    """Run one external command, turning failure into a LinkError.

    The command line goes into `request.commands` whether it succeeds or not:
    when a build fails, the exact invocation is the first thing anyone wants,
    and reconstructing it from a description is how the reported command ends
    up differing from the one that ran.
    """
    request.commands.append(list(argv))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        raise LinkError(f"could not run {argv[0]}", detail=str(exc)) from None
    if proc.returncode != 0:
        raise LinkError(
            f"{what} failed (exit {proc.returncode})",
            detail=(proc.stderr or proc.stdout or "").strip(),
            help=f"command was: {' '.join(argv)}")
