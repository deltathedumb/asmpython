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
from .. import target as target_registry
from ..target import Target
from ..diagnostics import DiagnosticSink, Severity, SourceFile, error
from ..ir import Module, print_module, verify
from ..backend.base import BackendUnsupported
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
    #: Produce a program, not just artifacts. False is `--emit`.
    link: bool = False
    toolchain: str = "cc"
    #: Extra objects/archives/-l names handed to the toolchain.
    link_inputs: tuple[str, ...] = ()
    workdir: Path | None = None
    keep_intermediates: bool = False
    verbose: bool = False
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
    #: The executable, if the link stage ran and succeeded.
    program: Path | None = None
    #: External commands the link stage ran, in order.
    commands: list[list[str]] = field(default_factory=list)
    #: The target actually used. Recorded because the link stage needs the
    #: object format and suffixes, and re-deriving them from the options
    #: would mean two places deciding what "the target" was.
    target: Target | None = None

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

    try:
        module = fe.compile(source, sink)
    except RecursionError:
        # A long expression is a deep tree, and analysis and lowering both
        # walk it recursively. `1 + 2 + ... + 999` exhausted the interpreter
        # stack and reached the user as a traceback ending in `_binop`, which
        # reads as a compiler crash. It is a real limit and it has a real
        # cause, so it gets said.
        sink.report(
            error("E9105", "expression is too deeply nested to compile")
            .note("analysis and lowering walk the expression tree "
                  "recursively, and this one is deeper than the stack allows")
            .help("split it across several statements"))
        return Result()
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
    target = opts.target if opts.target is not None \
        else target_registry.get(be.default_target)
    result.target = target
    try:
        result.artifacts = be.emit(module, target)
    except BackendUnsupported as exc:
        sink.report(
            error("E9103", f"the {be.name} backend cannot compile this program "
                           f"for {target.name}")
            .note(str(exc)))
        return Result()

    if opts.link:
        _link_stage(opts, result, be, target, module, sink)
    return result


def _link_stage(opts: Options, result: Result, be, target: Target,
                module: Module, sink: DiagnosticSink) -> None:
    """Artifacts to a program.

    Separate from emission because they fail for unrelated reasons and the
    user needs to know which happened: a backend that cannot compile a
    construct is a compiler limitation, while a missing assembler is a machine
    that needs a package installed. Collapsing both into "build failed" sends
    people to the wrong place.
    """
    from .. import link as link_registry

    # A bare-metal target cannot be linked by the hosted toolchain: no libc,
    # no start files, and a linker script that has to match the machine. The
    # default follows the target rather than making every invocation say so.
    name = opts.toolchain
    if name == "cc" and target.os == "none":
        name = "baremetal"
    toolchain = link_registry.get(name)
    workdir = opts.workdir or (opts.output or opts.source).parent / ".asmpython"
    output = opts.output or opts.source.with_suffix(target.executable_suffix)
    if output.suffix != target.executable_suffix and target.executable_suffix:
        output = output.with_suffix(target.executable_suffix)

    runtime_sources: tuple[Path, ...] = ()
    if not be.self_contained and link_registry.needs_runtime(module):
        runtime_sources = (link_registry.write_runtime(workdir),)

    request = link_registry.LinkRequest(
        artifacts=result.artifacts, target=target, output=output,
        workdir=workdir, extra_inputs=opts.link_inputs,
        runtime_sources=runtime_sources,
        keep_intermediates=opts.keep_intermediates, verbose=opts.verbose)
    try:
        result.program = toolchain.link(request)
    except link_registry.LinkError as exc:
        d = error("E9104", exc.message)
        if exc.detail:
            d.note(exc.detail)
        if exc.help:
            d.help(exc.help)
        sink.report(d)
        result.module = None          # nothing usable was produced
    finally:
        result.commands = [list(c) for c in request.commands]


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
