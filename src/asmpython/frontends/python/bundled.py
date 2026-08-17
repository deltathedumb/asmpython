"""Standard-library modules written in Python and spliced into the program.

`functools.reduce` is twelve lines of ordinary Python. Writing it as runtime C
means writing it twice -- once in C and once in the interpreter's host -- and
keeping both agreeing with CPython forever. Writing it HERE means writing it
once, in the language it is specified in, and letting the compiler that is
being tested compile it.

WHAT THIS IS NOT: an import system. Nothing is loaded at run time and no
module object exists. A program that imports a bundled module has that
module's definitions SPLICED INTO IT, under names it cannot collide with, and
every reference rewritten to point at them. The result is one module, which is
the only shape the rest of the frontend knows.

THE CONSTRAINT THAT MAKES THIS HONEST: a bundled module is compiled by this
compiler, so it may only use what this compiler accepts. A construct one of
them cannot use is a gap worth closing rather than a reason to drop back to C.
"""
from __future__ import annotations

import ast
import pathlib

from .analysis import span_of
from .modules import resolve as _resolve
from ...diagnostics import error

#: Where the sources live. One file per module, named for it.
_HERE = pathlib.Path(__file__).parent / "bundled"

#: The prefix a spliced name gets. An IDENTIFIER, because the rest of the
#: frontend reads a dot in a function key as "nested inside", and a name with
#: one in it is not a module-level `def` to anything downstream. A program
#: could write this spelling itself; `splice` refuses rather than shadowing.
_MANGLE = "_asmpy_bundled_"


def available() -> set[str]:
    """Which modules are bundled."""
    return {p.stem for p in _HERE.glob("*.py")}


def _mangled(module: str, name: str, prefix: str = None) -> str:
    # THE DOT GOES. `collections.abc` is one bundled module with a dot in its
    # name, and the rest of the frontend reads a dot in a key as "nested
    # inside" -- so a name carrying one is not a module-level definition to
    # anything downstream.
    return f"{prefix or _MANGLE}{module.replace('.', '_')}_{name}"


#: The builtins that need a compiler at run time, and the bundled module
#: that provides them. A program naming one gets it spliced in.
_RUNTIME_COMPILER = {"compile": "_pycompile",
                     "eval": "_pyrun", "exec": "_pyrun"}


def _module_bindings(tree) -> set:
    """Every name the program itself binds at module level.

    A program with its own `def compile(...)` or `compile = something` means
    its own, and rewriting the call would send it somewhere else without
    saying so.
    """
    out = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            out.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out.add(target.id)
        elif isinstance(stmt, ast.AnnAssign) \
                and isinstance(stmt.target, ast.Name):
            out.add(stmt.target.id)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                out.add(alias.asname or alias.name.split(".")[0])
    return out


class _Reserved(Exception):
    """A program used the prefix `splice` reserves for itself."""


def _no_member(sink, source, node, module: str, name: str, members) -> None:
    """`warnings.deprecated` when `warnings` is bundled and has no such name.

    THIS PASS IS THE ONLY ONE THAT CAN SAY IT. A bundled module is spliced and
    then GONE -- the import statement is dropped and no module object is left
    behind -- so by the time analysis runs there is nothing named `warnings`
    for it to have an opinion about. What it said instead was one of two wrong
    things, and both sent a reader to the wrong place:

      from warnings import deprecated
        -> `E0083: no module named 'warnings' is available; there is no import
           path`, listing modules that do not include it. Flatly false: the
           module is bundled, it was spliced into the program, and every other
           name in it worked. The reader goes looking for a missing module.

      import warnings; warnings.deprecated(...)
        -> NOTHING AT COMPILE TIME, and `NameError: name 'warnings' is not
           defined` at run time -- because the import was dropped and the
           attribute was the one reference the splice could not rewrite. The
           reader goes looking for a broken import.

    Neither is the truth, which is that the module is here and this member is
    not. Stating the coverage in a docstring (`docs/STDLIB.md`) is worth
    nothing if the compiler contradicts it, so the members it does have go in
    the diagnostic.
    """
    public = sorted(one for one in members[module] if not one.startswith("_"))
    report = (error("E0084", f"module {module!r} has no member {name!r}")
              .at(span_of(source, node))
              .note(f"{module!r} is bundled: it is Python spliced into this "
                    f"program rather than an import, and it covers part of "
                    f"CPython's module. See docs/STDLIB.md"))
    if public:
        report = report.help("it provides: " + ", ".join(public))
    sink.report(report)


def _bound_locally(node) -> set:
    """The names a function body BINDS, so they are its own and not the
    module's.

    A local that happens to share a name with a module-level definition is a
    different variable -- `fields = [...]` inside a function that a module
    also defines `fields()` in -- and renaming it pointed the body at the
    function instead of at its own list. Silently: the code compiled and did
    something else.
    """
    out = set()
    for arg in getattr(node.args, "args", []) if hasattr(node, "args") else []:
        out.add(arg.arg)
    if hasattr(node, "args"):
        for group in ("posonlyargs", "kwonlyargs"):
            for arg in getattr(node.args, group, []) or []:
                out.add(arg.arg)
        for one in (node.args.vararg, node.args.kwarg):
            if one is not None:
                out.add(one.arg)
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and isinstance(inner.ctx,
                                                      (ast.Store, ast.Del)):
            out.add(inner.id)
        elif isinstance(inner, ast.arg):
            out.add(inner.arg)
        elif isinstance(inner, (ast.Global, ast.Nonlocal)):
            # DECLARED TO BE THE OUTER ONE, so it is not local after all.
            out.difference_update(inner.names)
    return out


def _dependencies(wanted, have):
    """Every bundled module needed, each before anything that imports it.

    A depth-first walk with the module emitted AFTER what it needs, which is
    the order the prelude has to be in: a spliced definition referring to
    another module's name is only correct once that name exists.
    """
    order, seen = [], set()

    def visit(name):
        if name in seen:
            return
        seen.add(name)
        source = (_HERE / f"{name}.py").read_text(encoding="utf-8")
        for stmt in ast.parse(source, filename=f"<bundled {name}>").body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module in have:
                visit(stmt.module)
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.name in have:
                        visit(alias.name)
        order.append(name)

    for one in dict.fromkeys(wanted):
        visit(one)
    return order


class _Rename(ast.NodeTransformer):
    """Rewrite the bundled module's own top-level names to mangled ones.

    Only the names the module DEFINES: a reference to `list` or `TypeError`
    inside it means the builtin, exactly as it would in any other program --
    and neither does a LOCAL that shares a name with one of them.
    """

    def __init__(self, module: str, defined: set[str],
                 borrowed: dict | None = None,
                 imported: dict | None = None,
                 members: dict | None = None,
                 prefix: str | None = None) -> None:
        #: WHOSE NAMESPACE THE MANGLED NAMES LIVE IN. `imports.py` reuses this
        #: transformer for a program's own modules and must not mint names
        #: under the bundled prefix -- `bundled.splice` refuses a tree that
        #: already contains one, on the grounds that a program writing the
        #: reserved spelling would otherwise have its own name replaced.
        self.prefix = prefix
        self.module = module
        self.defined = defined
        #: What this module imported FROM ANOTHER BUNDLED ONE, already
        #: mangled. `from _pylex import LexError` inside `_pycompile` binds
        #: nothing after the import statement is dropped, so the reference has
        #: to point at the name `_pylex` was spliced under.
        self.borrowed = borrowed or {}
        #: `import warnings` inside a bundled module: the local name, and the
        #: module it stands for. The statement is DROPPED -- the module is
        #: spliced instead -- so `warnings.warn(...)` has to be rewritten or
        #: the name is simply unbound, which is a NameError at run time for a
        #: module the source plainly imports.
        self.imported = imported or {}
        self.members = members or {}
        #: Names the enclosing function bodies bind. A name in here is theirs.
        self.shadowed: set = set()

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id in self.shadowed:
            return node
        if node.id in self.defined:
            node.id = _mangled(self.module, node.id, self.prefix)
        elif node.id in self.borrowed:
            node.id = self.borrowed[node.id]
        return node

    def visit_FunctionDef(self, node):
        # The function's OWN name is bound outside it, so it is renamed with
        # the module's; everything the body binds is not.
        outer = self.shadowed
        self.shadowed = outer | (_bound_locally(node) - {node.name})
        self.generic_visit(node)
        self.shadowed = outer
        if node.name in self.defined and node.name not in self.shadowed:
            node.name = _mangled(self.module, node.name, self.prefix)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node):
        """`warnings.warn(...)` -> the name `warnings` was spliced under."""
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) \
                and node.value.id in self.imported \
                and node.value.id not in self.shadowed:
            module = self.imported[node.value.id]
            if node.attr in self.members.get(module, ()):
                return ast.copy_location(
                    ast.Name(id=_mangled(module, node.attr, self.prefix), ctx=node.ctx),
                    node)
        return node

    def visit_ClassDef(self, node):
        """The class's own name is the module's; its methods' names are not.

        `_pyrun` has a top-level `eval()` AND a `_Walker.eval` method, and
        renaming the second with the first left the class without the method
        it plainly defines -- `'_Walker' object has no attribute 'eval'`, at
        run time, for a name the source has in front of it.

        ONLY THE NAME, and not references that happen to match it. Shadowing
        every mention inside the class was the first attempt and it was worse:
        `datetime` has a method called `date` and inherits from the CLASS
        `date`, so `class datetime(date)` stopped being rewritten and the base
        was reported as undefined. A base is evaluated OUTSIDE the body, and a
        method body cannot see its siblings unqualified either -- in both
        places the name means the module's.
        """
        methods = [one for one in node.body
                   if isinstance(one, (ast.FunctionDef, ast.AsyncFunctionDef))]
        kept = [one.name for one in methods]
        self.generic_visit(node)
        # `generic_visit` renamed the method names along with everything
        # else; a method keeps the name its class body gave it.
        for one, name in zip(methods, kept):
            one.name = name
        if node.name in self.defined and node.name not in self.shadowed:
            node.name = _mangled(self.module, node.name, self.prefix)
        return node


class _Rewrite(ast.NodeTransformer):
    """Point the user's references at the spliced definitions.

    `functools.reduce(...)` becomes the mangled name; `from functools import
    reduce` becomes a binding of `reduce` to it, so the user's own spelling
    goes on working wherever it was already legal.
    """

    def __init__(self, imported: dict[str, str], members: dict[str, set],
                 names: dict[str, str], redirects: bool = False,
                 prefix: str | None = None) -> None:
        #: See `_Rename.prefix`.
        self.prefix = prefix
        #: Whether the program ASSIGNS to `sys.stdout`. See `visit_Name`.
        self.redirects = redirects
        #: local name -> module, for `import functools` and `import x as y`
        self.imported = imported
        self.members = members
        #: local name -> mangled, for `from functools import reduce`
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.Name:
        # REWRITTEN, not bound to a variable. Binding `wraps = <mangled>` made
        # it a module-level VALUE, and a decorator written `@wraps(fn)` is a
        # call whose callee the frontend then looked for among its functions.
        # Pointing the name straight at the definition keeps it a `def`.
        if node.id in self.names:
            node.id = self.names[node.id]
            return node
        # PEP 553: `breakpoint()` IS `sys.breakpointhook()`, by definition
        # rather than by convention. Rewritten only when the program brought
        # `sys` in -- without it there is nothing to have replaced the hook,
        # and the builtin's own no-op is the whole behaviour.
        if node.id == "breakpoint"                 and "breakpointhook" in self.members.get("sys", ()):
            node.id = _mangled("sys", "breakpointhook")
        # `print` GOES THROUGH `sys.stdout` ONLY FOR A PROGRAM THAT REPLACES
        # IT. Routing every program's printing through Python would cost the
        # ones that never redirect anything, and the direct call is the whole
        # of what they need.
        if node.id == "print" and self.redirects:
            node.id = _mangled("sys", "_print")
        # `compile()` IS THE BUNDLED ONE. See `_RUNTIME_COMPILER`: the name is
        # a builtin, so nothing imports it, and the module is spliced because
        # the name appears at all.
        # `compile()` IS THE BUNDLED ONE, and so are `eval` and `exec`.
        # See `_RUNTIME_COMPILER`: the names are builtins, so nothing
        # imports them, and the module is spliced because the name
        # appears at all.
        module = _RUNTIME_COMPILER.get(node.id)
        if module is not None and node.id in self.members.get(module, ()):
            node.id = _mangled(module, node.id, self.prefix)
        return node

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in self.imported:
            module = self.imported[node.value.id]
            if node.attr in self.members[module]:
                return ast.copy_location(
                    ast.Name(id=_mangled(module, node.attr, self.prefix), ctx=node.ctx),
                    node)
        return node


def splice(tree: ast.Module, source, sink) -> ast.Module:
    """Rewrite `tree` so its bundled imports become ordinary definitions.

    Returns the tree unchanged when it imports none of them, so a program that
    uses no bundled module pays nothing and looks exactly as it did.

    `source` and `sink` are REQUIRED rather than optional. This pass is the
    only one that can report a reference to a member a bundled module does not
    have -- see `_no_member` -- and a sink that defaults to None is a sink that
    silently swallows every one of those the day someone calls this from
    somewhere new.
    """
    have = available()
    wanted, imported, aliases = [], {}, []
    at = None
    # `compile`, `eval` and `exec` ARE BUILTINS, so no import brings them in
    # -- the name appearing is what does. `_pycompile` is spliced exactly as
    # an imported module is, and the name is rewritten to point at it.
    #
    # ONLY WHEN THE PROGRAM DOES NOT BIND THE NAME ITSELF: a program with its
    # own `def compile(...)` means its own, and rewriting the call would
    # silently send it somewhere else.
    bound = _module_bindings(tree)
    uses = {name for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in _RUNTIME_COMPILER
            and node.id not in bound
            for name in (node.id,)}
    for one in sorted(uses):
        wanted.append(_RUNTIME_COMPILER[one])
    if uses:
        at = at or next((n for n in ast.walk(tree)
                         if isinstance(n, ast.Name) and n.id in uses), None)
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name in have:
                    wanted.append(alias.name)
                    imported[alias.asname or alias.name] = alias.name
                    at = at or stmt
        elif isinstance(stmt, ast.ImportFrom) and stmt.module in have:
            wanted.append(stmt.module)
            at = at or stmt
    if not wanted:
        return tree
    # A program that writes the reserved prefix itself would have its own name
    # replaced silently. Refusing is the only honest answer, and it costs a
    # walk of a tree that is already in memory.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.startswith(_MANGLE):
            raise _Reserved(node.id)

    prelude, members = [], {}
    # A BUNDLED MODULE MAY IMPORT ANOTHER, and until `_pycompile` none did.
    # The list is walked while it grows, so a module pulled in by another is
    # spliced too -- `_pycompile` needs `_pylex`, `_pyparse` and
    # `_pyvalidate`, none of which the program ever names.
    #
    # BEFORE ITS IMPORTERS, so that the names it defines are already known
    # when the importer's own references are rewritten. `_dependencies` is
    # what puts them in that order.
    order = _dependencies(wanted, have)
    for module in order:
        # NOT `source`: that is the SourceFile of the program being compiled,
        # which `_no_member` needs to turn a node into a span.
        text = (_HERE / f"{module}.py").read_text(encoding="utf-8")
        parsed = ast.parse(text, filename=f"<bundled {module}>")
        defined = {s.name for s in parsed.body
                   if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef))}
        # A MODULE-LEVEL VARIABLE IS A MEMBER TOO. `sys.monitoring` is an
        # object the module builds rather than a class it defines, and a
        # `defined` set that held only definitions left every such name
        # unrewritten -- so the reference reached the builtin table, which
        # does not have it.
        for stmt in parsed.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(stmt, ast.AnnAssign)                     and isinstance(stmt.target, ast.Name):
                defined.add(stmt.target.id)
        members[module] = defined
        # WHAT THIS MODULE IMPORTED FROM ANOTHER BUNDLED ONE. `from _pylex
        # import LexError` inside `_pycompile` has to become the mangled name
        # `_pylex` was spliced under -- the import statement itself is dropped
        # below, so nothing else would bind it.
        borrowed, brought = {}, {}
        body = []
        for stmt in parsed.body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module in have:
                for alias in stmt.names:
                    borrowed[alias.asname or alias.name] = _mangled(
                        stmt.module, alias.name)
                continue
            if isinstance(stmt, ast.Import) \
                    and all(a.name in have for a in stmt.names):
                # DROPPED, and remembered: `warnings.warn` has to point at the
                # name `warnings` was spliced under, or it is a NameError for
                # a module the source plainly imports.
                for alias in stmt.names:
                    brought[alias.asname or alias.name] = alias.name
                continue
            body.append(stmt)
        parsed.body = body
        renamed = _Rename(module, defined, borrowed, brought,
                          members).visit(parsed)
        # The docstring goes: it is the module's, and a spliced statement that
        # binds nothing is one more thing for the class-body and entry rules
        # to have an opinion about.
        for stmt in renamed.body:
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                continue
            prelude.append(stmt)
            # THE MANGLED NAME IS AN IMPLEMENTATION DETAIL AND A PROGRAM CAN
            # SEE IT: `type(C).__name__` is the metaclass's `__name__`, and
            # that is the name of the `class` statement. Renaming the
            # statement is how the binding avoids colliding with the user's
            # names; restoring `__name__` after it is how the collision stays
            # invisible.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and stmt.name.startswith(
                                     _MANGLE):
                real = stmt.name[len(_mangled(module, "")):]
                for dunder in ("__name__", "__qualname__"):
                    prelude.append(ast.Assign(
                        targets=[ast.Attribute(
                            value=ast.Name(id=stmt.name, ctx=ast.Load()),
                            attr=dunder, ctx=ast.Store())],
                        value=ast.Constant(value=real)))

    # EVERY SPLICED NODE POINTS AT THE IMPORT. The positions it arrives with
    # are lines in the bundled file, which do not exist in the program being
    # compiled -- a diagnostic carrying one indexed past the end of the source
    # and crashed the compiler. The import is the honest place to point: it is
    # where this code entered the program.
    line = getattr(at, "lineno", 1)
    col = getattr(at, "col_offset", 0)
    for stmt in prelude:
        for node in ast.walk(stmt):
            if hasattr(node, "lineno"):
                node.lineno = node.end_lineno = line
                node.col_offset = col
                node.end_col_offset = col + 1

    # `from functools import reduce` REWRITES the user's spelling to the
    # spliced definition rather than binding a variable to it.
    names = {}
    kept = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Import) \
                and all(a.name in have for a in stmt.names):
            # THE IMPORT SURVIVES when the module also exists as a builtin
            # one: `sys` is bundled IN PART -- `audit` and `monitoring` are
            # ordinary Python while `maxsize` is a compiler constant -- and
            # dropping the statement unbound the half this does not cover.
            also = [a for a in stmt.names if _resolve(a.name) is not None]
            if also:
                kept.append(ast.copy_location(ast.Import(names=also), stmt))
            continue
        if isinstance(stmt, ast.ImportFrom) and stmt.module in have:
            left = []
            for alias in stmt.names:
                if alias.name not in members[stmt.module]:
                    # NOT SOMETHING THE BUNDLED MODULE DEFINES, so the import
                    # of it SURVIVES -- WHEN THERE IS SOMETHING ELSE TO SURVIVE
                    # INTO. A module can be bundled IN PART -- `typing`'s
                    # special forms are already runtime values and only its
                    # classes need writing -- and dropping the whole statement
                    # unbound the half this does not cover.
                    #
                    # When nothing else provides the module, leaving the
                    # statement handed analysis an import it could not resolve
                    # and produced a diagnostic denying the module exists. See
                    # `_no_member`.
                    if _resolve(stmt.module) is None:
                        _no_member(sink, source, stmt, stmt.module,
                                   alias.name, members)
                        continue
                    left.append(alias)
                    continue
                names[alias.asname or alias.name] = _mangled(stmt.module,
                                                             alias.name)
            if left:
                kept.append(ast.copy_location(
                    ast.ImportFrom(module=stmt.module, names=left,
                                   level=stmt.level), stmt))
            continue
        kept.append(stmt)

    # `warnings.deprecated` WHERE `warnings` IS BUNDLED AND HAS NO SUCH NAME.
    # Reported HERE, before `_Rewrite` runs, because afterwards the attributes
    # that DID resolve are gone and the ones left look like an attribute of an
    # ordinary object. The condition is exactly the one under which
    # `_Rewrite.visit_Attribute` declines to rewrite and the import statement
    # was dropped -- so the diagnostic and the rewrite cannot disagree.
    for stmt in kept:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Attribute) \
                    or not isinstance(node.value, ast.Name):
                continue
            module = imported.get(node.value.id)
            if module is None or node.attr in members[module] \
                    or _resolve(module) is not None:
                continue
            _no_member(sink, source, node, module, node.attr, members)

    # DOES THE PROGRAM REPLACE `sys.stdout`? Only then is `print` routed
    # through it -- see `_Rewrite.visit_Name`.
    redirects = any(
        isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
        and n.attr in ("stdout", "stderr")
        and isinstance(n.value, ast.Name) and n.value.id in imported
        for n in ast.walk(tree))
    out = ast.Module(body=prelude + kept, type_ignores=tree.type_ignores)
    # The prelude is already mangled; only the user's half is rewritten, so a
    # bundled module that happens to define `reduce` is not renamed twice.
    out.body[len(prelude):] = [
        _Rewrite(imported, members, names, redirects).visit(s)
        for s in kept]
    ast.fix_missing_locations(out)
    return out
