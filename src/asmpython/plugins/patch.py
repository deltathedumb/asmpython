"""Patching the compiler from a plugin.

A registry covers the four things asmpython expected people to extend. This
covers everything else -- a pass that needs to run differently, a diagnostic
worth rewording, an experiment you would otherwise maintain as a fork:

    from asmpython.plugins import CompilerPatch

    def louder(original, module, sink):
        sink.report(...)
        return original(module, sink)

    patch = CompilerPatch("asmpython.passes.dce.run", wrap=louder,
                          reason="log what DCE removes")
    plugin.patches.append(patch)

FOUR KINDS, and `wrap` is the one to reach for. `replace` throws the original
away, `before`/`after` observe, and `wrap` receives the original as its first
argument and decides. Wrapping composes: two plugins wrapping one function
both run, in install order, and neither has to know about the other. Two
plugins REPLACING one function means the second silently wins, so that is
reported as a conflict rather than discovered later.

WHAT CANNOT BE PATCHED, and why there are two tiers:

  * SEALED -- this module and the plugin store. A patch able to disable the
    check makes every other rule advisory, and a patch able to rewrite the
    installed-plugin list can reinstall itself after being removed. These are
    not judgement calls about risk; they are the two cases where allowing it
    means the rest of this file is decoration.

  * GUARDED -- the verifier and the four registries. Patchable, but only by
    saying `force=True`, which `asmpython plugin show` then displays. These
    are things OTHER plugins rely on: a backend author is told ten invariants
    hold without checking, and a patch that quietly stops the verifier from
    enforcing them turns their correct code into a crash far away.

Everything else is open, deliberately. The alternative is guessing in advance
which internals someone will legitimately need, and being wrong in the
direction that makes people fork.

Patches are REVERSIBLE. `revert_all()` puts everything back, which the test
suite depends on and which makes a patch something you can experiment with
rather than a decision that survives until you restart.

THE ONE THING THAT WILL SURPRISE YOU: patching `mod.func` rebinds the
attribute on `mod`, so a caller that did `from mod import func` is holding
the function directly and never sees the patch. Nothing here can fix that --
it is how names work in Python -- so patch what the callers actually reach:

    asmpython.passes.transforms.dce            # patched: callers use the
                                               # module attribute
    asmpython.driver.pipeline.compile_source   # patched, but cli.py did
                                               # `from .pipeline import
                                               # compile_source` and will
                                               # keep calling the original

The same applies in reverse to timing: a plugin loads before the CLI resolves
a backend, but after `driver.cli` has imported its own names. When a patch
appears to do nothing, this is the reason, and it is not subtle once you know
to look for it.
"""
from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

#: Never patchable. See the module docstring: allowing these makes every
#: other protection advisory.
SEALED: tuple[str, ...] = (
    "asmpython.plugins.patch",
    "asmpython.plugins.store",
)

#: Patchable with `force=True`, and reported when it happens.
GUARDED: tuple[str, ...] = (
    "asmpython.ir.verifier",
    "asmpython.backend.base",
    "asmpython.frontend.base",
    "asmpython.target.registry",
    "asmpython.link.registry",
)


class PatchError(Exception):
    """A patch that cannot be applied, or must not be."""


@dataclass
class CompilerPatch:
    """One change to one function or method.

    `target` is a dotted path: `asmpython.passes.dce.run`, or
    `asmpython.ir.builder.Builder.emit` for a method. The module part is
    whatever prefix imports; the rest is attribute lookup, so patching a
    method of a class inside a module needs no special spelling.
    """

    target: str
    replace: Callable | None = None
    wrap: Callable | None = None
    before: Callable | None = None
    after: Callable | None = None
    #: Required to patch anything in GUARDED. Displayed by `plugin show`.
    force: bool = False
    #: Why. Printed in conflict reports and by `plugin show`, because the
    #: useful question about a patch is never "what" but "what for".
    reason: str = ""

    _owner: Any = field(default=None, repr=False, compare=False)
    _attr: str = field(default="", repr=False, compare=False)
    _original: Any = field(default=None, repr=False, compare=False)
    _applied: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        kinds = [k for k in ("replace", "wrap", "before", "after")
                 if getattr(self, k) is not None]
        if not kinds:
            raise PatchError(
                f"patch of {self.target!r} does nothing: pass replace=, "
                f"wrap=, before= or after=")
        if len(kinds) > 1:
            raise PatchError(
                f"patch of {self.target!r} gives {' and '.join(kinds)}; "
                f"pick one -- combining them makes the order they run in a "
                f"detail of this class rather than something you wrote")

    @property
    def kind(self) -> str:
        for k in ("replace", "wrap", "before", "after"):
            if getattr(self, k) is not None:
                return k
        return "?"           # unreachable: __post_init__ refuses

    @property
    def applied(self) -> bool:
        return self._applied

    # ── the protection ────────────────────────────────────────────────────
    def check(self) -> None:
        """Refuse a sealed target, and a guarded one without `force`."""
        for prefix in SEALED:
            if self.target == prefix or self.target.startswith(prefix + "."):
                raise PatchError(
                    f"{self.target!r} is sealed and cannot be patched: it is "
                    f"the mechanism that enforces this, and a patch able to "
                    f"disable it would make every other protection advisory")
        if self._guarded_prefix() and not self.force:
            prefix = self._guarded_prefix()
            raise PatchError(
                f"{self.target!r} is guarded ({prefix}). Other plugins rely "
                f"on it: pass force=True if you mean it, and it will be "
                f"listed wherever this plugin is described")

    def _guarded_prefix(self) -> str:
        for prefix in GUARDED:
            if self.target == prefix or self.target.startswith(prefix + "."):
                return prefix
        return ""

    @property
    def is_guarded(self) -> bool:
        return bool(self._guarded_prefix())

    # ── applying ──────────────────────────────────────────────────────────
    def resolve(self) -> tuple[Any, str]:
        """Find the object holding the attribute, and the attribute's name.

        The longest importable prefix is the module and everything after it
        is attribute lookup, so `pkg.mod.Class.method` needs no special
        spelling and neither does `pkg.mod.function`.
        """
        parts = self.target.split(".")
        module = None
        used = 0
        for i in range(len(parts), 0, -1):
            try:
                module = importlib.import_module(".".join(parts[:i]))
            except ImportError:
                continue
            used = i
            break
        if module is None:
            raise PatchError(f"no module in {self.target!r} could be imported")
        if used == len(parts):
            raise PatchError(
                f"{self.target!r} names a module, not something in one")
        owner: Any = module
        for name in parts[used:-1]:
            try:
                owner = getattr(owner, name)
            except AttributeError as exc:
                raise PatchError(f"{self.target!r}: {exc}") from exc
        attr = parts[-1]
        if not hasattr(owner, attr):
            raise PatchError(f"{self.target!r} does not exist")
        return owner, attr

    def apply(self) -> None:
        if self._applied:
            return
        self.check()
        owner, attr = self.resolve()
        original = getattr(owner, attr)
        if not callable(original):
            raise PatchError(f"{self.target!r} is not callable")

        self._owner, self._attr, self._original = owner, attr, original
        setattr(owner, attr, self._build(original))
        self._applied = True
        _APPLIED.append(self)

    def revert(self) -> None:
        if not self._applied:
            return
        setattr(self._owner, self._attr, self._original)
        self._applied = False
        if self in _APPLIED:
            _APPLIED.remove(self)

    def _build(self, original: Callable) -> Callable:
        kind = self.kind
        if kind == "replace":
            new = self.replace
        elif kind == "wrap":
            wrapper = self.wrap

            def new(*args, **kwargs):
                return wrapper(original, *args, **kwargs)
        elif kind == "before":
            hook = self.before

            def new(*args, **kwargs):
                hook(*args, **kwargs)
                return original(*args, **kwargs)
        else:
            hook = self.after

            def new(*args, **kwargs):
                return hook(original(*args, **kwargs), *args, **kwargs)

        try:
            functools.update_wrapper(new, original)
        except (AttributeError, TypeError):
            # A builtin or a slot wrapper has no __dict__ to copy onto. The
            # patch still works; only the introspection sugar is missing.
            pass
        # Kept so a second patch of the same target can be reported against
        # the first by name rather than as "something already patched this".
        new.__asmpython_patch__ = self
        return new

    def describe(self) -> str:
        bits = [f"{self.target} ({self.kind}"]
        if self.force:
            bits.append(", forced")
        bits.append(")")
        if self.reason:
            bits.append(f" -- {self.reason}")
        return "".join(bits)


#: Applied patches, in the order they were applied, so `revert_all` can undo
#: them in reverse -- two patches of one target only compose back correctly
#: if they are removed innermost first.
_APPLIED: list[CompilerPatch] = []


def applied() -> list[CompilerPatch]:
    return list(_APPLIED)


def conflicts(patch: CompilerPatch) -> list[CompilerPatch]:
    """Applied patches of the same target that this one would fight with.

    Two `wrap`s compose and are not a conflict. Anything involving a
    `replace` is: the original is gone, so whichever ran second decides, and
    which one that is depends on install order nobody chose deliberately.
    """
    same = [p for p in _APPLIED if p.target == patch.target]
    if not same:
        return []
    if patch.kind == "replace":
        return same
    return [p for p in same if p.kind == "replace"]


def apply_all(patches: list[CompilerPatch]) -> list[CompilerPatch]:
    """Apply in order, refusing on the first failure.

    Anything already applied is reverted before raising, so a plugin whose
    third patch is bad does not leave the first two installed -- half a
    plugin is harder to diagnose than none of it.
    """
    done: list[CompilerPatch] = []
    try:
        for patch in patches:
            clash = conflicts(patch)
            if clash:
                raise PatchError(
                    f"{patch.target!r} is already patched by "
                    f"{clash[0].describe()}; a `replace` cannot be combined "
                    f"with another patch of the same target")
            patch.apply()
            done.append(patch)
    except Exception:
        for patch in reversed(done):
            patch.revert()
        raise
    return done


def revert_all() -> None:
    """Undo every applied patch, innermost first."""
    for patch in reversed(list(_APPLIED)):
        patch.revert()
