"""`apc` -- the command line.

    apc build prog.py                  source -> IR -> a program you can run
    apc build prog.py --emit            stop at the artifacts; do not link
    apc build prog.py --emit-ir        stop after the IR and print it
    apc build prog.py -O --time-passes optimise, and show what each pass cost
    apc build prog.py --target x86_64-linux --backend x86-64
    apc run prog.py                    execute in the reference interpreter
    apc check prog.py                  analyse and verify, produce nothing
    apc ops / types / targets / backends / frontends / toolchains / passes

Every stage can be stopped at and dumped, and `--emit-ir` writes text that
`apc run` accepts. That round trip is what makes a backend debuggable: you can
read exactly what it was given, and run that same text through the interpreter
to learn what it should have produced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import backend as backend_registry
from .. import frontend as frontend_registry
from .. import link as link_registry
from .. import target as target_registry
from ..diagnostics import DiagnosticSink, Renderer, SourceFile
from ..ir import opcodes, types as T
from ..ir.interpreter import Interpreter, Trap
from ..ir.printer import parse_module
from ..ir.verifier import VerifyError
from ..passes import available as available_passes
from .pipeline import DEFAULT_PASSES, Options, compile_source


def _sink(args) -> DiagnosticSink:
    return DiagnosticSink(
        max_errors=getattr(args, "max_errors", 100),
        warnings_are_errors=getattr(args, "werror", False),
    )


def _options(args) -> Options:
    return Options(
        source=Path(args.source),
        output=Path(args.output) if getattr(args, "output", None) else None,
        frontend=getattr(args, "frontend", None),
        backend=getattr(args, "backend", "c"),
        target=(target_registry.get(args.target)
                if getattr(args, "target", None) else None),
        link=not getattr(args, "emit", False),
        toolchain=getattr(args, "toolchain", "cc"),
        link_inputs=tuple(getattr(args, "link_input", None) or ()),
        workdir=Path(args.workdir) if getattr(args, "workdir", None) else None,
        keep_intermediates=getattr(args, "keep_intermediates", False),
        verbose=getattr(args, "verbose", False),
        passes=tuple(p for p in (getattr(args, "passes", "") or "").split(",") if p),
        optimise=getattr(args, "optimise", False),
        emit_ir=getattr(args, "emit_ir", False),
        show_spans=getattr(args, "show_spans", False),
        verify_each=getattr(args, "verify_each", False),
        time_passes=getattr(args, "time_passes", False),
        max_errors=getattr(args, "max_errors", 100),
        warnings_are_errors=getattr(args, "werror", False),
    )


def cmd_build(args) -> int:
    sink = _sink(args)
    opts = _options(args)
    result = compile_source(opts, sink)
    sink.emit()
    if not result.ok:
        return 1

    if result.pass_report:
        print(result.pass_report, file=sys.stderr)

    if result.ir_text is not None:
        if opts.output:
            opts.output.write_text(result.ir_text, encoding="utf-8")
            print(f"wrote {opts.output}")
        else:
            sys.stdout.write(result.ir_text)
        return 0

    if opts.verbose:
        for cmd in result.commands:
            print("$ " + " ".join(cmd), file=sys.stderr)

    if result.program is not None:
        print(f"wrote {result.program}")
        return 0

    out = opts.output
    for name, data in sorted(result.artifacts.items()):
        # One artifact goes exactly where -o said. Several are written beside
        # it under their own names -- renaming the second onto the requested
        # path would silently overwrite the first.
        dest = out if (out and len(result.artifacts) == 1) else \
            ((out.parent / name) if out else Path(name))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"wrote {dest} ({len(data)} bytes)")
    return 0


def cmd_run(args) -> int:
    path = Path(args.source)
    if path.suffix == ".ir":
        module = parse_module(path.read_text(encoding="utf-8"))
        from ..ir import verify
        try:
            verify(module)
        except VerifyError as exc:
            print("invalid IR:\n" + exc.report(), file=sys.stderr)
            return 2
    else:
        sink = _sink(args)
        opts = _options(args)
        opts.emit_ir = True             # the interpreter runs IR, not artifacts
        opts.link = False
        result = compile_source(opts, sink)
        sink.emit()
        if not result.ok:
            return 1
        module = result.module

    try:
        value = Interpreter(module).run(args.entry, [int(a) for a in args.args])
    except Trap as trap:
        print(f"trap: {trap}", file=sys.stderr)
        return 70
    if args.print_result and value is not None:
        print(f"-> {value}")
    return 0


def cmd_check(args) -> int:
    sink = _sink(args)
    opts = _options(args)
    opts.emit_ir = True                     # stop before any backend
    opts.link = False
    result = compile_source(opts, sink)
    sink.emit()
    if not result.ok:
        return 1
    stats = result.module.statistics()
    print("ok: " + ", ".join(f"{v} {k}" for k, v in stats.items() if v))
    return 0


def cmd_ops(args) -> int:
    print(f"{len(opcodes.Op)} opcodes. `ty` is the width the opcode operates "
          f"at; for a comparison it is the OPERAND type (the result is i1).\n")
    width = max(len(o.value) for o in opcodes.Op)
    for op in opcodes.Op:
        s = opcodes.spec(op)
        arity = "*" if s.arity is None else str(s.arity)
        mark = "  [terminator]" if s.terminator else ""
        allowed = "any" if not s.allowed else " ".join(t.name for t in s.allowed)
        print(f"  {op.value:<{width}}  args={arity:<2} -> {s.result:<4}{mark}")
        print(f"  {'':<{width}}  types: {allowed}")
        for line in _wrap(s.doc, 64):
            print(f"  {'':<{width}}  {line}")
        print()
    return 0


def cmd_types(args) -> int:
    for name, ty in T.ALL.items():
        if ty.is_void:
            print(f"  {name:<5} no value")
            continue
        kind = ("float" if ty.is_float else "pointer" if ty.is_ptr
                else "signed integer" if ty.is_signed else "unsigned integer")
        print(f"  {name:<5} {ty.bits:>3} bits, {ty.size} byte(s)   {kind}")
    return 0


def cmd_backends(args) -> int:
    backend_registry.load_builtin()
    for name, be in sorted(backend_registry.available().items()):
        flag = "" if be.ready else "   (unfinished)"
        print(f"  {name:<10} {be.description}{flag}")
    return 0


def cmd_targets(args) -> int:
    targets = target_registry.available()
    aliases: dict[str, list[str]] = {}
    for alias, canonical in target_registry.aliases().items():
        aliases.setdefault(canonical, []).append(alias)
    width = max((len(n) for n in targets), default=4)
    for name, t in sorted(targets.items()):
        alias = f"   aka {', '.join(sorted(aliases.get(name, [])))}"             if aliases.get(name) else ""
        print(f"  {name:<{width}}  {t.arch}/{t.os}  abi={t.abi} "
              f"format={t.object_format}{alias}")
    return 0


def cmd_toolchains(args) -> int:
    for name, tc in sorted(link_registry.available().items()):
        print(f"  {name:<10} {tc.description}")
    return 0


def cmd_frontends(args) -> int:
    frontend_registry.load_builtin()
    for name, fe in sorted(frontend_registry.available().items()):
        print(f"  {name:<10} {fe.description:<42} {' '.join(fe.extensions)}")
    return 0


def cmd_passes(args) -> int:
    print(f"default pipeline for -O: {', '.join(DEFAULT_PASSES)}\n")
    for name, p in sorted(available_passes().items()):
        print(f"  {name:<12} {p.description}")
        tags = []
        for label, s in (("requires", p.requires), ("provides", p.provides),
                         ("invalidates", p.invalidates)):
            if s:
                tags.append(f"{label}={','.join(sorted(s))}")
        if tags:
            print(f"  {'':<12} {'  '.join(tags)}")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="apc", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def source_args(p):
        p.add_argument("source")
        p.add_argument("--frontend")
        p.add_argument("--max-errors", type=int, default=100)
        p.add_argument("--werror", action="store_true",
                       help="treat warnings as errors")

    def pass_args(p):
        p.add_argument("-O", "--optimise", action="store_true",
                       help=f"run the default pipeline ({', '.join(DEFAULT_PASSES)})")
        p.add_argument("--passes", help="comma-separated pass names")
        p.add_argument("--verify-each", action="store_true",
                       help="verify after every pass; names the pass that broke it")
        p.add_argument("--time-passes", action="store_true")

    b = sub.add_parser("build", help="compile to backend artifacts")
    source_args(b)
    pass_args(b)
    b.add_argument("-o", "--output")
    b.add_argument("--backend", default="c")
    b.add_argument("--target",
                   help="platform to emit for; see `apc targets`")
    b.add_argument("--emit", action="store_true",
                   help="write backend artifacts and stop; do not link")
    b.add_argument("--toolchain", default="cc",
                   help="how to turn artifacts into a program "
                        "(see `apc toolchains`)")
    b.add_argument("--link-input", action="append", metavar="INPUT",
                   help="extra object, archive or -l name for the link step")
    b.add_argument("--workdir", help="where intermediates go (default .apc)")
    b.add_argument("--keep-intermediates", action="store_true")
    b.add_argument("-v", "--verbose", action="store_true",
                   help="print the external commands that were run")
    b.add_argument("--emit-ir", action="store_true")
    b.add_argument("--show-spans", action="store_true",
                   help="annotate each instruction with its source position")
    b.set_defaults(fn=cmd_build)

    r = sub.add_parser("run", help="execute in the reference interpreter")
    source_args(r)
    pass_args(r)
    r.add_argument("--entry", default="main")
    r.add_argument("args", nargs="*")
    r.add_argument("--print-result", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="analyse and verify, produce nothing")
    source_args(c)
    pass_args(c)
    c.set_defaults(fn=cmd_check)

    for name, fn, doc in (
        ("ops", cmd_ops, "print the instruction set"),
        ("types", cmd_types, "print the type system"),
        ("targets", cmd_targets, "list target platforms"),
        ("backends", cmd_backends, "list backends"),
        ("toolchains", cmd_toolchains, "list ways of producing a program"),
        ("frontends", cmd_frontends, "list frontends"),
        ("passes", cmd_passes, "list optimisation passes"),
    ):
        p = sub.add_parser(name, help=doc)
        p.set_defaults(fn=fn)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
