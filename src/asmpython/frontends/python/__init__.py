"""Python frontend: a statically-annotated subset.

    analysis.py   resolve names, assign a type to every expression, report
    lower.py      typed AST -> IR

Lowering runs only if analysis reported nothing, so it contains no validation.

THE SUBSET: annotated int/float/bool parameters and locals, arithmetic and
comparison, if/while/for-over-range, calls, and print. No objects, no dynamic
typing, no exceptions, no closures.

That boundary is honest rather than arbitrary. Everything missing needs a
runtime and an object model, which is a separate body of work; the point of
this frontend is to prove the IR is usable by a real language and to be the
worked example a second frontend is written against.
"""
from __future__ import annotations

import ast

from ...diagnostics import DiagnosticSink, SourceFile, error, warning
from ...frontend import Frontend, register
from ...ir import Module
from .analysis import Analyzer, span_of
from . import cffi
from .bundled import splice
from .imports import splice as user_splice
from .lower import Lowerer


#: Spellings that are not distinct types: PEP 3151 folded the old I/O error
#: names into `OSError`, and CPython keeps them only as aliases.
_EXC_ALIASES = {"IOError": "OSError", "EnvironmentError": "OSError",
                "WindowsError": "OSError"}


class _Aliases(ast.NodeTransformer):
    """Replace an aliased builtin name with the one it IS."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = _EXC_ALIASES.get(node.id, node.id)
        return node


#: The builtins that need a compiler at run time. Named here as well as in
#: `bundled.py` because the two ask different questions about them: that one
#: routes the call, this one says what it costs.
_RUNTIME_COMPILER = ("compile", "eval", "exec")


def _warn_about_runtime_compilation(tree, source, sink):
    """Say what `compile`, `eval` and `exec` cost, where they are written.

    A WARNING AND NOT AN ERROR. The program gets a real one: the parser, the
    validator and the code object are bundled Python spliced into it. What it
    does not get is the compiler that built the binary, and the difference is
    worth a line at the call site rather than a surprise later.

    TWO MESSAGES, because the costs differ. `compile()` answers whether source
    is valid Python and stops. `eval()` and `exec()` RUN it, and running it is
    interpretation -- far slower than the native code around it, which is the
    part a reader needs told.

    REPORTED BEFORE THE SPLICE, which is the last moment the bare name is
    still in the tree: afterwards it points at the bundled definition and
    nothing can tell it was ever written.
    """
    bound = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            bound.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    said = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or node.id in bound:
            continue
        if node.id not in _RUNTIME_COMPILER or node.id in said:
            continue
        said.add(node.id)
        note = ("it answers whether source is valid Python, through the "
                "parser bundled into this binary -- not through the compiler "
                "that built it") if node.id == "compile" else (
            "the source it is given is INTERPRETED rather than compiled: it "
            "runs through the interpreter bundled into this binary, and is "
            "far slower than the code around it")
        sink.report(
            warning("W0091",
                    f"{node.id}() is not recommended in a compiled program")
            .at(span_of(source, node))
            .note(note)
            .help("prefer a function the compiler can see and call directly"))


def _stringifies(tree: ast.Module) -> bool:
    """Did this module ask for PEP 563?"""
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            if any(a.name == "annotations" for a in stmt.names):
                return True
    return False


class _Stringify(ast.NodeTransformer):
    """Replace every annotation with its source text.

    `ast.unparse` is the round trip Python itself uses for this, so the text a
    program reads back is the text CPython would give it -- `list[int]` and
    not `list [ int ]`.
    """

    @staticmethod
    def _text(node):
        made = ast.copy_location(ast.Constant(value=ast.unparse(node)), node)
        # MARKED, so analysis can tell this string from a forward reference a
        # program wrote itself. A quoted `'int'` IS the static int; a PEP 563
        # annotation is TEXT the program asked not to have evaluated, and
        # using it as a type would contradict the directive that made it text.
        made.pep563 = True
        return made

    def visit_arg(self, node: ast.arg):
        if node.annotation is not None:
            node.annotation = self._text(node.annotation)
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node.returns is not None:
            node.returns = self._text(node.returns)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.generic_visit(node)
        node.annotation = self._text(node.annotation)
        return node


class PythonFrontend(Frontend):
    name = "python"
    extensions = (".py",)
    description = "statically-annotated Python subset"

    def compile(self, source: SourceFile, sink: DiagnosticSink, *,
                library: bool = False) -> Module | None:
        try:
            tree = ast.parse(source.text, filename=source.name)
        except SyntaxError as exc:
            sink.report(self._syntax_error(source, exc))
            return None

        # A bundled standard-library module becomes ordinary definitions
        # in this program, before anything looks at it. See `bundled.py`: it
        # is a splice, not an import system, and a program that uses none of
        # them comes back unchanged.
        # ASKED BEFORE THE SPLICE, because `__future__` is itself a bundled
        # module now and splicing consumes the very statement this reads.
        stringify = _stringifies(tree)
        # SAID BEFORE THE SPLICE CONSUMES THE NAME. `compile`, `eval` and
        # `exec` are rewritten to the bundled implementation, and after that
        # nothing downstream can tell they were ever written -- so the warning
        # about what they cost is reported from here, where the source still
        # says what the programmer wrote.
        _warn_about_runtime_compilation(tree, source, sink)
        # THE PROGRAM'S OWN MODULES FIRST, then the bundled standard library.
        # A spliced user module may `import functools`, and after the first
        # pass that statement is an ordinary one in the merged tree for the
        # second to resolve. The other order leaves it unspliced.
        tree = user_splice(tree, source.path)
        tree = splice(tree)
        # PEP 3151: `IOError` and `EnvironmentError` ARE `OSError` -- the same
        # object in CPython, not subclasses of it. Rewriting the name here is
        # what makes `IOError is OSError` True and an `except IOError` catch
        # an OSError; leaving them as distinct names would have given two
        # objects that print differently and never match each other.
        _Aliases().visit(tree)
        # PEP 563: `from __future__ import annotations` makes every annotation
        # its own SOURCE TEXT. Done here, as a rewrite, because that is what
        # the future import means -- nothing downstream needs to know about it
        # once each annotation is already the string it stands for.
        if stringify:
            _Stringify().visit(tree)

        analyzer = Analyzer(source, sink, library=library)
        functions = analyzer.run(tree)
        # WHAT THE LINKER HAS TO BE TOLD. `ctypes.CDLL("m")` is a promise that
        # `-lm` will be there; published here because the driver drives the
        # link and the frontend only knows what the source said.
        cffi.name_libraries(analyzer.ctypes_libraries)
        if sink.failed:
            # Lowering assumes analysis succeeded. Running it anyway would
            # produce IR that fails the verifier, and the user would see an
            # internal-error report on top of the real diagnostics.
            return None
        return Lowerer(functions, source, analyzer, library=library).run()

    @staticmethod
    def _syntax_error(source: SourceFile, exc: SyntaxError):
        line = exc.lineno or 1
        col = max(0, (exc.offset or 1) - 1)
        starts = source.line_starts
        begin = starts[line - 1] + col if line <= len(starts) else 0
        return error("E0000", exc.msg or "invalid syntax").at(
            source.span(begin, begin + 1))


register(PythonFrontend())
