# Compiler Extension System

## Overview

The native compiler (`asmpython/_compiler/`) supports opt-in syntax
extensions, activated per-file with module-level directives:

```python
extend constants
const MAX_USERS = 100
retract constants
const MAX_USERS_2 = 100   # invalid: constants is no longer active
```

`extend <name>` activates a compiler extension for the rest of the current
module; `retract <name>` deactivates it. The only built-in extension is
`constants`, which adds `const NAME [: annotation] = value` declarations.

This is implemented as real compiler syntax handled by the existing lexer,
recursive-descent parser, AST, semantic analyzer, and shared IR
lowering/legacy codegen -- not as runtime function calls, and not through
source-text preprocessing.

## Contextual (soft) keywords

`extend`, `retract`, and `const` are never added to the lexer's `KEYWORDS`
set -- they tokenize as plain `NAME`, exactly like `match` already does. The
parser recognizes each one only when a speculative lookahead confirms the
right shape (`_looks_like_extend_stmt`, `_looks_like_retract_stmt`,
`_looks_like_const_decl` in `parser.py`, following the same
save-position/parse/rewind idiom as the existing `_looks_like_match_stmt`).

This means all three remain ordinary identifiers whenever the shape doesn't
match or the relevant extension isn't active:

```python
extend = 5          # ordinary assignment
retract = False      # ordinary assignment
const = 10           # ordinary assignment (no NAME follows `const`)
extend.attr = 1       # ordinary attribute assignment
```

## `extend` / `retract` semantics

- **Module-scope only.** Both directives are rejected anywhere nested --
  inside a function, class body, `if`/`for`/`while`/`try`/`with`, or any
  other suite -- with `ExtensionScopeError`-equivalent diagnostic
  `P012: 'extend'/'retract' may only appear at module scope`. Enforced via a
  single `Parser._suite_depth` counter incremented/decremented by
  `_parse_block` (covering all of its call sites at once) plus an explicit
  check inside `_parse_classdef`'s own bespoke class-body loop (which does
  not go through `_parse_block`).
- **Forward-only, per-file, transactional.** Activation/retraction only
  affects parsing from that point forward in the current file. Every
  `Parser` owns a fresh `ExtensionContext` (see below), so there is no way
  for one file's directives to affect another file, or an earlier compiler
  run to affect a later one.
- **Duplicate activation is an error** (`P013: extension 'X' is already
  active`), not idempotent -- this is intentional so a stray double
  `extend constants` is caught rather than silently ignored.
- **Retracting an inactive extension is an error** (`P014: extension 'X' is
  not active`).
- Retracting only changes what grammar/handlers are active *going forward*.
  It does not erase semantics already attached to already-parsed
  declarations: `extend constants; const MAX = 100; retract constants` still
  leaves `MAX` permanently const-locked for the rest of the module.
- Extensions declare `requires`/`conflicts`. Activating one loads/validates
  its dependencies first (missing dependency: `P016`; version mismatch:
  `P016`); conflicting extensions can't be active simultaneously (`P015`,
  checked in both directions); retracting an extension another active
  extension depends on is blocked (`P017`).

## The built-in `constants` extension

```python
extend constants
const MAX_USERS = 100
const RATE: float = 0.05
```

- `const NAME = value` requires an initializer. `const NAME` alone is a hard
  error (`P010`), regardless of whether an annotation is present.
- `const NAME: annotation = value` uses the compiler's existing
  `_parse_type_annotation`/annotation-validation machinery -- a mismatched
  initializer (`const count: int = "five"`) fails with the normal type-error
  diagnostics, unchanged.
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
(`Parser.__init__` sets `self.ext_ctx = ExtensionContext()`). No extension
state is ever stored globally. This gives three isolation guarantees for
free:

1. **Two `Parser` instances in the same process never share state** --
   verified directly in `tests/test_extensions.py`.
2. **Whole-program compiles never leak extension state between modules.**
   `program.py`'s `load_program` builds a genuinely fresh `Parser` for the
   entry module and for every imported module it merges (two separate
   `Parser(...)` construction sites) -- so `main.py` doing `extend constants`
   has zero effect on how `helper.py` parses, even when `main.py` imports
   `helper.py`. Verified in `tests/test_program_isolation.py`, including the
   nuance that a helper module using `const` without its own `extend
   constants` simply fails to parse and is silently dropped from the merge
   (matching `program.py`'s existing, deliberate leniency toward modules it
   can't parse) rather than aborting the whole-program compile.
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
- `_REGISTRY`: a module-level dict of extension name -> `CompilerExtension`
  subclass (class references, not instances -- each activation constructs
  its own fresh instance). `register_extension(cls)` is the public hook for
  registering a new built-in extension.

**Current v1 simplification:** with exactly one built-in extension,
`parser.py` dispatches `const` directly (guarded by
`self.ext_ctx.is_active("constants")`) rather than routing through the
generic `ExtensionContext.handler_for("const")` indirection. The generic
dispatch path still exists and is fully exercised by
`tests/test_extensions.py`'s dummy extensions -- it's simply not yet wired
into the parser's main dispatch loop, since doing so for a single extension
would add indirection with no present benefit. A second built-in extension
would be the natural point to wire it in.

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
compiler environment, unless the file's own `extend` directives explicitly
opt in). None of this is needed for the built-in `constants` extension --
it ships in-tree and is trusted like any other compiler source -- but a
future extension author shouldn't assume the registry is already hardened
for arbitrary third-party use.

## IR / backend neutrality

The extension system is entirely a frontend concern:

- `Extend`/`Retract` are transient AST nodes: they mutate the parser's
  `ExtensionContext` as they're parsed and are then filtered out of the
  final `Module.body` (see `Parser.parse()`) before sema, IR lowering, or
  codegen ever run. None of those stages need to know these directives
  existed.
- `ConstDecl` is permanent (it reaches sema/IR-lowering/codegen), but after
  semantic validation it lowers *exactly* like an ordinary initialized
  `Assign` -- `ir_lower.py`'s `_lower_stmt` and `codegen.py`'s `gen_stmt`
  each normalize a `ConstDecl` into an equivalent `Assign` at the top of the
  function, before any other dispatch runs. This means:
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
  and `tests/cases_fail/const_*.py` / `extend_*.py` / `retract_*.py`
  (negative: `# expect-error: <substring>`) -- the standard
  `tests/runner.py` convention, covering activation/deactivation, typed
  constants, the initializer requirement, every rebinding form, the
  mutation-vs-rebinding distinction, unknown extensions, duplicate
  activation, retraction of an inactive extension, module-scope violations
  for `extend`/`retract`/`const`, the def/class-vs-const ordering asymmetry,
  and the plain-variable non-regression cases (`extend`/`retract`/`const`
  used as ordinary identifiers).
- `tests/test_extensions.py` -- `unittest`-based unit tests for
  `ExtensionContext`'s dependency/conflict/cycle/transactional-rollback
  logic, using small dummy `CompilerExtension` subclasses (the only
  built-in extension, `constants`, has no dependents/conflicts of its own
  to exercise this against), plus a same-process two-`Parser`-instances
  isolation test.
- `tests/test_program_isolation.py` -- new, non-globbed whole-program
  cross-module isolation test (not part of the `tests/cases*` convention,
  since `tests/runner.py`'s existing harness never drives `program.py`'s
  whole-program merge path). Run via `python -m unittest
  tests.test_program_isolation`.
