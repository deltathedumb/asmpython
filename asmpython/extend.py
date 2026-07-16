"""Public API for authoring asmpython plugins: compiler-syntax extensions,
codegen backends, and linkers.

A plugin is an ordinary Python file, run by the *host* CPython interpreter
(never compiled by asmpython itself) via `--ext path/to/plugin.py`. It
imports this module and calls `Extension(...)` / `Backend(...)` /
`Linker(...)`, each of which registers itself as a side effect of
construction -- there is nothing further to call.

    # my_plugin.py
    import asmpython

    def handle_let(parser, pos):
        \"\"\"`let NAME = value` -- a trivial single-binding alternative
        to `=`, added purely to demonstrate the statement-handler
        contract.\"\"\"
        from asmpython._compiler import ast_nodes as A

        name = parser._expect("NAME").value
        parser._expect("OP", "=")
        value = parser._parse_expr()
        parser._expect("NEWLINE")
        return A.Assign(target=name, value=value, pos=pos)

    asmpython.Extension(id="let_binding", statement_handlers={"let": handle_let})

Then:

    asmpython build myfile.py --ext my_plugin.py --ext let_binding

(`--ext` accepts a bare registered id OR a filesystem path to a plugin
file -- a path is exec'd first, registering whatever it defines, before
activation by id proceeds as usual.)

**Statement-handler contract** (the `statement_handlers` dict passed to
`Extension`): each value is a callable `(parser, pos) -> ast_nodes.Stmt`.
`parser` is the live `asmpython._compiler.parser.Parser` instance, already
positioned just past the claimed keyword -- the callback drives it
directly via its private `_eat`/`_check`/`_expect`/`_parse_expr`/etc.
methods and constructs a real node from `asmpython._compiler.ast_nodes`
(imported as a private module -- there is no separate public AST
surface yet, so this is the de facto contract for now). Once active, a
claimed keyword unconditionally becomes a statement prefix for the rest of
that compile (no shape lookahead the way built-in soft keywords like
`const`/`match` have) -- it can no longer double as a plain identifier.
This is a real, deliberate trade-off, acceptable because it only ever
applies when the invoker explicitly opted in via `--ext`.

This is v1, deliberately narrow: extensions can declare metadata
(id/version/requires/conflicts) and claim new statement-prefix keywords.
The long-term goal is letting an extension reshape the language far more
broadly (up to and including replacing it with a different grammar
entirely) -- not implemented yet.
"""

from __future__ import annotations

from typing import Callable


def _lazy_extensions_module():
    # Imported lazily (not at module load time) so `import asmpython` alone
    # -- without ever touching the plugin-authoring surface -- doesn't pull
    # in the whole compiler frontend.
    from ._compiler import extensions as _ext

    return _ext


def _lazy_backends_module():
    from . import _backends as _backends_pkg

    return _backends_pkg


def _lazy_linkers_module():
    from . import _linkers as _linkers_pkg

    return _linkers_pkg


class Extension:
    """Registers a compiler-syntax extension, activatable via `--ext id`.

    `id`: the name `--ext` and other extensions' `requires`/`conflicts`
    refer to this extension by.
    `version`: a dotted version string (plain tuple-of-ints comparison).
    `requires`: dict of other extension id -> minimum version string that
    must already be active before this one can activate.
    `conflicts`: set of other extension ids that can't be active
    simultaneously with this one.
    `statement_handlers`: dict of contextual keyword -> callable, see this
    module's docstring for the callable contract. Empty by default (a
    metadata-only extension -- useful as a pure dependency/conflict marker
    even without adding syntax of its own).

    Registration happens immediately, as a side effect of construction --
    there is no separate `.register()` call.
    """

    def __init__(
        self,
        id: str,
        *,
        version: str = "1.0",
        requires: "dict[str, str] | None" = None,
        conflicts: "set[str] | None" = None,
        statement_handlers: "dict[str, Callable] | None" = None,
    ) -> None:
        self.id = id
        self.version = version
        self.requires = dict(requires or {})
        self.conflicts = set(conflicts or ())
        self._statement_handlers = dict(statement_handlers or {})
        self._register()

    def _register(self) -> None:
        ext = _lazy_extensions_module()

        # A thin CompilerExtension instance wrapping this Extension's own
        # data -- ExtensionContext.activate() only knows how to work with
        # CompilerExtension (sub)instances, so this adapts without
        # requiring the plugin author to know that internal type exists.
        handlers = dict(self._statement_handlers)
        outer = self

        class _PluginExtension(ext.CompilerExtension):
            name = outer.id
            version = outer.version
            requires = outer.requires
            conflicts = outer.conflicts

            def statement_handlers(self) -> dict:
                return handlers

        ext.register_extension(_PluginExtension())

    def __repr__(self) -> str:
        return f"Extension(id={self.id!r}, version={self.version!r})"


class Backend:
    """Registers a codegen backend, selectable via `--backend name`.

    `impl` must conform to `asmpython._compiler.ir.IRBackend`:
    `compile(module, args) -> dict[str, bytes]` and
    `link(objects, args) -> dict[str, bytes]` are required;
    `requested_args` (list[dict]) and `default_linker` (str) are optional.

    Registration happens immediately, as a side effect of construction.
    """

    def __init__(self, name: str, impl: object) -> None:
        self.name = name
        self.impl = impl
        _lazy_backends_module().register_backend(name, impl)

    def __repr__(self) -> str:
        return f"Backend(name={self.name!r})"


class Linker:
    """Registers a linker, selectable via `--linker name`.

    `impl` must expose `link(ctx: dict) -> bytes`; `requested_args`
    (list[dict]) is optional. `ctx` carries at minimum `objects` (a list of
    object-file bytes to link) and `target_os`.

    Registration happens immediately, as a side effect of construction.
    """

    def __init__(self, name: str, impl: object) -> None:
        self.name = name
        self.impl = impl
        _lazy_linkers_module().register_linker(name, impl)

    def __repr__(self) -> str:
        return f"Linker(name={self.name!r})"
