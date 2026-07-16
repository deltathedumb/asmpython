# Compiler Extension System

## Overview

The native compiler (`asmpython/_compiler/`) supports opt-in syntax
extensions, activated for an entire compile *invocation* via the `--ext`
CLI flag:

```sh
asmpython build myfile.py --ext constants
```

```python
const MAX_USERS = 100
```

`--ext NAME` (repeatable: `--ext constants --ext other`) activates a
compiler extension for the whole build. The only built-in extension is
`constants`, which adds `const NAME [: annotation] = value` declarations.
Off by default — a source file's grammar never changes without this
explicit, outside-the-source opt-in from whoever invokes the compiler.

This is implemented as real compiler syntax handled by the existing lexer,
recursive-descent parser, AST, semantic analyzer, and shared IR
lowering/legacy codegen — not as runtime function calls, and not through
source-text preprocessing.

**There is no in-source activation directive.** An earlier design used
`extend <name>` / `retract <name>` module-level statements for forward-only,
per-file activation; this was removed in favor of CLI-only activation so a
program's grammar is never a function of anything the program's own source
controls. If you're looking for `extend`/`retract`, they no longer exist —
use `--ext NAME` instead.

## Contextual (soft) keyword

`const` is never added to the lexer's `KEYWORDS` set -- it tokenizes as
plain `NAME`, exactly like `match` already does. The parser recognizes it
only when a speculative lookahead confirms the right shape
(`_looks_like_const_decl` in `parser.py`, following the same
save-position/parse/rewind idiom as the existing `_looks_like_match_stmt`).

This means `const` remains an ordinary identifier whenever the shape doesn't
match or `constants` isn't active for this build:

```python
const = 10           # ordinary assignment
const.attr = 1        # ordinary attribute assignment
```

## Activation semantics

- **Whole-invocation, uniform across every module.** `--ext` applies to
  the entire compile — the entry module and every module a whole-program
  compile merges in (`program.py`'s `load_program` builds a fresh `Parser`
  per module, but passes the same `active_extensions` set to each one).
  There is no way for one file to opt in while a sibling file opts out;
  activation is a property of the *build*, not of any individual file.
- **`const NAME = value` requires an initializer.** `const NAME` alone is a
  hard error (`P010`), regardless of whether an annotation is present.
- **`const NAME: annotation = value`** uses the compiler's existing
  `_parse_type_annotation`/annotation-validation machinery -- a mismatched
  initializer (`const count: int = "five"`) fails with the normal type-error
  diagnostics, unchanged.
- **Module-scope only.** `const` is rejected anywhere nested -- inside a
  function, class body, `if`/`for`/`while`/`try`/`with`, or any other suite
  -- with diagnostic `P012: 'const' may only appear at module scope`.
  Enforced via a single `Parser._suite_depth` counter incremented/
  decremented by `_parse_block` (covering all of its call sites at once)
  plus an explicit check inside `_parse_classdef`'s own bespoke class-body
  loop (which does not go through `_parse_block`).
- **Binding, not deep immutability.** `const` locks the *name* against every
  future rebinding form: direct reassignment, augmented assignment (`+=`
  etc.), `del`, multiple assignment (`a = b = ...`), tuple/list
  destructuring (including starred forms and `for` loop targets),
  assignment-expression (walrus) targets, `except ... as` binding, and
  import aliases that would overwrite the name. All raise
  `E081: cannot reassign const 'NAME'` with the original declaration's
  location. Mutating an object a const name refers to is unaffected:

  ```python
  const values = [1, 2]
  values.append(3)   # allowed -- rebinding `values` itself is not
  values = []         # E081: cannot reassign const 'values'
  ```

- **Known limitation:** `def foo(): ...` appearing *before* `const foo = 1`
  in source order is caught (`E082: cannot declare const 'foo': a function
  or class with that name already exists`). The reverse order (`const foo =
  1` followed later by `def foo(): ...`) is not caught -- this matches the
  compiler's existing two-pass structure (every function/class name is
  collected in a pre-pass that runs *before* any module-body statement,
  including `const` declarations, is walked), and mirrors real CPython's own
  behavior of letting a later `def` silently clobber an earlier binding.
  This is a deliberate, documented limitation, not a bug this feature is
  trying to close.

## Module isolation and parallel-compilation isolation

Every `Parser` instance owns its own fresh `ExtensionContext`
(`Parser.__init__` sets `self.ext_ctx = ExtensionContext()`, then activates
each name in `active_extensions` immediately, before any token is parsed).
No extension state is ever stored globally. This gives three isolation
guarantees for free:

1. **Two `Parser` instances in the same process never share state** --
   verified directly in `tests/test_extensions.py`.
2. **Whole-program compiles apply the same activation set to every merged
   module.** `program.py`'s `load_program` builds a genuinely fresh
   `Parser` for the entry module and for every imported module it merges
   (two separate `Parser(...)` construction sites), both fed the same
   `active_extensions` — so a project's grammar is consistent across every
   file. Verified in `tests/test_program_isolation.py`, including the
   nuance that a helper module that fails to parse (e.g. because the build
   didn't pass `--ext constants` but the helper uses `const`) is silently
   dropped from the merge (matching `program.py`'s existing, deliberate
   leniency toward modules it can't parse) rather than aborting the whole
   compile.
3. **Separate compiler invocations trivially can't leak state** -- separate
   OS processes never share Python object state. No test needed; this is
   just how processes work.

## Extension-author registration API

Defined in `asmpython/_compiler/extensions.py`:

- `CompilerExtension`: base class with `name`, `version`, `requires` (dict
  of extension name -> minimum version), `conflicts` (set of extension
  names), `statement_handlers()` (dict of contextual keyword -> handler
  name), and `activate(context)`/`deactivate(context)` lifecycle hooks.
- `ExtensionContext`: per-`Parser` state (`is_active`, `activate`, `retract`,
  `handler_for`). Activation and retraction are both fully transactional --
  every validation check (unknown extension, conflict, missing/mismatched
  dependency, duplicate statement-prefix registration, blocked retraction)
  runs and completes *before* any state mutation, so a failed
  activation/retraction call leaves the context completely unchanged.
  `retract` has no CLI-facing use today (there's no way to retract
  mid-build), but remains a real, tested method on `ExtensionContext` for
  programmatic/future use.
- `_REGISTRY`: a module-level dict of extension name -> `CompilerExtension`
  subclass (class references, not instances -- each activation constructs
  its own fresh instance). `register_extension(cls)` is the public hook for
  registering a new built-in extension.

**`const` stays a special case:** `parser.py` dispatches `const` directly
(guarded by `self.ext_ctx.is_active("constants")`) rather than routing
through the generic `ExtensionContext.handler_for(...)` indirection --
`const` has its own dedicated shape lookahead (`_looks_like_const_decl`),
which a generic dispatch mechanism has no way to replicate for an
arbitrary third-party keyword (see below). This is unrelated to how many
extensions are registered; it's a property of `const`'s specific grammar.

## User-authored extensions

`asmpython.Extension(...)` (defined in `asmpython/extend.py`, exposed as
`asmpython.Extension`) is the public, third-party-facing way to register a
new extension without touching this file's source. A plugin is an
ordinary Python file, run by the *host* CPython interpreter (never
compiled by asmpython itself), loaded via `--ext path/to/plugin.py`:

```python
# my_plugin.py
import asmpython

def handle_let(parser, pos):
    from asmpython._compiler import ast_nodes as A

    name = parser._expect("NAME").value
    parser._expect("OP", "=")
    value = parser._parse_expr()
    parser._expect("NEWLINE")
    return A.Assign(target=name, value=value, pos=pos)

asmpython.Extension(id="let_binding", statement_handlers={"let": handle_let})
```

```sh
asmpython build myfile.py --ext my_plugin.py
```

`--ext` accepts either a bare registered id (`constants`) or a filesystem
path (distinguished by whether it names an existing file) -- a path is
exec'd first, so the plugin's `Extension(...)` call registers before
activation is attempted; the id it registers is then what actually gets
activated. A single `--ext path/to/plugin.py` is therefore enough to both
load and activate a plugin defining exactly one extension. A plugin that
registers zero or more than one `Extension` is a build-time error (there'd
be no unambiguous id for `--ext` to activate); register each and pass
their ids explicitly via separate `--ext` flags in that case.

**Statement-handler contract:** each `statement_handlers` value is a
callable `(parser, pos) -> ast_nodes.Stmt`. `parser` is the live
`asmpython._compiler.parser.Parser` instance, already positioned just past
the claimed keyword -- the callback drives it directly via its private
`_eat`/`_check`/`_expect`/`_parse_expr`/etc. methods and constructs a real
node from `asmpython._compiler.ast_nodes` (a private module -- there is no
separate public AST surface yet, so this is the de facto contract for
now). Internally, this reuses the exact same `ExtensionContext.
handler_for(...)` generic-dispatch mechanism `docs/EXTENSIONS.md` already
described as "exists but nothing consumes it yet" before this feature --
`parser.py`'s main statement-dispatch loop now checks it for every bare
`NAME` token, right after the `const`-specific check.

**Important trade-off, by design:** unlike `const`/`match`, a
plugin-claimed keyword has no shape lookahead the parser can check ahead
of time (the whole point is the plugin decides its own grammar) -- so once
its extension is active, the keyword unconditionally becomes a statement
prefix for the rest of that compile. It can no longer double as a plain
variable name. This only ever applies when the invoker explicitly opted
in via `--ext`, matching the "no consent, no grammar change" principle
this whole activation model is built on.

This is v1, deliberately narrow: an extension can declare metadata
(id/version/requires/conflicts, reusing the exact same transactional
activation machinery as the built-in `constants`) and claim new
statement-prefix keywords. The end goal is letting an extension reshape
the language far more broadly -- up to and including replacing it with a
different grammar entirely -- which is not implemented yet.

## Backends and linkers

The same "public registration API, activated via a CLI flag" pattern
extends to codegen backends and linkers:

- `asmpython.Backend(name=..., impl=...)` registers a third-party codegen
  backend, reachable via `--backend NAME`. `impl` must conform to
  `asmpython._compiler.ir.IRBackend` (`compile(module, args) -> dict[str,
  bytes]` and `link(objects, args) -> dict[str, bytes]`; `requested_args`/
  `default_linker` optional). Driver.py's `_run_backend_registered`
  handles it with a plain compile-then-link-then-write, the same shape as
  the built-in `ternary` backend -- no bespoke per-backend wiring the way
  `x86-64` gets (ABI shims, runtime object linking, GCC resolution).
- `asmpython.Linker(name=..., impl=...)` registers a third-party linker,
  reachable via `--linker NAME`. `impl` must expose `link(ctx: dict) ->
  bytes` (`requested_args` optional).

Both registries (`asmpython/_backends/__init__.py`, `asmpython/_linkers/
__init__.py`) are plain Python dicts, consulted only as a fallback after
the built-in names (`legacy`/`x86-64`/`ternary` for backends, `gcc`/
`builtin` for linkers) — CPython-hosted compilation only, **not yet safe
under self-hosting**: asmpython's self-hosted subset has no first-class
module/object values storable in a dict today, and these registries hold
live backend/linker objects. See RESUME.md's "Pending 2.0.0 workload"
section, "First-class module values", for the tracked follow-up.

## Current lexer limitation

The lexer (`lexer.py`) eagerly tokenizes the entire file before parsing
begins -- there is no streaming/lazy retokenization hook. This means an
extension can only introduce new *grammatical combinations* of token forms
the lexer already recognizes (`NAME`, `OP`, `NEWLINE`, `INDENT`/`DEDENT`,
string/number literals, etc.) -- it cannot introduce genuinely new lexical
forms.

Supported today (composed entirely of existing token forms):

```python
const x = 5
interface Writer:
    ...
sealed class Result:
    ...
```

Not supported without further lexer work (these need new lexical forms the
lexer doesn't tokenize at all):

```python
result?          # a new postfix operator token
10u32             # a new numeric-literal suffix
a <|> b           # a new operator token
```

For future extensions needing genuinely new lexical forms, two paths exist:
lex a stable superset of plausible tokens up front and let extensions choose
which ones carry meaning, or refactor the lexer to lazy/streaming
tokenization. Neither is needed for `constants` and neither is implemented
by this feature.

## Security and reproducibility notes for third-party extensions

There is no third-party extension loading today -- `register_extension()` is
an internal hook, not exposed via a plugin/entry-point mechanism. The
registry design doesn't preclude adding this later, but doing so safely
would require, at minimum: sandboxing extension `activate()`/`deactivate()`
hooks (they currently run arbitrary Python with full `ExtensionContext`
access), a trust/signing story for third-party extension code, and a
reproducibility guarantee (the same source file should compile identically
regardless of which extensions happen to be installed/registered in a given
compiler environment, unless the invocation's own `--ext` flags explicitly
opt in). None of this is needed for the built-in `constants` extension --
it ships in-tree and is trusted like any other compiler source -- but a
future extension author shouldn't assume the registry is already hardened
for arbitrary third-party use.

## IR / backend neutrality

The extension system is entirely a frontend concern:

- `ConstDecl` is the only extension-defined AST node that reaches later
  phases (there's no more `Extend`/`Retract` transient-node pair; activation
  is settled by the CLI before parsing starts and needs no in-tree AST
  representation at all). After semantic validation, `ConstDecl` lowers
  *exactly* like an ordinary initialized `Assign` -- `ir_lower.py`'s
  `_lower_stmt` and `codegen.py`'s `gen_stmt` each normalize a `ConstDecl`
  into an equivalent `Assign` at the top of the function, before any other
  dispatch runs. This means:
  - The IR-based backends (`asmpython/_backends/x86_64/`,
    `asmpython/_backends/ternary/`) need zero extension-specific code --
    they only ever consume already-lowered IR, and a `ConstDecl` produces
    the exact same `IRInstr("store", ...)` sequence an `Assign` would.
  - All four legacy NASM codegen targets (`LinuxCodegen`, `WindowsCodegen`,
    `FreestandingCodegen`, `Freestanding16Codegen`) get this for free too,
    since they all inherit `gen_stmt` from the same base `Codegen` class.
  - A small number of auxiliary whole-program/frame-layout passes that
    pattern-match on `A.Assign` directly (rather than going through
    `_lower_stmt`/`gen_stmt`'s own entry points) needed their own explicit
    `ConstDecl` branch: `codegen.py`'s per-function frame-layout pre-pass
    (`_cl_walk`, which reserves scratch slots for list/dict literals) and
    `program.py`'s cross-module value-export/call-rename passes
    (`_toplevel_value_assigns`, `_rename_call_targets`). These are
    documented inline at each call site.

## Testing

- `tests/cases/45x_const_*.py` (positive: compiles and runs, stdout-diffed)
  and `tests/cases_fail/const_*.py` (negative: `# expect-error: <substring>`)
  -- the standard `tests/runner.py` convention. A case that needs an
  extension active declares it via a `# ext: constants` marker comment
  (parsed by `tests/runner.py`'s `_parse_ext`, which appends `--ext NAME`
  to the compile command) instead of an in-source `extend` directive.
  Covers activation via `--ext`, typed constants, the initializer
  requirement, every rebinding form, the mutation-vs-rebinding distinction,
  module-scope violations for `const` across every suite shape (function/
  class/if/loop/try), the def/class-vs-const ordering asymmetry, and the
  plain-variable non-regression case (`const` used as an ordinary
  identifier when `constants` isn't active).
- `tests/test_extensions.py` -- `unittest`-based unit tests for
  `ExtensionContext`'s dependency/conflict/cycle/transactional-rollback
  logic, using small dummy `CompilerExtension` subclasses (the only
  built-in extension, `constants`, has no dependents/conflicts of its own
  to exercise this against), plus a same-process two-`Parser`-instances
  isolation test (one constructed with `constants` in `active_extensions`,
  the other without).
- `tests/test_program_isolation.py` -- new, non-globbed whole-program
  cross-module activation test (not part of the `tests/cases*` convention,
  since `tests/runner.py`'s existing harness never drives `program.py`'s
  whole-program merge path). Run via `python -m unittest
  tests.test_program_isolation`.
- `tests/test_extend.py` -- `unittest`-based coverage for the public
  `asmpython.Extension`/`Backend`/`Linker` authoring API: metadata-only
  registration and activation, a real statement-handler round-trip
  (registers a trivial `let NAME = value` extension and confirms it
  produces a real `A.Assign` node when active and an ordinary identifier
  when not), the statement-prefix collision diagnostic firing across two
  independently plugin-registered extensions, and `Backend`/`Linker`
  registration retrievability. Run via `python -m unittest
  tests.test_extend`.
