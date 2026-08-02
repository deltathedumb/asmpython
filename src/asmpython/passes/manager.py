"""The pass manager.

A pass is an IR -> IR transform over the neutral IR. It must not care which
frontend produced the module -- that is the property that lets one optimiser
serve every language, and it is easy to lose by accident (a pass that "knows"
integer division is always checked has quietly become Python-specific).

INVARIANTS ARE THE INTERESTING PART. Each pass declares three sets of string
tags:

    requires   must hold before it runs
    provides   it establishes these
    invalidates  it may break these

The manager checks the chain before running anything, so an impossible ordering
is a startup error naming both passes, not a crash halfway through or -- far
worse -- a silently wrong result. This is deliberately a handful of tags rather
than LLVM's analysis-preservation machinery: legibility is the point, and a
system nobody can predict is one people route around.

The tag that matters most is `cfg`. Any pass that adds, removes, merges or
reorders blocks invalidates it, and the manager then drops the cached
`ControlFlowGraph`. A pass that forgets to declare that loses correctness, not
just speed, so the manager also VERIFIES after each pass when asked -- and
`--verify-each` is the setting that turns "this program crashes sometimes" into
"pass `simplifycfg` produced invalid IR", which is a different afternoon.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field

from ..diagnostics import DiagnosticSink, Severity, error
from ..ir import Module, verify
from ..ir.verifier import VerifyError

#: Tags a pass may require, provide or invalidate. Kept closed so a typo is an
#: error rather than a requirement that can never be satisfied.
KNOWN_TAGS = frozenset({
    "cfg",           # the block graph is intact and analyses of it are valid
    "verified",      # the module satisfies verify()
    "no-dead-code",  # unreachable blocks and dead instructions are gone
    "constants",     # constant expressions are folded
})


class Pass(abc.ABC):
    """One IR -> IR transform."""

    #: Selector for `--passes`. Required, and unique.
    name: str = ""
    #: One line for `--passes help`.
    description: str = ""

    requires: frozenset[str] = frozenset()
    provides: frozenset[str] = frozenset()
    invalidates: frozenset[str] = frozenset({"cfg"})

    @abc.abstractmethod
    def run(self, module: Module) -> bool:
        """Transform in place. Return True if anything changed.

        The return value drives fixed-point iteration, so it must be honest:
        a pass that always reports True makes `--repeat` spin until the cap,
        and one that reports False after changing something leaves later
        passes looking at stale analyses.
        """

    def __repr__(self) -> str:
        return f"<pass {self.name}>"


@dataclass
class PassResult:
    name: str
    changed: bool
    duration: float
    stats_before: dict[str, int]
    stats_after: dict[str, int]

    @property
    def delta(self) -> dict[str, int]:
        return {k: self.stats_after[k] - self.stats_before[k]
                for k in self.stats_before
                if self.stats_after[k] != self.stats_before[k]}


_REGISTRY: dict[str, Pass] = {}


def register(p: Pass) -> Pass:
    if not p.name:
        raise ValueError(f"{type(p).__name__} has no name")
    if p.name in _REGISTRY:
        raise ValueError(f"pass {p.name!r} is already registered")
    unknown = (p.requires | p.provides | p.invalidates) - KNOWN_TAGS
    if unknown:
        raise ValueError(
            f"pass {p.name!r} uses unknown tag(s) {sorted(unknown)}; "
            f"known tags are {sorted(KNOWN_TAGS)}"
        )
    _REGISTRY[p.name] = p
    return p


def get(name: str) -> Pass:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise SystemExit(f"unknown pass {name!r}\navailable: {known}") from None


def available() -> dict[str, Pass]:
    return dict(_REGISTRY)


class PassManager:
    """Runs a pipeline, checking invariants before and verifying after."""

    def __init__(self, passes: list[Pass], *, verify_each: bool = False,
                 max_iterations: int = 4) -> None:
        self.passes = passes
        self.verify_each = verify_each
        self.max_iterations = max_iterations
        self.results: list[PassResult] = []

    @classmethod
    def from_names(cls, names: list[str], **kw) -> PassManager:
        return cls([get(n) for n in names], **kw)

    def check_pipeline(self) -> list[str]:
        """Problems with the ORDER, found before anything runs.

        Reported as a list rather than raised on the first, so a user fixing a
        pipeline sees every ordering problem at once.
        """
        problems: list[str] = []
        # A freshly-lowered module is verified and has an intact CFG; nothing
        # else may be assumed.
        held = {"cfg", "verified"}
        for p in self.passes:
            missing = p.requires - held
            if missing:
                culprit = self._who_invalidated(p, missing)
                problems.append(
                    f"pass {p.name!r} requires {sorted(missing)} which is not "
                    f"available at that point" + (f" ({culprit})" if culprit else "")
                )
            held -= p.invalidates
            held |= p.provides
        return problems

    def _who_invalidated(self, target: Pass, tags: set[str]) -> str:
        for p in self.passes:
            if p is target:
                return ""
            if p.invalidates & tags:
                return f"invalidated by {p.name!r}"
        return ""

    def run(self, module: Module, sink: DiagnosticSink | None = None) -> bool:
        """Run the pipeline. Returns True if the module changed at all."""
        problems = self.check_pipeline()
        if problems:
            if sink is not None:
                for p in problems:
                    sink.report(error("E9001", p))
                return False
            raise ValueError("invalid pass pipeline:\n  " + "\n  ".join(problems))

        any_change = False
        for iteration in range(self.max_iterations):
            changed_this_round = False
            for p in self.passes:
                before = module.statistics()
                start = time.perf_counter()
                changed = bool(p.run(module))
                elapsed = time.perf_counter() - start
                after = module.statistics()

                self.results.append(
                    PassResult(p.name, changed, elapsed, before, after))
                changed_this_round |= changed
                any_change |= changed

                if changed and after == before and p.invalidates:
                    # Not fatal, but worth saying: a pass reporting a change
                    # with identical statistics is usually a `return True` that
                    # should have been conditional, and it costs a whole extra
                    # fixed-point iteration.
                    pass

                if self.verify_each:
                    try:
                        verify(module)
                    except VerifyError as e:
                        raise VerifyError(
                            [f"after pass {p.name!r}: {msg}"
                             for msg in e.problems]
                        ) from None
            if not changed_this_round:
                break
        return any_change

    def report(self) -> str:
        """A one-line-per-pass summary, for `--time-passes`."""
        if not self.results:
            return "no passes run"
        width = max(len(r.name) for r in self.results)
        lines = []
        for r in self.results:
            delta = ", ".join(f"{k} {v:+d}" for k, v in sorted(r.delta.items()))
            mark = "*" if r.changed else " "
            lines.append(
                f"  {mark} {r.name:<{width}}  {r.duration * 1000:7.2f} ms"
                + (f"   {delta}" if delta else "")
            )
        total = sum(r.duration for r in self.results)
        lines.append(f"    {'total':<{width}}  {total * 1000:7.2f} ms")
        return "\n".join(lines)
