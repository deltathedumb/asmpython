# asmpython — Python Feature Support Breakdown

A feature-by-feature map of **the Python language** (the language, not CPython
internals) against what the asmpython compiler supports today. This is about
*language semantics a program can use*, not CPython implementation details
(reference counting, the GIL, bytecode, C-API) — those are out of scope by
design, since asmpython compiles to native code rather than implementing
CPython.

Legend:

- ✅ **Supported** — parses, type-checks, and emits correct native code.
- ⚠️ **Partial** — works in a limited form; the restriction is noted. **Some ⚠️ rows
  type-check (pass the front-end) but have no codegen yet** — driven by self-host
  work, where the front-end was advanced ahead of codegen. Such rows say "codegen
  pending"; don't rely on them compiling correctly.
- ❌ **Not supported** — rejected (or unimplemented). Usually a clear compile error.

> Scope note: asmpython compiles a *statically-typed subset* of Python to
> x86-64. Every value is a uniform 8-byte slot; collections are homogeneous;
> types are inferred, not dynamic. So some "supported" features carry a
> static-typing caveat that pure CPython doesn't have.
>
> Last synced to the compiler: 2026-06-09. Re-derive with
> `python -m selfhost.check` and the test suite when in doubt.

---

## Lexical / literals

| Feature | Status | Notes |
|---|:---:|---|
| Integer literals (decimal) | ✅ | 64-bit signed throughout (no bignum). |
| Hex / octal / binary literals (`0x1F`, `0o17`, `0b1010`) | ✅ | |
| Underscore digit separators (`1_000_000`) | ✅ | |
| Float literals (`3.14`, `1.5e-3`, `1e10`) | ✅ | IEEE-754 double. Leading-dot `.5` — ⚠️ lexer requires a digit before `.`. |
| Complex literals (`3j`) | ❌ | No complex type. |
| String literals `'...'` / `"..."` | ✅ | |
| Triple-quoted strings `"""..."""` | ✅ | Span newlines; module/class-body ones act as dropped docstrings. |
| Escape sequences `\n \t \r \0 \\ \' \"` | ✅ | `\x41` / `\u`/`\N{}` — ❌. |
| Raw strings `r"..."` | ❌ | |
| Byte strings `b"..."` | ❌ | No `bytes` type. |
| f-strings `f"{x}"` | ⚠️ | Expressions work; `{{`/`}}` escapes work. Conversions `!r`/`!s` and format specs `:.2f` are **stripped, not applied**. |
| Implicit string concatenation (`"a" "b"`) | ✅ | Including across f-strings and newlines inside parens. |
| `True` / `False` / `None` | ⚠️ | Parse as the ints `1` / `0` / `0`. No distinct `None`/`bool` type — `x is None` is `x == 0`. |
| Line continuation `\` | ✅ | |

## Numbers & arithmetic

| Feature | Status | Notes |
|---|:---:|---|
| `+ - * / // %` | ✅ | `/` is true division → float (even int/int); `//` floor. |
| `**` (power) | ❌ | `**` lexes (used for `**kwargs`) but is **not** an arithmetic operator yet. |
| Unary `- + ~` | ✅ | |
| Mixed int/float arithmetic | ✅ | Auto-promotes to float. |
| Bitwise `& \| ^ ~ << >>` | ✅ | Ints only (sema rejects floats). |
| Augmented assignment (`+= -= *= //= %= &= \| ^= <<= >>=`) | ✅ | On names **and** `self.x` attributes. `**=` lexes but unsupported. |
| `int()` / `float()` / `str()` conversions | ✅ | `int(s, base)` supported (incl. base 0 with `0x`/`0o`/`0b`). |
| `abs()` | ✅ | |
| `divmod()`, `round(x, n)` | ⚠️ | `round` partial; `divmod` ❌. |
| Arbitrary-precision ints (bignum) | ❌ | 64-bit only; overflow wraps. |
| `Decimal`, `Fraction`, `complex` | ❌ | |

## Strings

| Feature | Status | Notes |
|---|:---:|---|
| Concatenation `+`, repeat `*` | ✅ | |
| Equality `==` / `!=` | ✅ | |
| Ordering `<` `<=` `>` `>=` | ⚠️ | Wired in the runtime; lexicographic ordering exposure is incomplete. |
| Indexing `s[i]`, negative index | ✅ | Yields a fresh 1-char string. |
| Slicing `s[a:b]`, `s[a:b:c]` | ✅ | Including step and negative indices. |
| Membership `in` / `not in` | ✅ | |
| Iteration `for ch in s` | ✅ | |
| `len(s)` | ✅ | |
| Methods: `.upper .lower .strip .lstrip .rstrip .startswith .endswith .find .count .replace .split .join .splitlines .partition .rsplit` | ✅ | `.split(sep, maxsplit)` accepts the maxsplit arg (front-end); codegen ignores it. `.splitlines()` splits on `\n` (trailing newline yields no empty final element). |
| Predicates: `.isdigit .isalpha .isalnum .isspace .isupper .islower` | ✅ | ASCII-only. Empty string is False; cased predicates require ≥1 cased char. |
| `.format()` | ❌ | Use f-strings. |
| `.format_map`, `.encode`, `.title`, `.zfill`, `.center`, `.ljust`/`.rjust`, `.isidentifier` … | ⚠️ | Not implemented — but an unmodeled `.method()` on a str-typed value type-checks as opaque (front-end leniency for the exception-as-string case), so it won't compile correctly. |
| String formatting `%` operator | ❌ | |

## Collections

| Feature | Status | Notes |
|---|:---:|---|
| Lists `[...]` | ⚠️ | **Homogeneous**: all-int, all-str, all-float, all-instances-of-one-class, or a list/dict/tuple/set element (stored as opaque pointers — element kind not tracked). |
| List indexing / assignment / negative index | ✅ | |
| `.append` / `.pop` / `.extend` | ✅ | `.append` of a nested collection/instance is allowed. |
| `.index(v)` | ✅ | Linear scan (int or str elements); raises when absent, like CPython. |
| List slicing `xs[a:b]` (read) | ✅ | |
| Slice assignment `xs[a:b] = ...` | ❌ | |
| Nested lists (`list[list[int]]`) | ⚠️ | Accepted as opaque-element lists (front-end); element type not tracked, no nested-aware codegen. |
| Mixed-type *scalar* lists (`[1, "a"]`) | ❌ | Homogeneous scalars only. |
| List comprehensions `[e for x in it if c]` | ⚠️ | Single `for`, optional single `if`; iterable must be list/tuple/opaque. No nested/multiple `for`, no `enumerate()` iterable. |
| `list(x)` (copy) | ⚠️ | From a list/tuple: a real shallow copy (full-buffer copy, independent of the source). From str/dict source — ❌ (codegen pending). |
| Dicts `{k: v}` | ⚠️ | **str keys only**; values homogeneous (int/str/float/instance/nested-collection). Nested values (`dict[str, dict]`, `dict[str, list]`) are stored as heap pointers; a chained read `d[k][k2]` recovers the leaf type one nesting level deep. |
| Dict indexing / assignment | ✅ | Missing key → `KeyError` print + exit. |
| `.get(k[, default])`, `.keys()`, `.values()`, `.contains()`, `.update()` | ✅ | `.get(k)` (no default) returns 0 for a missing key; `.get(k, d)` returns `d`. `.update(src)` merges src's entries (overwriting). `.values()` carries the value kind. `.items()` — ⚠️ via tuples; `del d[k]` — ❌. |
| `.pop(k[, default])` | ⚠️ | Type-checks (returns the value kind); codegen pending. |
| `.items()` | ✅ | A list of `(key, value)` pair tuples; `for k, v in d.items()` types `k` as str and `v` as the value kind. |
| `dict()` / `dict(other)` (copy) | ✅ | Empty dict, or a shallow copy (built via the same merge helper `.update()` uses). |
| Dict iteration (`for k in d`) | ✅ | Iterates keys. |
| Dict comprehensions `{k: v for ...}` | ⚠️ | Same shape limits as list comprehensions; str keys. |
| Tuples `(a, b)` | ✅ | First-class, fixed-size, **heterogeneous** (per-slot types). |
| Tuple indexing / unpacking / `for k, v in ...` | ✅ | `a, b = b, a` swap works. `a, b = <single tuple>` unpacks. |
| Sets `{a, b}` / membership / `.add` / `.update` | ⚠️ | A set is a **dict keyed by its members** (str members in v1): `{a, b}` literals, `set(iterable)`, `x in s` / `x not in s`, and `s.add(x)` all work. `.discard/.remove/.update/.clear` type-check but mutator codegen is pending (no dict-delete runtime yet); set comprehensions and `set \| set` ❌. |
| `x in y` / `x not in y` against an opaque or instance `y` | ✅ | When `y` is opaque (`any`/untyped) or a user instance (e.g. with `__contains__`), membership lowers to a str-key dict lookup — instances/dicts/sets share the dict layout. Lenient rather than a type error. |
| `set()` / `set(iterable)` | ✅ | Empty set, or built from a list/tuple's elements. From a set/dict source: passed through. |
| `frozenset()` / `frozenset(iterable)` | ✅ | Same dict-backed representation as `set` (immutability not enforced — membership is all that's modelled). |
| `range()` (1/2/3-arg) | ✅ | In `for` loops. |
| `enumerate`, `zip` | ✅ | In `for` loops / comprehension iterables (not as standalone callables). |
| `sorted`, `reversed` | ⚠️ | `sorted(iterable)` works; `key=`/`reverse=` kwargs limited. |
| `sum`, `min`, `max`, `any`, `all` | ✅ | |
| `id(x)` | ✅ | Returns the object's pointer value (unique per object — true `id()` semantics). |
| `type(x)` | ⚠️ | Returns an opaque value; `.__name__` etc. read leniently. No real type objects. |
| `map`, `filter` | ❌ | Use a comprehension. |

## Control flow

| Feature | Status | Notes |
|---|:---:|---|
| `if` / `elif` / `else` | ✅ | |
| `while` | ✅ | |
| `for ... in range()` / list / dict / str / tuple | ✅ | |
| `break` / `continue` / `pass` | ✅ | |
| Ternary `a if c else b` | ✅ | Both arms must share a static type. |
| `and` / `or` / `not` | ✅ | Short-circuit. |
| Chained comparison (`0 < x < 10`) | ✅ | |
| `in` / `not in`, `is` / `is not` | ✅ | `is` lowers to bit-equality. |
| `assert cond[, msg]` | ✅ | Desugars to `if not cond: raise msg`. |
| `for`/`while` … `else` | ❌ | |
| `match` / `case` | ❌ | |
| `with` (context managers) | ❌ | |
| Walrus `:=` | ❌ | |

## Functions

| Feature | Status | Notes |
|---|:---:|---|
| `def`, positional args, `return`, recursion | ✅ | Implicit `return 0` on fall-through. |
| Many parameters (more than the ABI's arg registers) | ✅ | Integer/pointer args beyond 4 (Win64) / 6 (SysV) pass on the stack, both as a callee (prologue reads them, shadow-space-aware on Win64) and a caller (16-byte-aligned spill, cleaned up after the call). **Float params still ❌.** |
| Default arguments | ⚠️ | Literal int/str/`True`/`False`/`None` only. **Float defaults ❌.** |
| Type annotations on params / return | ⚠️ | Parsed and used for inference where possible; not enforced. |
| Keyword arguments at call sites (`f(x=1)`) | ⚠️ | Bound onto positional params; no float kwargs, no arbitrary `**`. |
| `*args` | ⚠️ | Packed into a trailing list param. |
| `**kwargs` | ⚠️ | **Parsed and discarded** — not a usable mapping. |
| Keyword-only `*` separator | ⚠️ | Parses; params stay positional. |
| Functions returning float / str / list / dict / instance | ✅ | Via return annotation / inference. |
| Multiple return values (`return a, b`) | ✅ | As a tuple. |
| Nested functions / closures | ❌ | `def` at module/class scope only. |
| Lambdas | ❌ | |
| Generators / `yield` | ❌ | Out of scope (no interpreter loop). |
| Decorators | ⚠️ | Parsed; most **dropped**. Only `@assembly_func` is acted on (see [about.md](about.md)). |
| First-class functions (pass/store a function) | ❌ | |
| `global` / `nonlocal` | ⚠️ | Module-level names are readable in functions; no explicit `global` statement needed/modeled. |

## Classes & objects

| Feature | Status | Notes |
|---|:---:|---|
| `class`, `__init__`, instance attributes, methods | ✅ | Methods take explicit `self`. |
| Single inheritance | ✅ | Method lookup walks the parent chain. |
| Instance attribute values | ⚠️ | int / str / float / list / instance supported. |
| Attribute read off an `any`/opaque value (`x.field`) | ✅ | Treated as an instance-dict field read (`dict_get_default`, default 0) — same layout an instance uses. |
| Class-body variable declarations | ⚠️ | Captured for typing; `@dataclass` field syntax parses. |
| `super()` | ⚠️ | Resolves against the single base. |
| `isinstance` / `type()` | ⚠️ | `isinstance(x, Cls)` and `isinstance(x, (A, B))` work: instances are RTTI-tagged with their class id at construction, and the check walks the subclass set. `type()` ❌. |
| Polymorphism / virtual dispatch | ⚠️ | Static dispatch by the variable's declared type; limited vtable RTTI for `isinstance`. |
| `@dataclass` (synthesised `__init__`) | ❌ | Decorator parses but synthesises nothing. |
| Multiple inheritance / MRO | ❌ | |
| `@classmethod` / `@staticmethod` / `@property` | ❌ | The compiler requires methods to take `self`. |
| Dunder methods beyond `__init__` (`__repr__`, `__eq__`, `__add__`, `__len__`, …) | ❌ | |
| `__slots__`, metaclasses, descriptors | ❌ | |

## Exceptions

| Feature | Status | Notes |
|---|:---:|---|
| `try` / `except` | ⚠️ | Single bare handler; catches everything. |
| `except as name` | ✅ | Binds the raised **string** message. |
| `raise <string>` | ✅ | String-message exceptions (hand-rolled setjmp/longjmp). |
| `raise ExcClass("msg")` / typed `except ExcClass:` | ⚠️ | Builtin exception **names** parse (`ValueError`, etc.); type-selective dispatch not modeled — first handler catches all. |
| Multiple `except` clauses / `else` / `finally` | ⚠️ | `finally` ✅ (runs on normal and exceptional exit; re-raises when there is no `except`). Multiple `except` / `else` parse but codegen rejects them. |
| Bare re-`raise` | ❌ | |
| `raise ... from ...` | ⚠️ | Parses; cause discarded. |
| Traceback objects | ❌ | Uncaught → `Unhandled exception: <msg>` + exit 1. |

## Modules & imports

| Feature | Status | Notes |
|---|:---:|---|
| `import math` / `from math import ...` | ✅ | Resolves against the curated `asmpython._stdlib` FFI registry. |
| Available stdlib | ⚠️ | `math` (22 fns + 5 consts) and `os` (`system`, `getenv`, `_exit`, plus file I/O: `fopen`/`fgetc`/`fclose`/`_access`). That's it today — see the 1.0 `asmpython.libs` plan in [roadmap.md](roadmap.md). |
| `import x as y` / `from x import a as b` | ✅ | |
| Unmodeled attr of a known module (`os.environ`, `os.sep`) | ⚠️ | Type-checks as opaque (front-end leniency); no codegen. |
| Dotted imports (`import os.path`) | ⚠️ | Leading segment binds; submodule lookup is post-bootstrap. |
| Relative imports (`from .x import y`) | ⚠️ | Functions and classes from a sibling project module are merged in (whole-program compile). A relative-imported *value* (`from .x import SOME_DICT`) is now materialized too — its initializer is pulled into the program as a global, **transitively** (deps emitted first) when self-contained. Names whose initializer needs a CPython-runtime value stay opaque. |
| Importing another user `.py` file | ⚠️ | Whole-program loader (`_compiler/program.py`) discovers the import graph and merges sibling modules' funcs, classes, and self-contained value globals into one compilation unit. `from . import mod as M` then `M.func(x)` dispatches to the merged function. No per-file `.o` linking; no executing arbitrary module-level side effects. |
| `__name__` / `__file__` dunders | ⚠️ | Provided as str (`__name__ == "__main__"` works); one entry point per program. |
| FFI to C functions | ✅ | Via `asmpython._stdlib` `Func`/`Const` bindings (int/float/str args & returns). `int` returns are sign-extended from EAX (C `int` is 32-bit), so `fgetc()`'s `-1` EOF compares right; a 64-bit-pointer return must be declared `-> str`, not `-> int`. |
| Inline assembly (`@assembly_func`, `include()`) | ✅ | asmpython-specific extension — see [about.md](about.md#inline-assembly--asmpythonassembly). |

## I/O & runtime

| Feature | Status | Notes |
|---|:---:|---|
| `print(*args)` | ✅ | 0–64 args, mixed int/float/str, space-separated + newline. |
| `input([prompt])` | ✅ | One line from stdin, newline stripped. |
| `open()` / file objects / `read` / `write` | ❌ | (Planned for `asmpython.libs.os`.) |
| `os.system`, `os.getenv` | ✅ | Via the FFI registry. |
| `os.environ` (as a mapping) | ⚠️ | `os.environ` type-checks as opaque (front-end); no usable codegen yet. |
| `sys.argv`, `sys.exit` | ❌ | |
| `subprocess`, `socket`, networking | ❌ | (Networking planned for `asmpython.libs.net`.) |
| `time`, `random`, `datetime`, `json`, `re`, `pathlib`, `itertools`, `functools`, `collections`, `threading`, `asyncio` | ❌ | None bound yet. |

## asmpython-specific extensions (not in Python)

| Feature | Status | Notes |
|---|:---:|---|
| `@assembly_func` (raw-NASM function body) | ✅ | `from asmpython.assembly import assembly_func`. |
| `include("pkg")` (`.asmpkg` assembly packages) | ✅ | `from asmpython.assembly import include`. |
| `# [compiler: ignore_start]` / `ignore_end` blocks | ✅ | Fence linter-only code out of compilation. |
| `--target {windows,linux,freestanding}`, `--emit-asm`, `--onefile`/`--onedir`, `--use-runtime-lib`, `--check` | ✅ | CLI build modes — see `python -m asmpython --help`. |
| `--type {executable,library}` | ✅ | `executable` (default) or `library` (a shared library: `.dll`/`.so`). User functions are emitted but not yet all exported. |
| `--target freestanding` (ring-0 / bare-metal output) | ❌ | The CLI selector exists (replaced the old `--freestanding` idea) but the backend isn't built — errors clearly. Planned for 1.0 `asmpython.libs.hardware`; the `.asmpkg` `freestanding:` flag is the seed. |

---

## Out of scope (CPython implementation, not the language)

These are intentionally **not** goals — asmpython compiles to flat machine code,
so the interpreter machinery has no analogue:

- The GIL, bytecode / `dis`, the C-API / C extensions (`.pyd`/`.so` modules).
- Developer tracebacks, via `--embed-tracebacks`. An unhandled exception prints
  CPython's structure -- header, one indented frame line per level outermost
  first, exception last -- with native details:

      Traceback (most recent last call):
          File "app.py", index 0x140001798, line 9 (derived), in <module>
          File "app.py", index 0x140001606, line 5 (derived), in outer
          File "app.py", index 0x14000108F, line 2 (derived), in inner
      Unhandled exception: list index out of range

  `index` is the address the frame was entered from; `line` is marked `(derived)`
  because it is reconstructed from a compile-time table rather than carried by an
  interpreter frame. `<module>` is CPython's name for module-level code.

  Mechanism: the compiler maintains a frame stack, expressed entirely in
  existing IR ops, so no backend changes were needed. Each function pushes
  `(name, file, &line_slot, entry_address)` on entry and pops on return; the
  line lives in that function's own stack slot, so a statement updates it with a
  single store at a fixed frame offset -- no depth arithmetic, no call. The
  push/pop shims are frameless and touch only volatile registers.

  Positions were already available: `pos: SourcePos` is on 65 AST node types and
  filled by the parser at 174 sites. This only stops discarding it. The same
  markers feed the `debug_loc` IR op, which the backend has consumed into DWARF
  `.debug_line` all along without ever having a producer -- so gdb gets real
  line numbers too.

  OFF BY DEFAULT and a build without it is unchanged: the frame bookkeeping costs
  a push/pop per call and a store per statement, and the embedded strings cost
  binary size.

  Caught exceptions are handled: a handler is reached by `longjmp`, which skips
  every pop between the raise and the catch, so the depth is saved at `try` and
  restored in the handler -- the same treatment `_runtime_handler_top` already
  gets. Without it, dead frames' line-slot pointers aimed into reclaimed stack
  and the printer reported a pointer where a line belonged.

  Not yet done: the source TEXT of each frame's line is not embedded, so a frame
  shows `file`, `line` and `function` but not the code. That is additive -- each
  statement's line is already interned as a string constant at the point the
  marker is emitted.

- Reference counting and `__del__` finalizers. The `gc` module is REAL: `collect()`
  runs a mark-sweep and returns the number of objects actually freed,
  `enable`/`disable`/`isenabled` drive the runtime flag, and
  `set_threshold`/`get_threshold` set how many object allocations pass between
  automatic collections (default 700, CPython's gen-0 number). What is absent is
  CPython's *deterministic* destruction -- `del obj` does not free at the `del`,
  and `__del__` does not run at a predictable point -- because that needs
  refcounting, not tracing. `--gc=refcount` names the gap and refuses rather than
  running mark-sweep under a name that promises otherwise.
  `get_objects`/`get_referrers`/`get_referents`/`is_tracked` are still stubs: the
  registry could answer the first and last, but the tracer walks words rather than
  building an object graph, so referrer queries have nothing to read.

  PLATFORM STATUS, because "the gc module is real" is not the same as "it
  collects everywhere": on Windows it does real work -- live data reachable only
  from a module-level name survives repeated collection and garbage is reclaimed.
  On Linux `collect()` returns 0 and frees nothing, by design: the collector
  refuses to sweep unless it can see every root set, and Linux has no stack-base
  hook (Win64 reads the TEB) nor a globals-range hook (Win64 walks its own PE
  headers). That refusal is deliberate -- a missing root set is not a degraded
  collection but a wrong one, since anything reachable only through the missing
  set gets freed while live.

  Two KNOWN parity gaps in the `gc` module itself, both currently enshrined in
  `tests/cases/207_gc_module.py` and `277_gc_module.py`, which is why fixing them
  is a corpus change and not just a stdlib one:

  1. `gc.isenabled()` returns an `int` (`0`/`1`), where CPython returns a `bool`
     (`False`/`True`), so it prints `1` where CPython prints `True`.
  2. Automatic collection starts DISABLED; in CPython it starts enabled. So
     `gc.isenabled()` on a fresh program answers `0` here and `True` there.

  (2) is the substantive one: matching CPython means every program pays for
  automatic collection by default, which is a behaviour change big enough to want
  its own gate rather than riding along with the collector.
- `eval` / `exec` / `compile` / dynamic code, `globals()`/`locals()`/`vars()` introspection. **Rejected with a clear located error** ("not supported: requires a Python interpreter") rather than a codegen crash.
- `importlib.import_module()` / dynamic import by string name. Same clear rejection. (Static `import math` / `from x import y` are fine.)
- Monkeypatching, runtime attribute injection on arbitrary objects, `setattr` on non-instances.
- Import hooks, `sys.modules` surgery, metaclass machinery.

See [roadmap.md](roadmap.md) for what's planned and [about.md](about.md) for the
working feature set in prose.
