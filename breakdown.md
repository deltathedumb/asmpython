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
| Methods: `.upper .lower .strip .lstrip .rstrip .startswith .endswith .find .count .replace .split .join` | ✅ | `.split(sep, maxsplit)` accepts the maxsplit arg (front-end); codegen ignores it. |
| `.format()` | ❌ | Use f-strings. |
| `.format_map`, `.encode`, `.splitlines`, `.title`, `.zfill`, `.center`, `.ljust`/`.rjust`, `.partition` … | ⚠️ | Not implemented — but an unmodeled `.method()` on a str-typed value type-checks as opaque (front-end leniency for the exception-as-string case), so it won't compile correctly. |
| String formatting `%` operator | ❌ | |

## Collections

| Feature | Status | Notes |
|---|:---:|---|
| Lists `[...]` | ⚠️ | **Homogeneous**: all-int, all-str, all-float, all-instances-of-one-class, or a list/dict/tuple/set element (stored as opaque pointers — element kind not tracked). |
| List indexing / assignment / negative index | ✅ | |
| `.append` / `.pop` / `.extend` | ✅ | `.append` of a nested collection/instance is allowed. |
| `.index(v)` | ⚠️ | Type-checks (returns int); codegen pending. |
| List slicing `xs[a:b]` (read) | ✅ | |
| Slice assignment `xs[a:b] = ...` | ❌ | |
| Nested lists (`list[list[int]]`) | ⚠️ | Accepted as opaque-element lists (front-end); element type not tracked, no nested-aware codegen. |
| Mixed-type *scalar* lists (`[1, "a"]`) | ❌ | Homogeneous scalars only. |
| List comprehensions `[e for x in it if c]` | ⚠️ | Single `for`, optional single `if`; iterable must be list-typed. No nested/multiple `for`, no `enumerate()` iterable. |
| Dicts `{k: v}` | ⚠️ | **str keys only**; values homogeneous (int/str/float/instance/nested-collection). |
| Dict indexing / assignment | ✅ | Missing key → `KeyError` print + exit. |
| `.get(k[, default])`, `.keys()`, `.values()`, `.contains()`, `.update()` | ✅ | `.values()` carries the value kind. `.items()` — ⚠️ via tuples; `del d[k]` — ❌. |
| `.pop(k[, default])` | ⚠️ | Type-checks (returns the value kind); codegen pending. |
| `dict()` / `dict(other)` (copy) | ⚠️ | Type-checks (carries value kind); codegen pending. |
| Dict iteration (`for k in d`) | ✅ | Iterates keys. |
| Dict comprehensions `{k: v for ...}` | ⚠️ | Same shape limits as list comprehensions; str keys. |
| Tuples `(a, b)` | ✅ | First-class, fixed-size, **heterogeneous** (per-slot types). |
| Tuple indexing / unpacking / `for k, v in ...` | ✅ | `a, b = b, a` swap works. `a, b = <single tuple>` unpacks. |
| Sets `{a, b}` / set ops | ❌ | `SetLit` parses; a `set`-annotated value type-checks and accepts `.add/.discard/.remove/.update/.clear` + `len()` + `in` (front-end), but there is **no set runtime** — no codegen, not a usable value. Set comprehensions and `set \| set` ❌. |
| `frozenset` | ❌ | |
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
| Class-body variable declarations | ⚠️ | Captured for typing; `@dataclass` field syntax parses. |
| `super()` | ⚠️ | Resolves against the single base. |
| `isinstance` / `type()` | ⚠️ | `isinstance` has RTTI via class ids; `type()` ❌. |
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
| Multiple `except` clauses / `else` / `finally` | ⚠️ | Parse; codegen implements the single-handler shape, rejects the rest. |
| Bare re-`raise` | ❌ | |
| `raise ... from ...` | ⚠️ | Parses; cause discarded. |
| Traceback objects | ❌ | Uncaught → `Unhandled exception: <msg>` + exit 1. |

## Modules & imports

| Feature | Status | Notes |
|---|:---:|---|
| `import math` / `from math import ...` | ✅ | Resolves against the curated `asmpython._stdlib` FFI registry. |
| Available stdlib | ⚠️ | `math` (22 fns + 5 consts) and `os` (`system`, `getenv`, `_exit`). That's it today — see the 1.0 `asmpython.libs` plan in [roadmap.md](roadmap.md). |
| `import x as y` / `from x import a as b` | ✅ | |
| Unmodeled attr of a known module (`os.environ`, `os.sep`) | ⚠️ | Type-checks as opaque (front-end leniency); no codegen. |
| Dotted imports (`import os.path`) | ⚠️ | Leading segment binds; submodule lookup is post-bootstrap. |
| Relative imports (`from .x import y`) | ⚠️ | Parse and bind names as opaque; no cross-file resolution yet. A call to an opaque-imported name returns an opaque value. |
| Importing another user `.py` file | ❌ | No real cross-file module loading yet. |
| `__name__` / `__file__` dunders | ⚠️ | Provided as str (`__name__ == "__main__"` works); one entry point per program. |
| FFI to C functions | ✅ | Via `asmpython._stdlib` `Func`/`Const` bindings (int/float/str args & returns). |
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
- Reference counting / `gc` module / `__del__` finalizers (memory currently leaks; a GC/refcount scheme is a roadmap item).
- `eval` / `exec` / `compile` / dynamic code, `globals()`/`locals()` introspection.
- Monkeypatching, runtime attribute injection on arbitrary objects, `setattr` on non-instances.
- Import hooks, `sys.modules` surgery, metaclass machinery.

See [roadmap.md](roadmap.md) for what's planned and [about.md](about.md) for the
working feature set in prose.
