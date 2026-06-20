"""argparse module: a small but functional command-line argument parser.

A practical alternative to CPython's argparse, scoped to what asmpython's
type system supports (no `**kwargs` / `*args`): every option is configured
through `add_argument`'s fixed, typed parameters instead of arbitrary
keywords. Parsed values come back in a `Namespace`-like object via typed
getters (`get` / `get_int` / `get_flag`).

`ArgumentParser(prog=, description=, usage=, epilog=, add_help=, formatter_class=)`:
  - `usage` overrides the auto-generated usage line entirely (CPython argparse
    behavior).
  - `epilog` is printed after the options list in `print_help()`.
  - `add_help=0` disables the built-in `-h`/`--help` handling so the caller
    can register its own via `add_argument(..., action="help")` (useful for
    controlling where `-h` appears in `print_help()`'s options list).
  - `formatter_class` is accepted for call-site compatibility with CPython
    argparse (e.g. a `HelpFormatter` subclass passed as a bare class, which
    asmpython evaluates to its RTTI id) but otherwise unused (see class
    docstring below).

Supported via `add_argument(name, ...)`:
  - positionals (`add_argument("source")`) -- required unless `required=0`
    or `nargs="?"` (in which case `default` is used when the argument is
    absent; `nargs="?"` additionally makes a positional optional even with
    no explicit `default`)
  - flags (`add_argument("--verbose", short="-v", action="store_true")`,
    also `"store_false"` and `"count"`)
  - `action="store_const"` with `const=` -- stores `const` when the flag is
    given (used for mutually-exclusive mode flags)
  - `action="help"` -- print help and exit 0 (see `add_help` above)
  - `action="version"` -- print `version` and exit 0
  - value-taking options (`add_argument("--output", short="-o")`)
  - `choices` -- a list of allowed string values
  - `metavar` -- the placeholder shown for an option's value in usage/help
  - `type` -- a bare class reference (e.g. `type=Path`, evaluating to its RTTI
    id) applied to the raw string value before it's stored, mirroring CPython
    argparse's `type=` conversion (e.g. `args.source` comes back as a real
    `Path`, not a string)
  - `default`, `dest`, `help`, `version`

`ap.add_argument_group(title)` returns a group whose `.add_argument(...)`
registers onto the same parser (CPython groups only affect help layout; this
module's `print_help` doesn't do column grouping, so groups exist for call-site
compatibility and to host `add_mutually_exclusive_group()`).

`ap.add_mutually_exclusive_group()` (also available on a group) returns an
object whose `.add_argument(...)` registers arguments that `parse_args` checks
are not given together (raises like CPython: "not allowed with argument ...").

`ap.set_defaults(bundle_mode=...)` overrides a `dest`'s value when the
argument wasn't given. Limited to the `bundle_mode` dest (asmpython has no
`**kwargs`, so this can't be CPython's fully generic `set_defaults(**kwargs)`);
extend with more named parameters if another dest needs this.

`parse_args(argv)` validates required arguments and `choices`; on error it
prints a usage line + message and calls `sys.exit(2)` (mirroring argparse).
With the default `add_help=1`, `-h` / `--help` prints generated help and
exits 0. `--` ends option parsing; all remaining tokens are positional.

Like `pathlib`, this is bundled as asmpython *source* (it defines real
classes), so it's merged into the program like a project module rather than
going through the FFI-only `STDLIB_BINDINGS` registry.
"""
from __future__ import annotations

import sys

from pathlib import Path


class HelpFormatter:
    """Minimal stand-in for CPython's `argparse.HelpFormatter`.

    `ArgumentParser`'s `formatter_class=` accepts (but doesn't invoke) a
    formatter class, so this exists mainly so programs can write
    `class MyHelp(argparse.RawDescriptionHelpFormatter): ...` and have it
    type-check; this module's own `print_help` doesn't do column layout.
    """

    def __init__(self, prog: str, indent_increment: int = 2,
                  max_help_position: int = 24, width: int = 0) -> None:
        self.prog = prog
        self.indent_increment = indent_increment
        self.max_help_position = max_help_position
        self.width = width


class RawDescriptionHelpFormatter(HelpFormatter):
    """Like `HelpFormatter`, but CPython argparse leaves `description`/`epilog`
    text unwrapped. This module's `print_help` already prints them verbatim,
    so this subclass behaves the same as `HelpFormatter`."""

    def __init__(self, prog: str, indent_increment: int = 2,
                  max_help_position: int = 24, width: int = 0) -> None:
        super().__init__(prog, indent_increment, max_help_position, width)


class RawTextHelpFormatter(HelpFormatter):
    """Like `RawDescriptionHelpFormatter`, but also leaves `help=` text for
    each argument unwrapped. This module's `print_help` already prints help
    text verbatim, so this subclass behaves the same as `HelpFormatter`."""

    def __init__(self, prog: str, indent_increment: int = 2,
                  max_help_position: int = 24, width: int = 0) -> None:
        super().__init__(prog, indent_increment, max_help_position, width)


class _Arg:
    def __init__(self, dest: str, name: str, short: str, is_positional: int,
                 action: str, default: str, choices: list[str], help: str,
                 required: int, version: str, const: str, metavar: str,
                 nargs: str, type: int = 0) -> None:
        self.dest = dest
        self.name = name
        self.short = short
        self.is_positional = is_positional
        self.action = action
        self.default = default
        self.choices = choices
        self.help = help
        self.required = required
        self.version = version
        self.const = const
        self.metavar = metavar
        self.nargs = nargs
        # RTTI id of the `type=` callable (e.g. `Path`), or 0 if none was
        # given. `parse_args` uses this to convert the raw string value
        # before storing it, mirroring CPython argparse's `type=` semantics
        # — callers like asmpython's own CLI (`__main__.py`) call real
        # `Path` methods (`.exists()`, `.read_text()`, ...) directly on
        # `args.source`, so the conversion has to actually happen here
        # rather than being a no-op accepted only for signature compat.
        self.type = type
        # >= 0 -> index into the owning parser's mutually-exclusive groups.
        self.mutex_group = -1

    def _convert(self, value: str) -> Path | str:
        if value is None:
            return None
        if self.type == Path:
            return Path(value)
        return value

    def _in_choices(self, value: str) -> int:
        if len(self.choices) == 0:
            return 1
        for c in self.choices:
            if c == value:
                return 1
        return 0

    def _choices_str(self) -> str:
        return ", ".join(self.choices)

    def _metavar(self) -> str:
        if self.metavar != "":
            return self.metavar
        return self.dest.upper()


class Namespace:
    """Parsed values live as real dynamic attributes (via `setattr`/`getattr`
    against this instance's own field dict — asmpython instances are
    str-keyed dicts under the hood), not a separate `dict` field. This lets
    CPython-argparse-style call sites (`args.source`, `args.output = ...`)
    work identically whether this module or real CPython argparse is in use,
    instead of only supporting the `.get()`-style accessors below.

    asmpython's sema only infers an instance field's type from a
    `self.x = ...` assignment inside this class's own methods (an attribute
    that's only ever set via an external `setattr(ns, ...)` call, as
    `parse_args` does, stays untyped/opaque, and an untyped truthy check
    like `if args.check:` tests the raw pointer rather than the value —
    always true for a non-null string/int). The fields below are the ones
    asmpython's own CLI (`__main__.py`) relies on coming back as a real
    typed value via plain attribute access instead of the `.get_path()` /
    `.get_flag()` accessors: the `Path` ones for `add_argument(...,
    type=Path)` dests (so `.exists()`, `.read_text()`, etc. resolve to real
    `Path` methods), the `int` ones for `store_true`/`count` dests checked
    with a plain `if args.x:`. Add more here if another caller needs
    real attribute-style access for a new dest."""

    def __init__(self) -> None:
        # `None` here (not `Path("")`) is deliberate: it keeps the runtime
        # value a null pointer until `parse_args` actually sets one via
        # `setattr`, so `args.source is None` (`is`/`is not` lower to a raw
        # `== 0` check — see sema's `is`/`is not` handling) still works for
        # an absent optional `type=Path` argument, exactly like before these
        # fields were declared. Only the static `Path` annotation is new.
        self.source: Path = None
        self.output: Path = None
        self.icon: Path = None
        self.nasm: Path = None
        self.gcc: Path = None
        # store_true/count flags read via plain `if args.x:` truthiness.
        self.check: int = 0
        self.use_runtime_lib: int = 0
        self.emit_asm: int = 0
        self.keep: int = 0
        self.keep_assembly: int = 0
        self.one_error: int = 0
        self.json: int = 0

    def get(self, key: str, default: str = "") -> str:
        return getattr(self, key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return getattr(self, key, default)

    def get_flag(self, key: str) -> int:
        return getattr(self, key, 0)

    def get_path(self, key: str, default: str = "") -> Path:
        return Path(getattr(self, key, default))


class _MutexGroup:
    """A mutually-exclusive group of arguments on `parser`. `add_argument`
    forwards to the parser, then tags the new spec with this group's id so
    `parse_args` can reject more than one of them being given together."""

    def __init__(self, parser: ArgumentParser, group_id: int) -> None:
        self.parser = parser
        self.group_id = group_id

    def add_argument(self, name: str, short: str = "", help: str = "",
                      default: str = "", choices: list[str] = [], action: str = "",
                      required: int = 0, dest: str = "", version: str = "",
                      const: str = "", metavar: str = "", nargs: str = "",
                      type: int = 0) -> _Arg:
        spec = self.parser.add_argument(
            name, short, help, default, choices, action, required, dest,
            version, const, metavar, nargs, type,
        )
        spec.mutex_group = self.group_id
        return spec


class _ArgGroup:
    """A named group of arguments on `parser`. CPython argparse uses groups to
    cluster `--help` output into sections; this module's `print_help` doesn't
    do that layout, so groups exist for call-site compatibility (`ap.
    add_argument_group("...")` + `.add_argument(...)`) and to host
    `add_mutually_exclusive_group()`."""

    def __init__(self, parser: ArgumentParser, title: str) -> None:
        self.parser = parser
        self.title = title

    def add_argument(self, name: str, short: str = "", help: str = "",
                      default: str = "", choices: list[str] = [], action: str = "",
                      required: int = 0, dest: str = "", version: str = "",
                      const: str = "", metavar: str = "", nargs: str = "",
                      type: int = 0) -> _Arg:
        return self.parser.add_argument(
            name, short, help, default, choices, action, required, dest,
            version, const, metavar, nargs, type,
        )

    def add_mutually_exclusive_group(self) -> _MutexGroup:
        return self.parser.add_mutually_exclusive_group()


class ArgumentParser:
    def __init__(self, prog: str = "", description: str = "", usage: str = "",
                  epilog: str = "", add_help: int = 1,
                  formatter_class: int = 0) -> None:
        self.prog = prog
        self.description = description
        self.usage = usage
        self.epilog = epilog
        # 1 (default): auto-handle -h/--help in parse_args/_usage/print_help.
        # 0: the caller registers its own -h/--help via
        # add_argument(..., action="help"), e.g. to control its placement.
        self.add_help = add_help
        # `formatter_class` only affects line-wrapping/column layout of
        # CPython's argparse help text; this module's `print_help` doesn't do
        # that kind of formatting, so the value is accepted (for signature
        # compatibility with CPython argparse call sites) and otherwise unused.
        self.specs: list[_Arg] = []
        self._next_mutex_group = 0
        # set_defaults(bundle_mode=...) -- see module docstring.
        self._default_bundle_mode = ""

    def add_argument(self, name: str, short: str = "", help: str = "",
                      default: str = "", choices: list[str] = [], action: str = "",
                      required: int = 0, dest: str = "", version: str = "",
                      const: str = "", metavar: str = "", nargs: str = "",
                      type: int = 0) -> _Arg:
        # CPython argparse accepts either flag-name order:
        # `add_argument("-o", "--output")` or `add_argument("--output", "-o")`.
        # Both `name` and `short` arrive as plain positionals here (asmpython
        # has no *args), so normalize: whichever of the two is the long form
        # (`--xxx`) becomes the canonical `name`; the other (if also a flag)
        # becomes `short`. Single-flag and positional calls pass through.
        if name.startswith("-") == 1 and short.startswith("-") == 1:
            if name.startswith("--") == 0 and short.startswith("--") == 1:
                name, short = short, name
        is_positional = int(name.startswith("-") == 0)
        if dest == "":
            if is_positional == 1:
                dest = name
            elif name.startswith("--"):
                dest = name[2:]
            else:
                dest = name[1:]
            dest = dest.replace("-", "_")
        if is_positional == 1 and default == "" and required == 0 and nargs != "?":
            required = 1
        spec = _Arg(dest, name, short, is_positional, action, default, choices,
                     help, required, version, const, metavar, nargs, type)
        self.specs.append(spec)
        return spec

    def add_argument_group(self, title: str = "", description: str = "") -> _ArgGroup:
        return _ArgGroup(self, title)

    def add_mutually_exclusive_group(self) -> _MutexGroup:
        gid = self._next_mutex_group
        self._next_mutex_group = gid + 1
        return _MutexGroup(self, gid)

    def set_defaults(self, bundle_mode: str = "") -> None:
        if bundle_mode != "":
            self._default_bundle_mode = bundle_mode

    def _usage(self) -> str:
        if self.usage != "":
            return "usage: " + self.usage
        out = "usage: " + self.prog
        if self.add_help == 1:
            out = out + " [-h]"
        for a in self.specs:
            if a.is_positional == 1:
                continue
            if (a.action == "store_true" or a.action == "store_false"
                    or a.action == "count" or a.action == "help"
                    or a.action == "version" or a.action == "store_const"):
                token = a.name
            else:
                token = a.name + " " + a._metavar()
            if a.required == 1:
                out = out + " " + token
            else:
                out = out + " [" + token + "]"
        for a in self.specs:
            if a.is_positional == 0:
                continue
            label = a.name if a.metavar == "" else a.metavar
            if a.required == 1:
                out = out + " " + label
            else:
                out = out + " [" + label + "]"
        return out

    def print_usage(self, file: int = 0) -> None:
        # `file` (e.g. `sys.stderr`) is accepted for call-site compatibility
        # with CPython argparse but otherwise unused — see module docstring.
        print(self._usage())

    def format_help(self) -> str:
        # Mirrors CPython argparse: print_help() is just print(format_help()).
        # Subclasses (e.g. a colorizing _ColorParser) override print_help to
        # post-process this text instead of duplicating the layout.
        out = self._usage()
        if self.description != "":
            out = out + "\n\n" + self.description
        out = out + "\n\npositional arguments:"
        for a in self.specs:
            if a.is_positional == 1:
                out = out + "\n  " + a.name + "  " + a.help
        out = out + "\n\noptions:"
        if self.add_help == 1:
            out = out + "\n  -h, --help  show this help message and exit"
        for a in self.specs:
            if a.is_positional == 0:
                label = a.name
                if a.short != "":
                    label = a.short + ", " + a.name
                out = out + "\n  " + label + "  " + a.help
        if self.epilog != "":
            out = out + "\n\n" + self.epilog
        return out

    def print_help(self) -> None:
        print(self.format_help())

    def error(self, message: str) -> None:
        self.print_usage()
        print(self.prog + ": error: " + message)
        sys.exit(2)

    def parse_args(self, argv: list[str]) -> Namespace:
        # `argv=None` (CPython's default) -> the real command-line args,
        # excluding the program name.
        if argv is None:
            argv = sys.argv[1:]
        ns = Namespace()
        for a in self.specs:
            if a.action == "store_true" or a.action == "count":
                setattr(ns, a.dest, 0)
            elif a.action == "store_false":
                setattr(ns, a.dest, 1)
            elif a.is_positional == 1 and a.nargs == "?" and a.default == "":
                # An optional positional with no explicit default behaves like
                # CPython's `default=None`: leave the field unset so it reads
                # back as the dict-default 0, matching how an explicit
                # `default=None` flag is stored (see add_argument above) —
                # this is what lets `args.source is None` work when absent.
                pass
            else:
                setattr(ns, a.dest, a._convert(a.default))
        if self._default_bundle_mode != "":
            setattr(ns, "bundle_mode", self._default_bundle_mode)

        positionals: list[_Arg] = []
        for a in self.specs:
            if a.is_positional == 1:
                positionals.append(a)

        seen: dict[str, int] = {}
        pos_i = 0
        force_positional = 0
        i = 0
        n = len(argv)
        while i < n:
            tok = argv[i]
            if force_positional == 0 and tok == "--":
                force_positional = 1
                i = i + 1
                continue
            if self.add_help == 1 and force_positional == 0 and (tok == "-h" or tok == "--help"):
                self.print_help()
                sys.exit(0)
            if force_positional == 0 and tok.startswith("-") == 1 and tok != "-":
                key = tok
                value = ""
                has_inline = 0
                eq = tok.find("=")
                if eq >= 0:
                    key = tok[0:eq]
                    value = tok[eq + 1:]
                    has_inline = 1
                matched_i = -1
                j = 0
                while j < len(self.specs):
                    a2 = self.specs[j]
                    if a2.is_positional == 0 and (a2.name == key or (a2.short != "" and a2.short == key)):
                        matched_i = j
                        break
                    j = j + 1
                if matched_i < 0:
                    self.error("unrecognized argument: " + tok)
                matched = self.specs[matched_i]
                if matched.action == "store_true":
                    setattr(ns, matched.dest, 1)
                elif matched.action == "store_false":
                    setattr(ns, matched.dest, 0)
                elif matched.action == "count":
                    setattr(ns, matched.dest, ns.get_int(matched.dest, 0) + 1)
                elif matched.action == "store_const":
                    setattr(ns, matched.dest, matched.const)
                elif matched.action == "help":
                    self.print_help()
                    sys.exit(0)
                elif matched.action == "version":
                    print(matched.version)
                    sys.exit(0)
                else:
                    if has_inline == 0:
                        i = i + 1
                        if i >= n:
                            self.error("argument " + key + ": expected one argument")
                        value = argv[i]
                    if matched._in_choices(value) == 0:
                        self.error("argument " + key + ": invalid choice: '" + value + "' (choose from " + matched._choices_str() + ")")
                    setattr(ns, matched.dest, matched._convert(value))
                seen[matched.dest] = 1
                i = i + 1
                continue
            if pos_i < len(positionals):
                a = positionals[pos_i]
                if a._in_choices(tok) == 0:
                    self.error("argument " + a.name + ": invalid choice: '" + tok + "' (choose from " + a._choices_str() + ")")
                setattr(ns, a.dest, a._convert(tok))
                seen[a.dest] = 1
                pos_i = pos_i + 1
            else:
                self.error("unrecognized argument: " + tok)
            i = i + 1

        for a in self.specs:
            if a.required == 1 and a.dest not in seen:
                self.error("the following arguments are required: " + a.name)

        # Mutually-exclusive groups: at most one member of each group may be
        # `seen` (i.e. explicitly given on the command line).
        group_seen: dict[str, str] = {}
        for a in self.specs:
            if a.mutex_group < 0 or a.dest not in seen:
                continue
            gkey = str(a.mutex_group)
            prev = group_seen.get(gkey, "")
            if prev != "" and prev != a.name:
                self.error("argument " + a.name + ": not allowed with argument " + prev)
            group_seen[gkey] = a.name

        return ns
