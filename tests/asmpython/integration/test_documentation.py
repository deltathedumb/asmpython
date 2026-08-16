"""The documentation is executed, not proofread.

README.md claims every code example in the five extension documents was
executed as written. That was true when written and there was nothing keeping
it true -- a renamed parameter, a moved import or a new required field breaks
an example silently, and the person it breaks for is someone following the
document for the first time, who has no way to tell a stale doc from their
own mistake. Documentation nobody runs decays into documentation nobody can
trust.

So every ``python`` block in those documents is extracted and compiled here,
and the self-contained ones are executed. Three kinds of block exist and they
are treated differently:

  * COMPLETE programs -- a registration, a class definition -- are run, in
    document order and sharing one namespace, because a document imports at
    the top and then shows variations. If the API moved, they raise.
  * FRAGMENTS -- `case Op.ADD: ...` inside a `match` -- cannot run, but they
    still have to parse in the surrounding shape, which catches a mnemonic
    renamed out from under the doc.
  * LANGUAGE examples in LANGUAGE.md are asmpython source, not driver code,
    and they are checked against what they CLAIM rather than merely compiled.
    `-7 // 2  # -4, not -3` is an assertion, and so is
    `# error[E0060]`; both are extracted and run. "It compiles" is nearly
    free to satisfy, while the numbers are the sentences a reader relies on
    and the ones that go wrong when the frontend's lowering changes.

Which kind a block is, is DECIDED BY ANALYSIS -- the names it reads without
binding -- rather than by a list of substrings to skip. A list has to be
extended for each new sort of fragment, and the extension always happens after
the fragment has already slipped through unchecked.

The whole file is worth about a second. Its value is entirely in being cheap
enough that nobody is tempted to move it behind a flag.
"""
from __future__ import annotations

import ast
import builtins
import re
import textwrap
from dataclasses import fields
from pathlib import Path

from tests import harness

from asmpython import link as link_registry
from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.target import Target

ROOT = Path(__file__).resolve().parents[3]


def _doc(relative: str) -> Path:
    """Locate one document, wherever the tree currently keeps it.

    `docs/` moved to `archived/docs/` and this module hardcoded the old path,
    so it stopped COLLECTING -- a FileNotFoundError at import time, which
    takes the whole file's tests with it and reads as a broken environment
    rather than as "nothing is checking the documentation any more". It was
    not checking it for as long as it took to rewrite most of LANGUAGE.md.

    Both locations are accepted so the reorganisation can finish either way.
    """
    for base in (ROOT, ROOT / "archived"):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return ROOT / relative        # report the canonical path in the failure


DOCS = tuple(_doc(name) for name in (
    "README.md", "docs/FRONTENDS.md", "docs/BACKENDS.md",
    "docs/TARGETS.md", "docs/LINKERS.md", "docs/LANGUAGE.md"))

def _readers_objects() -> dict:
    """The objects a document says "you have one of these" about.

    Several examples are written against `module`, `function` or `request` --
    things the reader is handed by the compiler, not things they construct. A
    block using one is not runnable as written, and only parsing it would let
    `alloc.frame_size` be renamed with the document still describing it.

    So they are supplied, for real. Nothing here is a mock: a stub with
    permissive `__getattr__` would make every attribute access succeed, which
    is precisely the failure these blocks exist to catch.
    """
    import tempfile
    from asmpython.link.base import LinkRequest

    work = Path(tempfile.mkdtemp(prefix="asmpython-docs-"))
    source = work / "prog.py"
    source.write_text(
        "def main() -> int:\n"
        "    total: int = 0\n"
        "    for i in range(4):\n"
        "        total = total + i\n"
        "    print(total)\n"
        "    return 0\n", encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=source, backend="x86-64"), sink)
    assert result.ok, [d.message for d in sink.diagnostics]

    module = result.module
    function = module.function("main")
    return {
        "module": module,
        "function": function,
        "ins": function.entry.instructions[0],
        "reg": function.entry.instructions[0].dst,
        "target": result.target or target_registry.get("x86_64-linux"),
        "request": LinkRequest(
            artifacts=result.artifacts,
            target=result.target or target_registry.get("x86_64-linux"),
            output=work / "prog", workdir=work),
    }


#: Built once: compiling a program per example would dominate the runtime of
#: a test whose whole point is that it is cheap enough to always run.
_READER = _readers_objects()


def _blocks(path: Path, language: str) -> list[tuple[int, str]]:
    """Every fenced block of `language`, with the line it starts on."""
    text = path.read_text(encoding="utf-8")
    out = []
    for match in re.finditer(rf"```{language}\n(.*?)```", text, re.S):
        line = text.count("\n", 0, match.start()) + 1
        out.append((line, match.group(1)))
    return out


def _all_python_blocks() -> list[tuple[str, int, str]]:
    return [(name, line, body)
            for name in DOCS
            for line, body in _blocks(name, "python")]


def _as_module(source: str) -> str:
    """Put a fragment in the smallest context that makes it parseable.

    Documents show method bodies and `match` arms, which are Python but not
    modules. Wrapping is not cheating: the wrapper is the context the reader
    is told to paste it into, so a fragment that will not parse inside it is
    one that will not parse for them either.

    Wrapping is tried only on a block that does NOT already parse. Deciding
    by pattern instead -- "it contains a `return`, so it is a method body" --
    wraps a perfectly good class definition whose methods return, and every
    such block is then written off as an unrunnable fragment. That is the
    failure mode to fear here: not a false alarm, which someone investigates,
    but a check that quietly stops checking.
    """
    for candidate in (source,
                      "def _method(self):\n" + textwrap.indent(source, "    "),
                      "match _subject:\n" + textwrap.indent(source, "    ")):
        try:
            compile(candidate, "<doc>", "exec")
        except SyntaxError:
            continue
        return candidate
    return source                        # nothing parses; the parse test says so


@harness.cases(
    "doc,line,source",
    [harness.param(*b, id=f"{b[0]}:{b[1]}") for b in _all_python_blocks()])
def test_every_python_example_parses(doc: str, line: int, source: str) -> None:
    """A fragment still has to be syntactically Python.

    This is the weak check that applies to everything, including the blocks
    that cannot run. It catches the most common form of decay -- an example
    edited by hand until it no longer means anything -- and it catches a block
    labelled `python` that is not Python, which is a promise to the reader's
    syntax highlighter and to the reader.
    """
    try:
        compile(_as_module(source), f"{doc}:{line}", "exec")
    except SyntaxError as exc:
        harness.fail(f"{doc}:{line} does not parse as Python: {exc}\n\n{source}")


def _undefined_names(source: str) -> set[str]:
    """Names the block reads without defining or importing.

    This is what separates an example from a fragment, and deciding it by
    analysis rather than by a list of substrings matters: a list has to be
    extended for each new kind of fragment, and the extension always happens
    after the fragment has already slipped through unchecked.
    """
    tree = ast.parse(_as_module(source))
    bound: set[str] = set(dir(builtins))
    read: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Load()):
                read.add(name)
            case ast.Name(id=name) | ast.arg(arg=name):
                bound.add(name)
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) \
                    | ast.ClassDef(name=name):
                bound.add(name)
            case ast.alias(name=name, asname=asname):
                bound.add(asname or name.split(".")[0])
            case ast.ExceptHandler(name=str() as name):
                bound.add(name)
    return read - bound


def _bound_names(source: str) -> set[str]:
    """What executing this block adds to the session's namespace."""
    bound: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Name(id=name, ctx=ast.Store()):
                bound.add(name)
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name)                     | ast.ClassDef(name=name):
                bound.add(name)
            case ast.alias(name=name, asname=asname):
                bound.add(asname or name.split(".")[0])
    return bound


def _sessions() -> dict[str, list[tuple[int, str, bool]]]:
    """Each document, as the sequence of blocks a reader would run.

    A DOCUMENT IS A SESSION. It imports at the top and then shows variations,
    so `register(Target(...))` four sections later is complete prose and an
    incomplete file. Executing each document's blocks in order into one shared
    namespace is what the reader does, and treating each block as an isolated
    file instead reports every one of those as a fragment -- which is how a
    checker ends up checking almost nothing while looking thorough.

    Returns, per document, `(line, source, runnable)`.
    """
    sessions: dict[str, list[tuple[int, str, bool]]] = {}
    for doc in DOCS:
        if doc.name == "LANGUAGE.md":
            continue                      # asmpython source, compiled below
        defined = set(_READER)
        blocks = []
        for line, body in _blocks(doc, "python"):
            runnable = (_as_module(body) == body
                        and not (_undefined_names(body) - defined))
            if runnable:
                defined |= _bound_names(body)
            blocks.append((line, body, runnable))
        sessions[doc] = blocks
    return sessions


_SESSIONS = _sessions()


@harness.cases(
    "doc", [d for d in sorted(_SESSIONS)
            if any(runnable for _, _, runnable in _SESSIONS[d])])
def test_every_document_runs_as_a_session(doc: str) -> None:
    """Execute the document top to bottom. A moved API fails here.

    One namespace for the whole document, because that is the claim a document
    makes: that following it from the top works. A block that raises the
    exception it is demonstrating is the example succeeding -- everything else
    is the document being wrong.
    """
    namespace: dict = dict(_READER)
    executed = 0
    for line, source, runnable in _SESSIONS[doc]:
        if not runnable:
            continue
        try:
            exec(compile(source, f"{doc}:{line}", "exec"), namespace)
        except Exception as exc:                    # noqa: BLE001 -- reporting
            if re.search(rf"raise {type(exc).__name__}\b", source):
                continue                  # the raise IS the example
            harness.fail(f"{doc}:{line} raised {type(exc).__name__}: "
                        f"{exc}\n\n{source}")
        executed += 1
    assert executed, f"no example in {doc} was executed"


def test_enough_examples_are_actually_executed() -> None:
    """Guard the guard.

    Every test above passes trivially if the classifier decides everything is
    a fragment, and one over-broad rule would do it silently while the suite
    stayed green. So the count is pinned: it may rise freely and may not
    collapse. The number is what the documents contain today, not a target.
    """
    executed = sum(1 for blocks in _SESSIONS.values()
                   for _, _, runnable in blocks if runnable)
    total = sum(len(b) for b in _SESSIONS.values())
    assert executed >= 15, (
        f"only {executed} of {total} documented examples are being executed; "
        f"the classifier is treating real examples as fragments")


def test_the_documented_target_fields_are_the_actual_fields() -> None:
    """TARGETS.md prints the constructor. Adding a field must break it.

    A field list is exactly the kind of thing that goes stale invisibly:
    `cc_names` was added and the document kept describing the ten-field
    version, which reads as complete and is not.
    """
    documented = re.search(
        r"```python\nTarget\((.*?)\)\n```",
        _doc("docs/TARGETS.md").read_text(encoding="utf-8"), re.S)
    assert documented, "TARGETS.md no longer lists the Target fields"
    listed = [n.strip() for n in documented.group(1).replace("\n", " ").split(",")]
    assert listed == [f.name for f in fields(Target)]


def test_the_documented_toolchains_are_the_shipped_ones() -> None:
    """The table in LINKERS.md names what ships. So does the registry."""
    link_registry.load_builtin()
    table = re.findall(r"^\| `(\w[\w-]*)` \|",
                       _doc("docs/LINKERS.md").read_text(encoding="utf-8"),
                       re.M)
    assert set(table) == set(link_registry.available()) - {"my-linker"}


def test_the_readme_layout_lists_the_real_packages() -> None:
    """The layout block is a map. A package it omits is a package nobody finds."""
    text = _doc("README.md").read_text(encoding="utf-8")
    block = re.search(r"```\nsrc/asmpython/\n(.*?)```", text, re.S)
    assert block, "the README no longer contains a layout block"
    listed = set(re.findall(r"^  (\w+)\(?s?\)?/", block.group(1), re.M))
    # `backend(s)/` means both `backend/` and `backends/` exist.
    actual = {p.name for p in (ROOT / "src/asmpython").iterdir()
              if p.is_dir() and not p.name.startswith("_")}
    unlisted = {name for name in actual
                if name not in listed and name.rstrip("s") not in listed}
    assert not unlisted, f"packages missing from the README layout: {unlisted}"


#: Every backend the README's layout claims exists, and the target each
#: reaches. The point is that a documented backend can actually be asked for.
@harness.cases("backend,target", [
    ("c", "c"),
    ("x86-64", "x86_64-linux"),
    ("arm64", "aarch64-none"),
    ("jvm", "jvm"),
])
def test_every_documented_backend_compiles_a_program(
        backend: str, target: str, tmp_path: Path) -> None:
    """`--backend X --target Y` reaches artifacts for each documented pair.

    Not executed -- that is what the per-backend suites do, with a toolchain.
    This asserts the narrower thing the docs promise: the backend exists, is
    registered under the name written down, accepts the target written down,
    and emits something.
    """
    source = tmp_path / "prog.py"
    source.write_text("def main() -> int:\n    print(1 + 1)\n    return 0\n",
                      encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(
        Options(source=source, backend=backend,
                target=target_registry.get(target)), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    assert result.artifacts


def _language_examples() -> list[tuple[int, str]]:
    """The blocks in LANGUAGE.md that are whole programs.

    A block showing one statement is describing a rule, not offering a
    program; only the ones defining `main` are compilable on their own.
    """
    return [(line, body) for line, body in _blocks(_doc("docs/LANGUAGE.md"),
                                                   "python")
            if "def main(" in body]


@harness.cases("line,source",
                         [harness.param(*b, id=f"LANGUAGE.md:{b[0]}")
                          for b in _language_examples()])
def test_every_language_example_compiles(line: int, source: str,
                                         tmp_path: Path) -> None:
    """LANGUAGE.md says what the subset accepts. asmpython decides.

    A document describing a language is a claim the implementation can
    falsify, which makes this the one doc test that can find a compiler bug
    rather than a documentation bug -- and it is the doc a reader trusts most,
    because it is the one they check their own program against.
    """
    path = tmp_path / "example.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path, backend="c"), sink)
    assert result.ok, (f"LANGUAGE.md:{line} does not compile: "
                       + "; ".join(d.message for d in sink.diagnostics))


# --------------------------------------------------------------------------
# LANGUAGE.md makes claims with numbers in them, and those are the ones worth
# checking. "It compiles" is nearly free to satisfy; "-7 // 2 is -4" and
# "this line reports E0060" are the sentences a reader actually relies on,
# and the ones that go wrong when the frontend's lowering changes.

#: Values the snippets read but do not bind. Declared explicitly rather than
#: inferred, so a snippet using a new name fails loudly instead of being
#: silently given something plausible.
_SNIPPET_SCOPE = {
    "c": "int = 1", "flag": "int = 1", "n": "int = 2",
    "x": "float = 3.0", "y": "float = 2.0",
}

#: `expr    # <value>, not <what Python does>`, and the "here:" column of the
#: table contrasting Python's answer with this language's.
_VALUE_CLAIM = re.compile(
    r"^\s*(?P<expr>[^#\n]+?)\s*#\s*(?:(?P<a>-?[\d.]+), not\b"
    r"|Python:\s*\S+\s+here:\s*(?P<b>-?[\d.]+))")
_ERROR_CLAIM = re.compile(r"error\[(?P<code>E\d+)\]")


def _as_main(body: str) -> str:
    """A snippet, as the body of `main`, with the names it reads declared."""
    reads = _undefined_names(body) & set(_SNIPPET_SCOPE)
    preamble = "".join(f"    {n}: {_SNIPPET_SCOPE[n]}\n" for n in sorted(reads))
    return ("def main() -> int:\n" + preamble
            + textwrap.indent(body.rstrip("\n"), "    ") + "\n    return 0\n")


def _run(source: str, tmp_path: Path) -> tuple[list[str], DiagnosticSink]:
    """Compile and interpret, returning printed lines and the diagnostics."""
    from io import StringIO
    from asmpython.ir.interpreter import Interpreter

    path = tmp_path / "snippet.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path), sink)
    if not result.ok:
        return [], sink
    out = StringIO()
    Interpreter(result.module, out=out).run("main")
    return out.getvalue().splitlines(), sink


def _value_claims() -> list[tuple[int, str, str]]:
    claims = []
    for line, body in _blocks(_doc("docs/LANGUAGE.md"), "python"):
        for offset, text in enumerate(body.split("\n")):
            match = _VALUE_CLAIM.match(text)
            if match and not _ERROR_CLAIM.search(text):
                want = match.group("a") or match.group("b")
                claims.append((line + offset + 1, match.group("expr"), want))
    return claims


@harness.cases(
    "line,expr,want",
    [harness.param(*c, id=f"LANGUAGE.md:{c[0]}") for c in _value_claims()])
def test_every_documented_value_is_the_value(line: int, expr: str, want: str,
                                             tmp_path: Path) -> None:
    """`-7 // 2  # -4, not -3` is an assertion. Treat it as one.

    These four lines are the entire reason the frontend lowers `//` and `%`
    itself instead of leaving them to the backends, so they are the numbers
    most likely to be quietly wrong after a change to that lowering -- and a
    document is the last place anyone thinks to look for a regression.

    Whether the answer is an int or a float is part of the claim, not a
    detail: `0 and 2.5` yielding `0.0` rather than `0` is the documented
    difference from Python, so the printed form is checked too.
    """
    printed, sink = _run(_as_main(f"print({expr})"), tmp_path)
    assert printed, (f"LANGUAGE.md:{line}: `{expr}` did not compile: "
                     + "; ".join(d.message for d in sink.diagnostics))
    got = printed[0]
    assert float(got) == float(want), (
        f"LANGUAGE.md:{line} says `{expr}` is {want}; it is {got}")
    assert ("." in got) == ("." in want), (
        f"LANGUAGE.md:{line} says `{expr}` is {want}, which is "
        f"{'a float' if '.' in want else 'an int'}; it printed {got}")


def _error_claims() -> list[tuple[int, str, str]]:
    claims = []
    for line, body in _blocks(_doc("docs/LANGUAGE.md"), "python"):
        for offset, text in enumerate(body.split("\n")):
            match = _ERROR_CLAIM.search(text)
            if match:
                # The whole block is the context. A prefix is not enough:
                # `print(later)` only errors because of the `if` above it,
                # and cutting at the marked line decapitates a loop whose
                # body comes after it.
                claims.append((line + offset + 1, match.group("code"), body))
    return claims


@harness.cases(
    "line,code,source",
    [harness.param(*c, id=f"LANGUAGE.md:{c[0]}-{c[1]}") for c in _error_claims()])
def test_every_documented_diagnostic_is_produced(line: int, code: str,
                                                 source: str,
                                                 tmp_path: Path) -> None:
    """A doc naming an error code is promising that code, not merely an error.

    The code is what a reader searches for and what a tool matches on, so
    "it still fails, with a different number" is a broken promise. Renaming a
    diagnostic without updating the document is exactly the change that makes
    this fail, and there is no other way to notice it.
    """
    _, sink = _run(_as_main(source), tmp_path)
    reported = {d.code for d in sink.diagnostics}
    assert code in reported, (
        f"LANGUAGE.md:{line} promises {code}; got "
        f"{sorted(reported) or 'no diagnostic at all'}")
