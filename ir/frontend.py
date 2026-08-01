"""The frontend interface.

A frontend turns source text into a `Module`. Mirror image of a backend, and
the same size:

    class MyLang(Frontend):
        name = "mylang"
        extensions = (".ml",)
        def compile(self, source, path) -> Module: ...

Whatever it returns is verified before any backend sees it, so a frontend
cannot hand a backend something malformed -- the failure lands on the frontend,
where the bug is, instead of somewhere in a code generator.

WHERE THE LANGUAGE LIVES
------------------------
Everything language-specific belongs on this side of the line. The IR has no
opinion about Python's `//` flooring toward negative infinity, about boxing,
about dynamic dispatch or exceptions: those are things a frontend LOWERS INTO
the IR's small vocabulary, plus whatever runtime functions it decides to call.

The division case is the concrete example. `Op.DIV` truncates toward zero,
like C and like every machine. Python's `//` floors. A Python frontend must
therefore emit more than one instruction for `//`, and `frontends/python.py`
does. If the IR had a "floor divide" opcode instead, every one of the 33
backends would owe an implementation of Python's semantics -- and a Lua
frontend would still not be served, because Lua's `//` floors on integers but
its `/` always produces a float.

That is the whole argument for a small IR: the cost of lowering is paid once
per frontend, the cost of a fat opcode is paid once per backend.
"""
from __future__ import annotations

import abc
from pathlib import Path

from .core import Module


class Frontend(abc.ABC):
    """Turn source text into a Module."""

    #: Selector used by `irc build --frontend NAME`. Required.
    name: str = ""
    #: File extensions this frontend claims, for auto-detection.
    extensions: tuple[str, ...] = ()
    #: One line, shown by `irc frontends`.
    description: str = ""

    @abc.abstractmethod
    def compile(self, source: str, path: Path | None = None) -> Module:
        """Lower `source` to a Module. Raise `CompileError` on bad input."""

    def __repr__(self) -> str:
        return f"<frontend {self.name}>"


class CompileError(Exception):
    """Source the frontend cannot accept. Carries a position when known."""

    def __init__(self, message: str, line: int | None = None,
                 path: Path | str | None = None) -> None:
        self.message = message
        self.line = line
        self.path = path
        where = ""
        if path is not None:
            where = f"{path}"
            if line is not None:
                where += f":{line}"
            where += ": "
        elif line is not None:
            where = f"line {line}: "
        super().__init__(where + message)


_REGISTRY: dict[str, Frontend] = {}


def register(frontend: Frontend) -> Frontend:
    if not frontend.name:
        raise ValueError(f"{type(frontend).__name__} has no name")
    if frontend.name in _REGISTRY:
        raise ValueError(f"frontend {frontend.name!r} is already registered")
    _REGISTRY[frontend.name] = frontend
    return frontend


def get(name: str) -> Frontend:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise SystemExit(f"unknown frontend {name!r}\navailable: {known}") from None


def for_path(path: Path) -> Frontend | None:
    """The frontend claiming this file's extension, if exactly one does."""
    matches = [f for f in _REGISTRY.values() if path.suffix in f.extensions]
    return matches[0] if len(matches) == 1 else None


def available() -> dict[str, Frontend]:
    return dict(_REGISTRY)


def load_builtin() -> None:
    from .frontends import python  # noqa: F401
