"""`asmpython` -- the command line.

    asmpython build prog.py                  source -> IR -> a program you can run
    asmpython build prog.py --emit            stop at the artifacts; do not link
    asmpython build prog.py --emit-ir        stop after the IR and print it
    asmpython build prog.py -O --time-passes optimise, and show what each pass cost
    asmpython build prog.py --target x86_64-linux --backend x86-64
    asmpython run prog.py                    execute in the reference interpreter
    asmpython check prog.py                  analyse and verify, produce nothing
    asmpython ops / types / targets / backends / frontends / toolchains / passes

Every stage can be stopped at and dumped, and `--emit-ir` writes text that
`asmpython run` accepts. That round trip is what makes a backend debuggable: you can
read exactly what it was given, and run that same text through the interpreter
to learn what it should have produced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import plugins
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
        from ..ir import verify
        from ..ir.printer import ParseError
        try:
            module = parse_module(path.read_text(encoding="utf-8"))
        except (ParseError, OSError) as exc:
            # Hand-edited IR is the documented way to test a backend, so
            # malformed IR is expected input, not an internal error.
            print(f"cannot read IR: {exc}", file=sys.stderr)
            return 2
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


def _column(items, floor: int = 10) -> int:
    """How wide the name column has to be.

    Computed rather than fixed, because a fixed width is only ever right for
    the names that shipped. Every built-in fits in ten characters and a
    third party's `counting-link` does not, so the first registration from
    outside this repository broke the alignment -- a small thing that is also
    the only kind of bug an extension point can have that nobody in-tree sees.
    """
    return max([floor, *(len(name) for name, _ in items)])


def cmd_backends(args) -> int:
    backend_registry.load_builtin()
    items = sorted(backend_registry.available().items())
    width = _column(items)
    for name, be in items:
        flag = "" if be.ready else "   (unfinished)"
        print(f"  {name:<{width}} {be.description}{flag}")
    return 0


def cmd_targets(args) -> int:
    targets = target_registry.available()
    aliases: dict[str, list[str]] = {}
    for alias, canonical in target_registry.aliases().items():
        aliases.setdefault(canonical, []).append(alias)
    width = max((len(n) for n in targets), default=4)
    for name, t in sorted(targets.items()):
        alias = (f"   aka {', '.join(sorted(aliases.get(name, [])))}"
                 if aliases.get(name) else "")
        print(f"  {name:<{width}}  {t.arch}/{t.os}  abi={t.abi} "
              f"format={t.object_format}{alias}")
    print()
    print(f"  {'host':<{width}}  resolves to this machine "
          f"({target_registry.host().name}); the default for a machine backend")
    return 0


def cmd_toolchains(args) -> int:
    link_registry.load_builtin()
    items = sorted(link_registry.available().items())
    width = _column(items)
    for name, tc in items:
        print(f"  {name:<{width}} {tc.description}")
    return 0


def cmd_frontends(args) -> int:
    frontend_registry.load_builtin()
    items = sorted(frontend_registry.available().items())
    width = _column(items)
    for name, fe in items:
        print(f"  {name:<{width}} {fe.description:<42} {' '.join(fe.extensions)}")
    return 0


def cmd_passes(args) -> int:
    print(f"default pipeline for -O: {', '.join(DEFAULT_PASSES)}\n")
    items = sorted(available_passes().items())
    width = _column(items)
    for name, p in items:
        print(f"  {name:<{width}} {p.description}")
        tags = []
        for label, s in (("requires", p.requires), ("provides", p.provides),
                         ("invalidates", p.invalidates)):
            if s:
                tags.append(f"{label}={','.join(sorted(s))}")
        if tags:
            print(f"  {'':<12} {'  '.join(tags)}")
    return 0


def _flag(value: str | None, default: bool) -> bool:
    """`--pip 1` / `--pip 0`, with the flag absent meaning the default.

    Spelled as an explicit value rather than `--pip`/`--no-pip` because these
    three switch a SEARCH ORDER, and a reader of a script wants to see which
    sources were on without knowing what the defaults were that week.
    """
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _sources(args) -> plugins.Sources:
    return plugins.Sources(
        cwd=_flag(getattr(args, "cwd", None), True),
        pypath=_flag(getattr(args, "pypath", None), True),
        pip=_flag(getattr(args, "pip", None), False),
    )


def _confirm_replace(name: str, entry, args) -> bool:
    """Ask before replacing an installed plugin. True to go ahead.

    `--yes`/`--no` answer it without asking, which is what a script needs.
    With neither, a NON-INTERACTIVE run refuses rather than prompting: a
    build that blocks forever on a hidden question is worse than one that
    stops and says which flag to pass.
    """
    if getattr(args, "assume_no", False):
        return False
    if getattr(args, "assume_yes", False):
        return True
    if not sys.stdin.isatty():
        print(f"asmpython: {name!r} is already installed "
              f"({entry.source or 'unknown'}: {entry.origin or '?'})",
              file=sys.stderr)
        print("  pass --yes to replace it, or --no to leave it alone",
              file=sys.stderr)
        return False
    print(f"{name!r} is already installed "
          f"({entry.source or 'unknown'}: {entry.origin or '?'})")
    for kind, names in (entry.provides or {}).items():
        if names:
            print(f"  {kind}: {', '.join(names)}")
    try:
        reply = input("replace it? [y/N] ").strip().lower()
    except EOFError:
        # isatty() is not a reliable "someone is there": a subprocess inherits
        # its parent's stdin, so a build runner can look interactive and still
        # have nothing to read. EOF means no answer, which means no.
        print("no answer; leaving it alone "
              "(pass --yes to replace without asking)", file=sys.stderr)
        return False
    return reply in ("y", "yes")


def cmd_plugin_invalidate(args) -> int:
    """Re-resolve installed plugins from where they came from."""
    if getattr(args, "all", False):
        names = [e.name for e in plugins.installed()]
        if not names:
            print("no plugins installed")
            return 0
    else:
        # Comma-separated OR repeated OR a single bare name -- all three are
        # the same thing to anyone typing it, so all three work.
        names = [n for chunk in (args.names or []) for n in chunk.split(",") if n]
        if not names:
            print("asmpython: name an id, or pass --all", file=sys.stderr)
            return 2
    failed = 0
    for name in names:
        try:
            entry = plugins.invalidate([name])[0]
        except plugins.PluginError as exc:
            print(f"asmpython: {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"invalidated {entry.name}  ({entry.source}: {entry.origin})")
        for kind, provided in (entry.provides or {}).items():
            if provided:
                print(f"  {kind:<10} {', '.join(provided)}")
    return 1 if failed else 0


def cmd_plugin_add(args) -> int:
    sources = _sources(args)
    existing = plugins.store.find(args.name)
    replace = False
    if existing is not None:
        if not _confirm_replace(args.name, existing, args):
            print("cancelled; nothing changed")
            return 1
        replace = True
    try:
        entry = plugins.install(args.name, sources, replace=replace)
    except plugins.AlreadyInstalled:
        # Only reachable if something installed the same name between the
        # check above and here.
        print(f"asmpython: {args.name!r} is already installed", file=sys.stderr)
        return 2
    except plugins.PluginError as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        print(f"  searched: {', '.join(sources.enabled()) or 'nothing'}",
              file=sys.stderr)
        if not sources.pip:
            print("  try --pip 1 to install it from an index",
                  file=sys.stderr)
        return 2
    print(f"added {entry.name}  ({entry.source}: {entry.origin})")
    contents = {k: v for k, v in entry.provides.items() if v}
    if contents:
        for kind, names in contents.items():
            print(f"  {kind:<10} {', '.join(names)}")
    else:
        # A module with no manifest registers on import; nothing here can say
        # what it added without diffing the registries, and claiming it added
        # nothing would be worse than saying so.
        print("  registered on import; declare __asmpython_plugin__ to list "
              "its contents")
    print(f"stored in {plugins.store.config_file()}")
    return 0


def cmd_plugin_remove(args) -> int:
    if not plugins.uninstall(args.name):
        print(f"asmpython: {args.name!r} is not installed", file=sys.stderr)
        return 2
    print(f"removed {args.name}")
    return 0


def cmd_plugin_list(args) -> int:
    entries = plugins.installed()
    if not entries:
        print("no plugins installed")
        print(f"  ({plugins.store.config_file()})")
        return 0
    width = max(len(e.name) for e in entries)
    for e in entries:
        print(f"  {e.name:<{width}}  {e.source or 'unknown'}: "
              f"{e.origin or '?'}")
        for kind, names in e.provides.items():
            if names:
                print(f"  {'':<{width}}  {kind}: {', '.join(names)}")
    return 0


def cmd_plugin_show(args) -> int:
    """What a module provides, without registering any of it."""
    try:
        found = plugins.resolve(args.name, _sources(args))
    except plugins.ResolveError as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                    # noqa: BLE001 -- reported
        # The module was found and raised on import. That is a bug in the
        # user's plugin, and a compiler traceback for it reads as a bug in
        # asmpython.
        print(f"asmpython: plugin {args.name!r} raised while importing: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    plugin = plugins.manifest_of(found.module)
    print(f"{args.name}  ({found.source}: {found.origin})")
    if plugin is None:
        print("  no __asmpython_plugin__; this module registers on import")
        return 0
    if plugin.name or plugin.version:
        print(f"  {plugin.name} {plugin.version}".rstrip())
    if plugin.description:
        print(f"  {plugin.description}")
    for kind, names in plugin.contents().items():
        if names:
            print(f"  {kind:<10} {', '.join(names)}")
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
    ap = argparse.ArgumentParser(prog="asmpython", description=__doc__.split("\n")[0])
    # Two dests, one flag. A subparser's argument OVERWRITES the namespace
    # attribute rather than appending to it, so sharing `dest` would make
    # `asmpython --plugin a build x.py --plugin b` load only `b` -- silently,
    # and only when both positions are used.
    ap.add_argument("--plugin", action="append", default=[], metavar="MODULE",
                    dest="plugin_before",
                    help="import MODULE before running, so its backends, "
                         "targets, toolchains and frontends register "
                         "(repeatable; also accepted after the command)")
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
                   help="platform to emit for; see `asmpython targets`")
    b.add_argument("--emit", action="store_true",
                   help="write backend artifacts and stop; do not link")
    b.add_argument("--toolchain", default="cc",
                   help="how to turn artifacts into a program "
                        "(see `asmpython toolchains`)")
    b.add_argument("--link-input", action="append", metavar="INPUT",
                   help="extra object, archive or -l name for the link step")
    b.add_argument("--workdir", help="where intermediates go (default .asmpython)")
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

    pl = sub.add_parser("plugin", help="install and inspect plugins")
    pls = pl.add_subparsers(dest="plugin_command", required=True)

    def where(p):
        """The three sources, as explicit 1/0 values."""
        p.add_argument("--cwd", metavar="1|0",
                       help="look for NAME.py or NAME/ here (default 1)")
        p.add_argument("--pypath", metavar="1|0",
                       help="import NAME from the Python path (default 1)")
        p.add_argument("--pip", metavar="1|0",
                       help="pip install NAME first (default 0)")

    a = pls.add_parser("add", help="install a plugin and remember it")
    a.add_argument("name")
    where(a)
    a.add_argument("--yes", "-y", dest="assume_yes", action="store_true",
                   help="replace an already-installed plugin without asking")
    a.add_argument("--no", "-n", dest="assume_no", action="store_true",
                   help="never replace; fail instead")
    a.set_defaults(fn=cmd_plugin_add)

    inv = pls.add_parser(
        "invalidate",
        help="re-resolve installed plugins from where they came from")
    inv.add_argument("names", nargs="*", metavar="ID",
                     help="plugin ids, comma-separated or repeated")
    inv.add_argument("--all", action="store_true",
                     help="every installed plugin")
    inv.set_defaults(fn=cmd_plugin_invalidate)

    rm = pls.add_parser("remove", help="forget an installed plugin")
    rm.add_argument("name")
    rm.set_defaults(fn=cmd_plugin_remove)

    ls = pls.add_parser("list", help="what is installed")
    ls.set_defaults(fn=cmd_plugin_list)

    sh = pls.add_parser("show", help="what a module provides, registering none of it")
    sh.add_argument("name")
    where(sh)
    sh.set_defaults(fn=cmd_plugin_show)

    # argparse accepts a parser-level flag only BEFORE the subcommand, and
    # `asmpython build prog.py --plugin mine` is what people type. So every
    # subparser takes it too, and main() merges the two lists.
    for p in sub.choices.values():
        p.add_argument("--plugin", action="append", default=[],
                       metavar="MODULE", help=argparse.SUPPRESS)
    return ap


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the command.

    `LookupError` is caught here rather than at each call site. Every registry
    -- targets, toolchains, passes -- raises it for a name nobody registered,
    which is a typo, and a typo should not print a traceback with a compiler
    stack in it. Catching the type once means a new registry gets the same
    treatment without anyone remembering to wire it up.

    Nothing else is caught: an unexpected exception IS a compiler bug, and the
    traceback is the most useful thing to show.
    """
    args = build_parser().parse_args(argv)

    # Plugins load before anything asks a registry a question, because that
    # is the whole point: a third-party backend has to be registered by the
    # time `--backend` is resolved, and every command resolves something.
    try:
        report = plugins.load_all(
            list(getattr(args, "plugin_before", []))
            + list(getattr(args, "plugin", [])))
    except plugins.PluginError as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        return 2
    for name, why in report.failed:
        # Installed but broken: said out loud every run, and never fatal.
        # `asmpython plugin remove NAME` is how you fix it, and that command
        # cannot be the one thing a broken plugin prevents.
        print(f"asmpython: warning: installed plugin {name!r} did not load "
              f"({why})", file=sys.stderr)
    if report.loaded and getattr(args, "verbose", False):
        print(f"loaded plugins: {', '.join(report.loaded)}", file=sys.stderr)

    try:
        return args.fn(args)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
