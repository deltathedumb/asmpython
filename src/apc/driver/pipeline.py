"""Orchestration: source in, artifacts out.

One function, `compile_source`, running the stages in order and stopping at the
first that fails. It exists so the CLI, the test suite and any embedding tool
drive the compiler through exactly the same path -- a test that reproduces a
bug through a different sequence of calls is testing something the user never
runs.

    parse + analyse   frontend      -> Module or None (errors reported)
    verify            ir.verify     -> internal error if the frontend is wrong
    optimise          passes        -> Module
    verify again      ir.verify     -> internal error if a pass is wrong
    emit              backend       -> {filename: bytes}

THE TWO VERIFY CALLS ARE NOT REDUNDANT. The first attributes bad IR to the
frontend, the second to the pass pipeline. Without both, a malformed module
reaching a backend is a crash whose cause could be either, and the difference
is which file you open.

Invalid IR is reported as an INTERNAL error, distinctly from a user error. A
user handed a list of IR invariants will reasonably assume their program is at
fault, and go looking in the wrong place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import backend as backend_registry
from .. import frontend as frontend_registry
from ..backend import Target
from ..diagnostics import DiagnosticSink, Severity, SourceFile, error
from ..ir import Module, print_module, verify
from ..ir.verifier import VerifyError
from ..passes import PassManager

#: Passes run when the user asks for optimisation but names none.
DEFAULT_PASSES = ("constfold", "copyprop", "dce", "simplifycfg")


@dataclass
class Options:
    """Everything that varies between invocations."""

    source: Path
    output: Path | None = None
    frontend: str | None = None
    backend: str = "c"
    target: Target | None = None
    passes: tuple[str, ...] = ()
    optimise: bool = False
    emit_ir: bool = False
    show_spans: bool = False
    verify_each: bool = False
    time_passes: bool = False
    max_errors: int = 100
    warnings_are_errors: bool = False

    @property
    def effective_passes(self) -> tuple[str, ...]:
        if self.passes:
            return self.passes
        return DEFAULT_PASSES if self.optimise else ()


@dataclass
class Result:
    """What a compilation produced, plus how it got there."""

    module: Module | None = None
    artifacts: dict[str, bytes] = field(default_factory=dict)
    ir_text: str | None = None
    pass_report: str = ""

    @property
    def ok(self) -> bool:
        return self.module is not None


def compile_source(opts: Options, sink: DiagnosticSink) -> Result:
    """Run the pipeline. Errors go to `sink`; `Result.ok` says whether to write."""
    frontend_registry.load_builtin()
    backend_registry.load_builtin()

    try:
        source = SourceFile.read(opts.source)
    except OSError as exc:
        sink.report(error("E9100", f"cannot read {opts.source}: {exc.strerror}"))
        return Result()

    fe = (frontend_registry.get(opts.frontend) if opts.frontend
          else frontend_registry.for_path(opts.source))
    if fe is None:
        sink.report(
            error("E9101", f"no frontend claims {opts.source.suffix!r}")
            .help("choose one with --frontend "
                  + "|".join(sorted(frontend_registry.available()))))
        return Result()

    module = fe.compile(source, sink)
    if module is None or sink.failed:
        return Result()

    if not _verify_stage(module, sink, "the frontend"):
        return Result()

    result = Result(module=module)

    names = opts.effective_passes
    if names:
        pm = PassManager.from_names(list(names), verify_each=opts.verify_each)
        pm.run(module, sink)
        if sink.failed:
            return Result()
        if opts.time_passes:
            result.pass_report = pm.report()
        if not _verify_stage(module, sink, "the pass pipeline"):
            return Result()

    if opts.emit_ir:
        result.ir_text = print_module(module, show_spans=opts.show_spans)
        return result

    be = backend_registry.get(opts.backend)
    if not be.ready:
        sink.report(
            error("W9102", f"backend {be.name!r} is not finished")
            .note("its output may be incorrect or incomplete"))
        sink.diagnostics[-1].severity = Severity.WARNING
    target = opts.target or be.default_target
    result.artifacts = be.emit(module, target)
    return result


def _verify_stage(module: Module, sink: DiagnosticSink, who: str) -> bool:
    try:
        verify(module)
        return True
    except VerifyError as exc:
        d = error("E9999", f"internal error: {who} produced invalid IR")
        d.note("This is a bug in the compiler, not in your program.")
        for problem in exc.problems[:10]:
            d.note(problem)
        if len(exc.problems) > 10:
            d.note(f"... and {len(exc.problems) - 10} more")
        sink.report(d)
        return False
