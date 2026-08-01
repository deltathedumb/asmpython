"""`irc` -- the command line tool.

    irc build prog.py -o prog.c        source -> IR -> backend artifacts
    irc build prog.py --emit-ir        stop after the IR and print it
    irc run prog.py                    run it in the reference interpreter
    irc run prog.ir                    ... including hand-written IR
    irc check prog.ir                  parse and verify only
    irc ops                            the instruction set
    irc backends / irc frontends       what is available

The pipeline is deliberately visible: every stage can be stopped at, dumped,
and fed back in. `--emit-ir` writes text that `irc run` accepts, so the IR is
never a thing you have to take on trust -- if a backend is wrong, you can read
exactly what it was given, and run that same text through the interpreter to
find out what it should have produced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import backend, frontend, ops, types as T
from .core import Module
from .frontend import CompileError
from .interp import Interpreter, Trap
from .text import ParseError, parse_module, print_module
from .verify import VerifyError, verify


def _load(path: Path, fe_name: str | None) -> Module:
    """Produce a Module from a source file or a `.ir` text file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".ir":
        return parse_module(text)
    fe = frontend.get(fe_name) if fe_name else frontend.for_path(path)
    if fe is None:
        raise SystemExit(
            f"no frontend claims {path.suffix!r}; pass --frontend NAME "
            f"(have: {', '.join(sorted(frontend.available()))})"
        )
    return fe.compile(text, path)


def cmd_build(args) -> int:
    src = Path(args.source)
    module = _load(src, args.frontend)
    verify(module)

    if args.emit_ir:
        text = print_module(module)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"wrote {args.output}")
        else:
            sys.stdout.write(text)
        return 0

    be = backend.get(args.backend)
    if not be.ready:
        print(f"warning: backend {be.name!r} is not finished", file=sys.stderr)
    artifacts = be.emit(module)

    out = Path(args.output) if args.output else None
    for i, (name, data) in enumerate(sorted(artifacts.items())):
        # A single artifact goes exactly where -o said; several are written
        # beside it under their own names, because renaming the second one to
        # the requested path would silently overwrite the first.
        dest = out if (out and len(artifacts) == 1) else \
            (out.parent / name if out else Path(name))
        dest.write_bytes(data)
        print(f"wrote {dest} ({len(data)} bytes)")
    return 0


def cmd_run(args) -> int:
    module = _load(Path(args.source), args.frontend)
    verify(module)
    interp = Interpreter(module)
    try:
        result = interp.run(args.entry, [int(a) for a in args.args])
    except Trap as t:
        print(f"trap: {t}", file=sys.stderr)
        return 70
    if result is not None and args.print_result:
        print(f"-> {result}")
    return 0


def cmd_check(args) -> int:
    module = _load(Path(args.source), args.frontend)
    verify(module)
    n = sum(len(b.instrs) for f in module.funcs for b in f.blocks)
    print(f"ok: {len(module.funcs)} function(s), {len(module.globals)} global(s), "
          f"{n} instruction(s)")
    return 0


def cmd_ops(args) -> int:
    print(f"{len(ops.Op)} opcodes. `ty` is the width the opcode operates at; "
          f"for a comparison it is the OPERAND type (the result is i1).\n")
    width = max(len(o.value) for o in ops.Op)
    for op in ops.Op:
        s = ops.spec(op)
        arity = "*" if s.arity is None else str(s.arity)
        mark = " [terminator]" if s.terminator else ""
        allowed = ("any" if not s.allowed
                   else " ".join(t.name for t in s.allowed))
        print(f"  {op.value:<{width}}  args={arity:<2} -> {s.result:<4}{mark}")
        print(f"  {'':<{width}}  types: {allowed}")
        for line in _wrap(s.doc, 66):
            print(f"  {'':<{width}}  {line}")
        print()
    return 0


def cmd_backends(args) -> int:
    backend.load_builtin()
    for name, be in sorted(backend.available().items()):
        flag = "" if be.ready else "  (unfinished)"
        print(f"  {name:<10} {be.description}{flag}")
    return 0


def cmd_frontends(args) -> int:
    frontend.load_builtin()
    for name, fe in sorted(frontend.available().items()):
        ext = " ".join(fe.extensions) or "-"
        print(f"  {name:<10} {fe.description:<40} {ext}")
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


def main(argv: list[str] | None = None) -> int:
    frontend.load_builtin()
    backend.load_builtin()

    ap = argparse.ArgumentParser(
        prog="irc", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("source")
        p.add_argument("--frontend", help="override frontend auto-detection")

    b = sub.add_parser("build", help="compile to backend artifacts")
    common(b)
    b.add_argument("-o", "--output")
    b.add_argument("--backend", default="naive")
    b.add_argument("--emit-ir", action="store_true",
                   help="stop after the IR and print it")
    b.set_defaults(fn=cmd_build)

    r = sub.add_parser("run", help="execute in the reference interpreter")
    common(r)
    r.add_argument("--entry", default="main")
    r.add_argument("args", nargs="*", help="integer arguments for the entry")
    r.add_argument("--print-result", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="parse and verify, produce nothing")
    common(c)
    c.set_defaults(fn=cmd_check)

    for name, fn, help_ in (
        ("ops", cmd_ops, "print the instruction set"),
        ("types", cmd_types, "print the type system"),
        ("backends", cmd_backends, "list available backends"),
        ("frontends", cmd_frontends, "list available frontends"),
    ):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except CompileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ParseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except VerifyError as e:
        # The frontend produced invalid IR. That is a compiler bug, not a user
        # error, so it says so -- otherwise the user reads a list of internal
        # invariants and reasonably assumes their program is at fault.
        print("internal error: the frontend produced invalid IR.\n"
              "This is a bug in the compiler, not in your program.\n",
              file=sys.stderr)
        print(e.report(), file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
