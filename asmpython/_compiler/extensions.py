"""Compiler-extension registry and per-compilation extension state.

This module gives the parser a way to accept extra, opt-in syntax --
activated per compile *invocation* via the `--ext NAME` CLI flag (never by
in-source directives: a program's grammar never changes without the
invoker's explicit, outside-the-source opt-in) -- without any global mutable
state and without the lexer or parser's core grammar tables ever being
modified. Everything here is scoped to a single `ExtensionContext` instance,
and every `Parser` owns exactly one fresh instance, pre-activated at
construction time from the CLI's `--ext` list (see `Parser.__init__`), so
extension activation:

  - never leaks between two `Parser` instances in the same process
    (confirmed exercised by `tests/test_extensions.py`),
  - never leaks between modules of the same whole-program compile
    (`program.py` builds a fresh `Parser` -- and therefore a fresh
    `ExtensionContext` -- per module; see `tests/test_program_isolation.py`),
  - trivially never leaks across separate compiler invocations (separate
    processes never share Python object state).

Extensions are declarative: a `CompilerExtension` subclass declares a name,
version, dependencies, conflicts, and the statement-prefix keywords it wants
to claim. Activating/retracting an extension is transactional -- any error
(unknown extension, missing dependency, conflict, duplicate activation,
retracting something another active extension depends on) leaves the
`ExtensionContext` completely unchanged; no partial mutation is ever
observable.

Current v1 scope note: the only built-in extension is `constants` (see
`ConstantsExtension` below), and `parser.py` dispatches its one contextual
keyword (`const`) directly rather than routing through
`ExtensionContext.handler_for(...)`. The generic dispatch path still exists
(`statement_handlers()` / `handler_for`) so a future second extension has
somewhere to register into, but nothing yet consumes it. Documented in
`docs/EXTENSIONS.md`.
"""

from __future__ import annotations

from typing import Optional

from .errors import ErrorCode, ParseError, SourcePos


class ExtensionError(ParseError):
    """Base class for every extension-activation/retraction diagnostic.

    Subclasses `ParseError` (rather than introducing a brand new exception
    hierarchy) because `CompileError.format()` keys its "lex"/"parse"/
    "semantic" label off `self.phase`, a plain string set by the base
    `ParseError.__init__` -- not off the concrete Python class -- and
    `--all-errors` mode's collection loop (`sema.py`'s `_try_check_block`)
    specifically catches `except SemaError:`. A brand new top-level
    exception class would silently not be caught there. Keeping every
    extension diagnostic a real `ParseError` means it's caught, formatted,
    and `--explain`-able exactly like every other P-coded diagnostic.
    """


class CompilerExtension:
    """Base class for a registrable compiler extension.

    Subclasses declare their identity/compatibility metadata as class
    attributes and override `statement_handlers()` to claim contextual
    statement-prefix keywords. `activate`/`deactivate` are lifecycle hooks a
    future extension can override to do extra bookkeeping in the
    `ExtensionContext` beyond the base activate/retract machinery already
    provides (the built-in `constants` extension needs neither).
    """

    name: str = ""
    version: str = "1.0"
    # Maps a required extension name to a minimum version string. Version
    # comparison is a plain tuple-of-ints compare (see `_version_at_least`).
    requires: dict = {}
    # Names of extensions this one cannot be active alongside.
    conflicts: set = set()

    def statement_handlers(self) -> dict:
        """Maps a contextual statement-prefix keyword to a handler name.
        e.g. `{"const": "handle_const"}`. Empty by default."""
        return {}

    def activate(self, context: "ExtensionContext") -> None:
        """Extra activation-time hook. No-op by default."""

    def deactivate(self, context: "ExtensionContext") -> None:
        """Extra retraction-time hook. No-op by default."""


class ConstantsExtension(CompilerExtension):
    """The `constants` extension: enables `const NAME [: annot] = value`."""

    name = "constants"
    version = "1.0"
    requires: dict = {}
    conflicts: set = set()

    def statement_handlers(self) -> dict:
        return {"const": "handle_const"}


# Class references (not instances) -- looked up by name at activation time so
# each activation constructs its own fresh instance.
_REGISTRY: dict = {
    "constants": ConstantsExtension,
}


def register_extension(cls_or_instance):
    """Register a `CompilerExtension` under its own `.name`.

    Accepts either a subclass (the in-tree built-in convention -- a fresh
    instance is constructed per activation) or an already-constructed
    instance (the `asmpython.extend.Extension(...)` public-API convention -- see
    `asmpython/extend.py`, which calls this directly). Returns whatever was
    passed in, so it also works as a class decorator for the subclass case.
    """
    _REGISTRY[cls_or_instance.name] = cls_or_instance
    return cls_or_instance


def _version_tuple(v: str) -> tuple:
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _version_at_least(actual: str, required: str) -> bool:
    return _version_tuple(actual) >= _version_tuple(required)


class ExtensionContext:
    """Per-Parser, per-compilation extension state. No global mutable state
    lives anywhere in this module -- every field below is instance state,
    constructed fresh by `Parser.__init__`."""

    def __init__(self) -> None:
        self._active: dict = {}          # name -> CompilerExtension instance
        self._activation_order: list = []  # names, in activation order
        self._stmt_handlers: dict = {}   # keyword -> (ext_name, handler_name)

    def is_active(self, name: str) -> bool:
        return name in self._active

    def handler_for(self, keyword: str) -> Optional[tuple]:
        """Returns (extension_name, callable) for a contextual statement
        keyword claimed by a currently-active extension, or None. The
        callable takes `(parser, pos)` and returns a real `A.Stmt` node --
        see `parser.py`'s dispatch loop for the call site. `statement_
        handlers()` may declare a handler either as a bound-method name
        (a string, resolved against the extension instance here -- the
        in-tree convention, e.g. `ConstantsExtension`) or as a plain
        callable directly (the `asmpython.extend.Extension(...)` public-API
        convention, since a dynamically-registered extension has no
        subclass of its own to hang a method off of) -- both resolve to a
        real callable by the time this returns."""
        entry = self._stmt_handlers.get(keyword)
        if entry is None:
            return None
        ext_name, handler = entry
        if isinstance(handler, str):
            handler = getattr(self._active[ext_name], handler)
        return (ext_name, handler)

    def activate(self, name: str, pos: Optional[SourcePos] = None) -> None:
        """Activate extension `name`. Transactional: on any failure, no
        state is mutated at all -- every check below is read-only and runs
        to completion before the single block of mutations at the end."""
        if name in self._active:
            raise ExtensionError(
                f"extension {name!r} is already active",
                pos,
                ErrorCode.P_EXTENSION_ALREADY_ACTIVE,
            )
        registered = _REGISTRY.get(name)
        if registered is None:
            hint = _did_you_mean(name, _REGISTRY.keys())
            msg = f"unknown extension {name!r}"
            if hint:
                msg += f" (did you mean {hint!r}?)"
            raise ExtensionError(msg, pos, ErrorCode.P_UNKNOWN_EXTENSION)

        # In-tree built-ins register a CLASS (a fresh instance per
        # activation, since a class can carry real per-activation instance
        # state); a dynamically-built `asmpython.extend.Extension(...)` registers
        # an already-constructed CompilerExtension INSTANCE directly (it
        # has no subclass of its own to instantiate, and its metadata/
        # handlers are fixed data with nothing meaningful to reset between
        # activations).
        instance = registered() if isinstance(registered, type) else registered

        # Conflict check (read-only): does this extension conflict with any
        # currently-active extension, or vice versa?
        for active_name, active_ext in self._active.items():
            if active_name in instance.conflicts or name in active_ext.conflicts:
                raise ExtensionError(
                    f"extension {name!r} conflicts with active extension {active_name!r}",
                    pos,
                    ErrorCode.P_EXTENSION_CONFLICT,
                )

        # Dependency check (read-only): every required extension must
        # already be active, at a compatible version.
        for dep_name, min_version in instance.requires.items():
            dep_active = self._active.get(dep_name)
            if dep_active is None:
                raise ExtensionError(
                    f"extension {name!r} requires {dep_name!r}, which is not active",
                    pos,
                    ErrorCode.P_EXTENSION_MISSING_DEP,
                )
            if not _version_at_least(dep_active.version, min_version):
                raise ExtensionError(
                    f"extension {name!r} requires {dep_name!r}>={min_version}, "
                    f"but {dep_active.version} is active",
                    pos,
                    ErrorCode.P_EXTENSION_MISSING_DEP,
                )

        # Statement-prefix collision check (read-only): the new extension's
        # claimed keywords must not already be claimed by another active
        # extension.
        new_handlers = instance.statement_handlers()
        for keyword in new_handlers:
            existing = self._stmt_handlers.get(keyword)
            if existing is not None:
                raise ExtensionError(
                    f"extension {name!r} defines statement prefix {keyword!r}, "
                    f"which is already registered by {existing[0]!r}",
                    pos,
                    ErrorCode.P_EXTENSION_CONFLICT,
                )

        # All checks passed -- mutate.
        instance.activate(self)
        self._active[name] = instance
        self._activation_order.append(name)
        for keyword, handler_name in new_handlers.items():
            self._stmt_handlers[keyword] = (name, handler_name)

    def retract(self, name: str, pos: Optional[SourcePos] = None) -> None:
        """Retract extension `name`. Transactional like `activate`. Retracting
        only changes what grammar/handlers are active going forward -- it
        does NOT erase semantics already attached to already-parsed
        declarations (e.g. a `ConstDecl` parsed while `constants` was active
        remains const-locked forever; see sema.py's `const_names`)."""
        if name not in self._active:
            raise ExtensionError(
                f"extension {name!r} is not active",
                pos,
                ErrorCode.P_EXTENSION_NOT_ACTIVE,
            )

        # Dependency-blocked check (read-only): no other active extension may
        # depend on `name`.
        for other_name, other_ext in self._active.items():
            if other_name == name:
                continue
            if name in other_ext.requires:
                raise ExtensionError(
                    f"cannot retract {name!r}: active extension {other_name!r} "
                    f"depends on it",
                    pos,
                    ErrorCode.P_EXTENSION_RETRACT_BLOCKED,
                )

        # All checks passed -- mutate.
        instance = self._active[name]
        instance.deactivate(self)
        del self._active[name]
        self._activation_order.remove(name)
        for keyword in list(self._stmt_handlers):
            if self._stmt_handlers[keyword][0] == name:
                del self._stmt_handlers[keyword]


def _did_you_mean(name: str, candidates) -> Optional[str]:
    """Cheap nearest-match hint for an unknown extension name -- prefers an
    exact case-insensitive match, then a substring relationship, else None.
    Not a general edit-distance search: the built-in registry is tiny, and a
    simple heuristic is enough for a helpful diagnostic."""
    lname = name.lower()
    for c in candidates:
        if c.lower() == lname:
            return c
    for c in candidates:
        if lname in c.lower() or c.lower() in lname:
            return c
    return None
