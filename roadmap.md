# Mamba Roadmap to 99.9% Python Compatibility

This document is the long-form plan for evolving mamba from "compiles small Python scripts" to "compiles essentially anything that doesn't require CPython's C extension API."

99.9% compatibility means: a developer writes idiomatic Python — `requests.get(url).json()`, `with open(path) as f: lines = f.readlines()`, `dataclass`-decorated config, an asyncio task or three — and mamba either compiles it or fails with a clear "this feature isn't implemented yet" message. **Not** "your code segfaults at runtime" or "results silently differ from CPython."

The work is grouped into **tiers**. Each tier completes a self-consistent capability slice. Items within a tier are roughly ordered by dependencies. Estimates are calendar-time for one focused engineer.

---

## The headline goal: self-compilation by July

**Mamba should compile mamba.** The compiler is ~5,700 lines of Python; today CPython runs it and emits NASM. The milestone is: feed `mamba/` to `python -m mamba`, get back a `mamba.exe` that itself compiles `mamba/` and produces a byte-identical (or behaviourally-identical) result on the next pass. That's the test that means we actually built a Python compiler, not a Python-subset toy.

Why this matters:

- **It's the honest measure of "compatibility."** Every cute test case can be hand-trimmed to fit a subset; the compiler can't. If mamba can compile mamba, the supported subset is broad enough that real code lands on it.
- **It forces priorities.** The features mamba is missing today (dataclasses, exception classes, `**kwargs`, list-of-string, comprehensions, file I/O, …) are exactly the ones blocking real users — but the compiler source tells us which of them matter *most* by where they appear most often. The tier ordering below already reflects that audit.
- **It collapses the bootstrap dependency.** Once mamba compiles mamba, CPython stops being a prerequisite for shipping a mamba binary. Users on a machine without Python can still get a working compiler.

The plan is to hit this by **July 2026**. The detailed tiers below are the path — Tier 1 (strings) is already done, Tier 2 (collections) is the current battleground, and each subsequent tier knocks out a category of Python feature the compiler relies on. We don't need every tier complete to self-host — we need *enough* of each that the specific constructs in `mamba/*.py` parse, type-check, and emit correct code.

What we explicitly **don't** need before July:

- Full stdlib coverage (Tier 5). The compiler only imports `os`, `sys`, `subprocess`, `dataclasses`, `pathlib`, `typing`, and `enum`. Everything else in Tier 5 is post-bootstrap polish.
- Performance work (Tier 9). Self-host means *correct* compilation, not fast compilation. A 60-second build is fine as long as it converges.
- Most of Tier 11 (other platforms). Self-host on Windows x86-64 is the milestone; Linux/macOS follow.

Progress will be measured by a `selfhost/` script that tries to compile the compiler and reports which file in `mamba/` chokes first. Each tier landing should move that needle.

---

## Tier 0 — What's done

Integer/float arithmetic, strings (immutable, with concat/repeat/`==`/`!=`/`in`/indexing/slicing and the `.upper/.lower/.strip/.lstrip/.rstrip/.startswith/.endswith/.find/.count/.replace` methods), f-strings anywhere a `str` is expected, lists (int-only today), dicts (str→int today), classes with single inheritance, exceptions (`try`/`except`/`raise`), `import math` / `from math import …` FFI, multi-arg `print`, `input`, tuple destructuring assignment (`a, b = b, a`). Diagnostics with file:line:col + caret. NASM + gcc toolchain. Runtime library extraction (`--use-runtime-lib`). **47/47 tests passing.**

This is the "I can write a numerics or text-munging script that does math, talks to the user, and handles bad input" baseline. **Tier 1 is complete; Tier 2 is in progress.**

---

## Tier 1 — Strings as first-class values *(~1 week)*

Without this tier, every higher tier hits the same wall — Python programs are mostly string manipulation.

| # | Item | Why it matters | Depends on |
|---|------|----------------|-----|
| 1.1 | ✅ **String concatenation** `"a" + "b"` | Unblocks f-strings outside print, every other string operation | — |
| 1.2 | ✅ **String equality** `s1 == s2`, `s1 != s2` (ordering with `<`/`>` not yet) | Sorting, dict-with-string-values lookups by content | — |
| 1.3 | ✅ **String multiplication** `"-" * 80` | Pretty printing, ASCII art | 1.1 |
| 1.4 | ✅ **String indexing** `s[i]` returns a 1-char str | Parsing | 1.1 |
| 1.5 | ✅ **String slicing** `s[i:j]` (open endpoints, negative indices; no step yet) | Trimming, reversing, splitting by index | 1.4 |
| 1.6 | ✅ **`in` / `not in` on strings** `"x" in s` | Search predicates | 1.5 |
| 1.7 | ✅ Partial **String methods**: `.upper`, `.lower`, `.strip`, `.lstrip`, `.rstrip`, `.startswith`, `.endswith`, `.find`, `.count`, `.replace` — still missing `.split`, `.rsplit`, `.join`, `.index`, `.title`, `.capitalize`, `.swapcase`, `.zfill`, `.center`, `.ljust`, `.rjust` (most gated on list-of-string) | The bread and butter of Python text processing | 1.1, 1.5 |
| 1.8 | **String iteration** `for ch in s` | Character-level loops | 1.4 |
| 1.9 | **Multiline strings** `"""..."""` | Embedded docs, SQL queries, JSON literals | — |
| 1.10 | **String escapes**: `\x41`, `é`, `\N{...}` | Unicode-rich code | 1.9 |
| 1.11 | ✅ **F-strings everywhere** (not just `print`) — lowered through runtime concat | What everybody actually writes | 1.1 |
| 1.12 | **F-string format specs** `f"{x:.2f}"`, `f"{n:>5}"` | Numeric output, table formatting | 1.11 |
| 1.13 | **`%` formatting** `"%s = %d" % (k, v)` | Older code that hasn't migrated to f-strings | 1.11 |
| 1.14 | **`.format()` method** `"{}={}".format(k, v)` | Same | 1.12 |
| 1.15 | **`repr()` builtin** | Debugging output | 1.11 |
| 1.16 | **`bytes` and `bytearray`** types | Binary data: file I/O, network, hashing | 1.1 |

Architecture impact: introduces a `string` value that owns its memory. Either reference-counted or arena-allocated. This decision cascades into every subsequent tier — pick a memory model before any of this lands.

---

## Tier 2 — Collections *(~2 weeks)*

| # | Item | Why it matters | Depends on |
|---|------|----------------|-----|
| 2.1 | **Tagged values** — uniform 16-byte slot `(tag, payload)` for lists, dicts, attrs | Lets collections hold mixed types | — |
| 2.2 | Partial **Lists of any type**: homogeneous `list[int]` / `list[str]` / `list[float]` ship; nesting (`list[list[int]]`) and instance-element lists still wait on (2.1) | "I want a list of names" | 2.1 |
| 2.3 | **Dict with any value type** | "I want a config dict" | 2.1 |
| 2.4 | **Dict with int keys** | Common Python idiom | 2.1 |
| 2.5 | Partial **Tuples**: destructuring `a, b = e1, e2` works, but `(a, b)` is not yet a first-class value (no storing, indexing, or `for x in t`) | Multiple-return-value Python | 2.1 |
| 2.6 | ✅ **Multiple assignment** `a, b = 1, 2` (parallel-eval semantics, supports swap) | Same — everywhere in Python | — |
| 2.7 | **Iterable unpacking** `a, *rest = [1, 2, 3]` | `argv` parsing, splitting heads/tails | 2.5 |
| 2.8 | **`for k, v in d.items()`** | Most common dict iteration form | 2.5, 2.3 |
| 2.9 | **`d.keys()`, `d.values()`, `d.items()`** returning list-like views | Same | 2.5 |
| 2.10 | **Sets** `{1, 2, 3}`, `s.add`, `s.remove`, `s.contains`, set ops `\|`, `&`, `-`, `^` | De-duplication, membership tests | 2.1 |
| 2.11 | **Slicing** on lists `xs[1:5]`, `xs[::-1]` | List comprehensions in disguise | 2.5 |
| 2.12 | **Negative indices** `xs[-1]`, `s[-3:]` | Pervasive Python idiom | 2.11 |
| 2.13 | **`in` operator** `x in lst`, `k in d`, `x in s` | Same | 2.10 |
| 2.14 | **`del`** on list indices, dict keys, attrs | Mutation patterns | 2.3 |
| 2.15 | **List comprehensions** `[x*2 for x in xs if x > 0]` | Python idiom #1 | 2.2, 2.13 |
| 2.16 | **Dict comprehensions** `{k: v*2 for k, v in d.items()}` | Same | 2.8, 2.15 |
| 2.17 | **Set comprehensions** `{x % 7 for x in xs}` | Same | 2.10, 2.15 |
| 2.18 | **Generator expressions** `sum(x*x for x in xs)` | Memory-efficient pipelines | 2.15 + lazy iter |
| 2.19 | **`enumerate(seq)`**, **`zip(a, b)`**, **`reversed(seq)`**, **`sorted(seq, key=…)`** | Standard iteration tooling | 2.5, 2.8 |
| 2.20 | **`min`, `max`, `sum`, `any`, `all`, `len` on any iterable** | Same | 2.19 |

This tier hits a fork: do we adopt **boxed values** (a uniform tagged-pointer model, slower but simple) or **type-specialized collections** (`list_of_int`, `list_of_str`, etc., faster but combinatorial)? The 99.9% target argues for boxed values — programs that mix types in collections are too common to refuse.

---

## Tier 3 — Polymorphism, closures, generators *(~3 weeks)*

The runtime gets a real object model.

| # | Item | Why it matters | Depends on |
|---|------|----------------|-----|
| 3.1 | **Vtables** — each class has a static dispatch table; method calls go through it | True polymorphism: `Shape` parameter dispatches to `Square.area` | Tier 2 |
| 3.2 | **`isinstance(obj, Cls)`** | Used everywhere | 3.1 |
| 3.3 | **`type(obj)` returns a class object** | Debugging, introspection | 3.1 |
| 3.4 | **`super()`** in methods | Multi-level inheritance patterns | 3.1 |
| 3.5 | **Multiple inheritance with C3 MRO** | Mixins, `@dataclass`-style composition | 3.1 |
| 3.6 | **Class attributes** (statics) | Constants attached to types | 3.1 |
| 3.7 | **`@classmethod`, `@staticmethod`** | Factory methods | 3.6 |
| 3.8 | **`@property`** getters and setters | Encapsulation | 3.1 |
| 3.9 | **All standard dunder methods**: `__init__`, `__repr__`, `__str__`, `__eq__`, `__lt__` (etc.), `__hash__`, `__add__` (etc.), `__getitem__`, `__setitem__`, `__contains__`, `__iter__`, `__next__`, `__len__`, `__call__`, `__bool__`, `__enter__`, `__exit__` | What makes Python feel like Python | 3.1, 3.2 |
| 3.10 | **First-class functions** — pass `def`'d names as values, store in vars | `map(fn, xs)`, callback-driven code | — |
| 3.11 | **Lambda expressions** `lambda x: x*2` | Inline callbacks | 3.10 |
| 3.12 | **Closures** — nested `def` captures enclosing locals | Decorator implementations, async helpers | 3.10 |
| 3.13 | **`nonlocal`** keyword | Closure mutation | 3.12 |
| 3.14 | **Decorators** `@decorator` syntax | `@dataclass`, `@property`, `@cache`, `@app.route` | 3.10 |
| 3.15 | **Default arguments** `def f(x=10):` | Pervasive | — |
| 3.16 | **Keyword arguments** `f(name="x")` | Pervasive | — |
| 3.17 | **`*args` and `**kwargs`** | Pervasive | 3.16 |
| 3.18 | **Generators with `yield`** | Iterators, lazy sequences, async foundations | 3.10 + coroutine impl |
| 3.19 | **Generator delegation `yield from`** | Compose iterators | 3.18 |
| 3.20 | **Async functions `async def` + `await`** | The whole modern Python ecosystem | 3.18 + event loop |

Generators require coroutine-style frame save/restore. Easiest implementation: heap-allocate the frame on first `yield`, switch stacks. Async is generators + an event loop + `asyncio` stdlib bindings.

---

## Tier 4 — Memory management *(~1 week, done in parallel with Tier 1-3)*

Currently mamba leaks: every `[1,2,3]` `malloc`s without freeing. We're below the radar for short-running scripts but production code needs this.

| # | Item | Approach |
|---|------|----------|
| 4.1 | **Reference counting** | One ref-count word in each heap object. Increments on assignment, decrements on scope exit. Free at zero. |
| 4.2 | **Cycle collector** | RC misses cycles (e.g. `a.b = b; b.a = a`). Mark-and-sweep over the cyclic candidates. Tracing GC like CPython's. |
| 4.3 | **Weak references** `weakref.ref(obj)` | Caches, observer patterns | 4.1 |
| 4.4 | **`__del__`** finalizers | Called when refcount hits zero | 4.1 |
| 4.5 | **`gc` module** | `gc.collect()`, `gc.disable()` for performance tuning | 4.2 |
| 4.6 | **Memory leak prevention** in exception unwinding | When `longjmp` skips frames, the runtime decrements RC for every live value in those frames | 4.1 |

Adopting RC means every store touches a refcount. Slowdown is real but the developer experience is non-negotiable: production code can't leak.

Alternative: defer RC and use an **arena per call** — each function call allocates from a bump allocator that gets reset at exit. Works for pure functions, breaks for anything that returns a heap value to the caller. Not viable for the 99.9% target.

---

## Tier 5 — Standard library bindings *(~3-4 weeks, mostly parallel)*

The stdlib is enormous; 99.9% means we ship enough of it that the typical script's `import` block works.

### 5a. Core OS and I/O

| Module | Surface area | Difficulty |
|--------|--------------|------------|
| `sys` | `argv`, `exit`, `stdin`, `stdout`, `stderr`, `path`, `version`, `platform`, `maxsize`, `getsizeof` | Easy — straight FFI |
| `os` | `getcwd`, `chdir`, `listdir`, `mkdir`, `makedirs`, `rmdir`, `remove`, `rename`, `stat`, `path.join`, `path.exists`, `path.isfile`, `path.isdir`, `path.basename`, `path.dirname`, `path.splitext`, `environ`, `getenv`, `system` | Easy-medium — POSIX/Win32 wrappers; need `Stat` struct binding |
| `pathlib` | `Path` class with `/` operator, `read_text`, `write_text`, `iterdir`, `glob`, `parent`, `stem`, `suffix`, `exists`, etc. | Medium — composes os.path |
| `io` | `open(path, mode)`, file objects with `read`, `write`, `readlines`, `seek`, `tell`, `close`, context-manager protocol | Medium — binds to fopen/fread/fwrite |
| `shutil` | `copy`, `copytree`, `rmtree`, `move`, `which`, `disk_usage` | Easy — straight C calls |
| `tempfile` | `NamedTemporaryFile`, `TemporaryDirectory`, `mkstemp` | Easy |
| `subprocess` | `run`, `Popen`, `check_call`, `check_output`, `PIPE`, `DEVNULL` | Medium-hard — fork/exec on Linux, CreateProcess on Windows |
| `glob` | `glob.glob`, `glob.iglob` | Easy on top of pathlib |
| `argparse` | The whole thing | Medium — pure-mamba, no FFI |

### 5b. Data and formats

| Module | Notes |
|--------|-------|
| `json` | `loads`, `dumps`, `JSONDecodeError`. Pure-mamba parser. |
| `csv` | `reader`, `writer`, `DictReader`, `DictWriter`. Pure-mamba. |
| `re` | Regular expressions. **Hard** — port PCRE or write a small NFA engine. ~1 week alone. |
| `pickle` | Almost certainly skip. Different bytecode universe. |
| `base64`, `hashlib`, `hmac`, `secrets` | FFI to OpenSSL or implement directly. |
| `struct` | Pack/unpack binary blobs. Pure-mamba with bit ops. |
| `xml.etree.ElementTree` | Maybe — XML is fundamentally heavy for "small compiler" energy. Defer. |
| `configparser`, `tomllib` | Pure-mamba. |
| `gzip`, `zipfile`, `tarfile` | FFI to zlib/libarchive. |
| `urllib.parse` | Pure-mamba: URL parsing, no network. |

### 5c. Numerics

| Module | Notes |
|--------|-------|
| `math` | ✅ already done. Extend with `gcd`, `lcm`, `isclose`, `isfinite`, `isinf`, `isnan`, `comb`, `perm`. |
| `cmath` | Complex math — needs complex number type first. |
| `random` | `random`, `randint`, `choice`, `shuffle`, `sample`, `seed`, `Random` class. FFI to libc rand or implement Mersenne Twister. |
| `statistics` | `mean`, `median`, `stdev`, `variance`. Pure-mamba. |
| `decimal` | Arbitrary precision. Hard — port libmpdec or skip. |
| `fractions` | Bignum dependency. Defer. |

### 5d. Concurrency

| Module | Notes |
|--------|-------|
| `threading` | `Thread`, `Lock`, `RLock`, `Event`, `Condition`. FFI to pthreads / Win32 threads. |
| `multiprocessing` | Hard. Different process model; would need its own serialization layer. |
| `concurrent.futures` | Layered on threading. |
| `asyncio` | Major. Coroutine impl (Tier 3.20) + event loop + selectors + `aiohttp` ecosystem hooks. ~2 weeks alone. |
| `queue` | `Queue`, `LifoQueue`, `PriorityQueue`. Pure-mamba. |

### 5e. Networking

| Module | Notes |
|--------|-------|
| `socket` | FFI to BSD sockets / Winsock. |
| `select` | `select.select`, `epoll`, `kqueue`. FFI. |
| `http.client`, `http.server` | Pure-mamba on top of `socket`. |
| `ssl` | FFI to OpenSSL. |
| `email` | Big — message parsing, MIME. Pure-mamba is hard; consider stub. |

### 5f. Datetime

| Module | Notes |
|--------|-------|
| `time` | `time`, `sleep`, `strftime`, `monotonic`, `perf_counter`. Easy FFI. |
| `datetime` | `date`, `time`, `datetime`, `timedelta`, `timezone`. Pure-mamba on top of `time`. Medium. |
| `calendar` | Pure-mamba on top of `datetime`. |
| `zoneinfo` | Needs tzdata. |

### 5g. Functional helpers

| Module | Notes |
|--------|-------|
| `itertools` | `chain`, `cycle`, `count`, `repeat`, `combinations`, `permutations`, `product`, `groupby`, `accumulate`, `dropwhile`, `takewhile`. Pure-mamba with generators. |
| `functools` | `reduce`, `partial`, `lru_cache`, `cache`, `cached_property`, `wraps`. Pure-mamba. |
| `operator` | `add`, `itemgetter`, `attrgetter`. Trivial. |
| `collections` | `deque`, `Counter`, `defaultdict`, `OrderedDict`, `namedtuple`. Pure-mamba. |
| `enum` | `Enum`, `IntEnum`, `auto`. Pure-mamba. |
| `dataclasses` | `@dataclass` decorator. Pure-mamba, depends on Tier 3.14. |
| `typing` | All annotations are runtime no-ops; `TypeVar`, `Generic`, `Protocol` accepted but unused. |

### 5h. Other

| Module | Notes |
|--------|-------|
| `logging` | Pretty mechanical — handler, formatter, levels. Pure-mamba. |
| `inspect` | Reflection. Hard — requires preserved source / AST attribution. |
| `traceback` | Stack walking. Requires unwind info. |
| `warnings` | Easy. |
| `contextlib` | `contextmanager`, `closing`, `suppress`, `ExitStack`. Depends on Tier 6 (`with`). |
| `copy` | `copy`, `deepcopy`. Depends on Tier 4 ref counting. |
| `string` | `Template`, `ascii_lowercase` etc. Easy. |
| `textwrap` | Easy. |
| `pprint` | Easy. |

---

## Tier 6 — Control-flow gaps *(~1 week)*

| # | Item | Why it matters |
|---|------|----------------|
| 6.1 | **`with` statement** — context manager protocol (`__enter__`/`__exit__`) | File I/O, locks, transactions; ubiquitous |
| 6.2 | **`finally` clauses** in try | Cleanup that must run even on exception |
| 6.3 | **`try/except/else`** | Pythonic "did the try succeed?" pattern |
| 6.4 | **Multiple `except` clauses** with type-based dispatch | `except (ValueError, TypeError):` |
| 6.5 | **`raise X from Y`** chained exceptions | Error pipeline preservation |
| 6.6 | **Bare `raise`** to re-raise inside a handler | Error pass-through |
| 6.7 | **`assert <cond>, "msg"`** | Test code, sanity checks |
| 6.8 | **`for/else`, `while/else`** | Loop-with-fallthrough pattern (Python-only) |
| 6.9 | **`match/case` statements** | Modern Python (3.10+) |
| 6.10 | **`continue` and `break` from nested loops** with labels — *Python doesn't have these*, but the existing `break` needs to interact correctly with `try` blocks (don't skip handlers, but do unwind them) | Correctness |
| 6.11 | **`return` from inside `with`/`try`** correctly runs cleanup | Correctness |

---

## Tier 7 — Modules, imports, packaging *(~2 weeks)*

The killer for "I want to compile any Python program."

| # | Item | Why it matters |
|---|------|----------------|
| 7.1 | **Multi-file projects** — `import mymodule` resolves to `mymodule.py` in the project | Programs > 1 file |
| 7.2 | **Package directories** with `__init__.py` | Standard project structure |
| 7.3 | **Relative imports** `from .utils import x` | Same |
| 7.4 | **`__name__ == "__main__"`** guard | Script-vs-library duality |
| 7.5 | **Each module gets its own namespace** | Imports don't leak |
| 7.6 | **Module-level code** runs at first import only | CPython semantics |
| 7.7 | **`import x as y`**, **`from x import y as z`** | Common aliasing |
| 7.8 | **Circular import resolution** | Pragmatic codebases |
| 7.9 | **A package manager** (optional v2) — `mamba install requests` pulling from a curated registry of "mamba-tested" PyPI packages | The big bet — "does this package work?" |
| 7.10 | **Compatibility shim layer** — pure-Python packages from PyPI that don't use C extensions just work (after Tier 5 covers their stdlib usage) | The other big bet |

7.9 and 7.10 are the path to 99.9%. We don't reimplement every PyPI package; we **vet** which ones work and surface that. A package that uses `requests` (which uses `urllib3`, which uses `socket`, `ssl`, `select`) needs all of Tier 5e — but once that exists, `requests` itself should just work.

---

## Tier 8 — Production-grade error reporting *(~1 week)*

Mamba's diagnostics today catch user errors before codegen. Production code needs *runtime* diagnostics too.

| # | Item | Why |
|---|------|-----|
| 8.1 | **Tracebacks** on uncaught exceptions — file:line:func for every frame | Without this, production crashes are unactionable |
| 8.2 | **Source-mapped errors** — runtime errors point back to original `.py` source | Same |
| 8.3 | **Exception types** with `__cause__`, `__context__`, `__traceback__` attributes | Composability with Python idioms |
| 8.4 | **`sys.excepthook`** for centralized error logging | Sentry, Rollbar integration |
| 8.5 | **DWARF debug info** in the executable for native debuggers (gdb, lldb, WinDbg) | Production debugging |
| 8.6 | **Coverage info** | Test infrastructure |
| 8.7 | **Profile hooks** `sys.setprofile`, `sys.settrace` | Profilers |

---

## Tier 9 — Performance *(ongoing, ~2-4 weeks for serious bite)*

Reasonable performance is part of compatibility. A 100x-slower script doesn't qualify as "compatible."

| # | Item | Expected speedup |
|---|------|------------------|
| 9.1 | **Constant folding** | 5-20% |
| 9.2 | **Dead code elimination** | 5-10% |
| 9.3 | **Peephole optimizer** on emitted asm | 5-15% |
| 9.4 | **Register allocation** (proper graph-coloring or linear-scan) | 2-3x on hot loops |
| 9.5 | **Inlining** of small functions / single-use functions | 1.5-2x |
| 9.6 | **Type specialization** — when sema proves a variable is always int, emit non-boxed code | 5-10x on numeric code |
| 9.7 | **Escape analysis** — stack-allocate objects that don't escape their function | Removes GC pressure |
| 9.8 | **Loop optimizations** — unrolling, strength reduction, vectorization | 2-10x on loops |
| 9.9 | **PGO** (profile-guided optimization) | 10-30% |
| 9.10 | **LTO** (whole-program optimization) | 5-15% |

CPython itself isn't a speed champion; mamba beating it by 5-10x on numeric code is the *baseline* expectation for a compiler. Beating PyPy on tight loops takes years; that's not the goal.

---

## Tier 10 — Tooling and ecosystem *(~3 weeks)*

What makes mamba *easy to use* in addition to capable.

| # | Item |
|---|------|
| 10.1 | **`mamba init <name>`** scaffolds a project (entry point, .gitignore, README) |
| 10.2 | **`mamba build`** auto-discovers the entry point and produces a binary |
| 10.3 | **`mamba run`** = build + execute |
| 10.4 | **`mamba test`** runs the test directory |
| 10.5 | **`mamba check`** type-checks without compiling — fast feedback loop |
| 10.6 | **`mamba fmt`** style enforcement (or delegate to Black) |
| 10.7 | **`mamba doctor`** diagnoses toolchain problems (missing NASM, wrong gcc, etc.) |
| 10.8 | **VS Code extension** — syntax highlighting, diagnostics inline, build button |
| 10.9 | **LSP server** — go-to-def, find-references, autocomplete in any editor |
| 10.10 | **Incremental compilation** — only re-emit changed files |
| 10.11 | **Cross-compilation pre-built**: ship the runtime archive for every target as a release artifact, so users don't need to assemble it themselves |
| 10.12 | **One-command install**: `curl … \| sh` (Linux/Mac) or single-MSI (Windows). Bundle NASM and a stripped gcc; users shouldn't have to install a toolchain |
| 10.13 | **Hosted CI templates** — GitHub Actions, GitLab CI examples |
| 10.14 | **Auto-update**: `mamba self update` |

---

## Tier 11 — Platform and target expansion *(~2-3 weeks)*

| # | Item |
|---|------|
| 11.1 | **macOS x86-64** target — Mach-O object format, slightly different linker invocation, codesigning |
| 11.2 | **macOS ARM64** target — Apple Silicon. Different ISA, different ABI |
| 11.3 | **Linux ARM64** target — Raspberry Pi, server VMs |
| 11.4 | **Linux RISC-V** target — emerging platform |
| 11.5 | **WebAssembly** target — Python in the browser, edge functions |
| 11.6 | **iOS / Android** targets — mobile |
| 11.7 | **AOT cross-compile from any host** — Windows host can produce a Linux binary, etc. |

x86-64 → ARM64 is the biggest jump (different instruction encoding, different ABI). A v1 strategy: keep x86-64 ISA-specific code, then add a second backend for ARM64 by retargeting the IR layer (which we don't have yet — see Tier 9.4).

---

## Tier 12 — C extension compatibility *(stretch — ~2-4 months)*

The hard limit on "99.9% of Python scripts." Many real packages ship `.so`/`.pyd` files compiled against CPython's C API.

| # | Item | Approach |
|---|------|----------|
| 12.1 | **Subset of CPython C API** that pure-Python packages declare | Implement `Py_BuildValue`, `PyArg_ParseTuple`, `PyDict_New`, etc. against mamba's runtime |
| 12.2 | **numpy compatibility shim** | Either implement enough numpy in pure mamba, or get binary numpy to load against our shim. |
| 12.3 | **`ctypes`** | Pure FFI; should be implementable directly. |
| 12.4 | **`cffi`** | Same as 12.3. |
| 12.5 | **Bridge mode**: ship a `libpython3.so` shim that real CPython extensions can link against; mamba intercepts the calls and bridges to its own runtime | High effort, high payoff if achievable. |

Realistically: full C-API compatibility is unlikely. The win is "compatible with C-extension *usage patterns*, not all C extensions." numpy is essential; everything else is opt-in.

---

## Effort totals (rough, calendar-time)

| Tier | Time | Cumulative |
|------|------|------------|
| Tier 1 — Strings | 1 week | 1 week |
| Tier 2 — Collections | 2 weeks | 3 weeks |
| Tier 3 — Polymorphism / generators | 3 weeks | 6 weeks |
| Tier 4 — Memory management | 1 week | 7 weeks |
| Tier 5 — Stdlib bindings | 3-4 weeks | 10-11 weeks |
| Tier 6 — Control flow gaps | 1 week | 11-12 weeks |
| Tier 7 — Modules and packages | 2 weeks | 13-14 weeks |
| Tier 8 — Production diagnostics | 1 week | 14-15 weeks |
| Tier 9 — Performance | 2-4 weeks | 16-19 weeks |
| Tier 10 — Tooling | 3 weeks | 19-22 weeks |
| Tier 11 — Platforms | 2-3 weeks | 21-25 weeks |
| Tier 12 — C extensions (stretch) | 2-4 months | 30-40 weeks |

That's **~5-8 months of focused engineering** to reach the 99.9% bar.

A team of two cuts that to ~3-4 months. A team of three or four hits 6-8 weeks if they parallelize well — stdlib bindings (Tier 5) are pleasingly parallel; tooling (Tier 10) is independent of language work; platform work (Tier 11) once the IR layer exists.

---

## Dependency graph (critical path)

```
Tier 1 (strings)
   │
   ├─→ Tier 2 (collections, depends on tagged values + strings)
   │      │
   │      ├─→ Tier 3 (polymorphism — needs collections for vtables)
   │      │      │
   │      │      └─→ Tier 5 stdlib (most modules need objects)
   │      │             │
   │      │             ├─→ Tier 7 (packages — needs working stdlib)
   │      │             │
   │      │             └─→ Tier 12 (C extension shim — needs object model)
   │      │
   │      └─→ Tier 4 (RC — needs all heap-object types known)
   │             │
   │             └─→ Tier 6 (with-statement — needs RC for __exit__)
   │
   └─→ Tier 8 (diagnostics — uses strings everywhere)

Independent / parallel-friendly:
  Tier 9 (perf — builds on whatever's there)
  Tier 10 (tooling — purely additive)
  Tier 11 (platforms — purely additive once IR exists)
```

The **critical path to 99.9%** is: Tier 1 → 2 → 3 → 5 → 7. About 11 weeks if perfectly executed.

---

## Decisions I'm punting on

Things that need agreement before they land:

1. **Boxed vs. unboxed values**. Boxed is simpler, slower, more compatible. Unboxed needs an inference pass and limits dynamism. Pick at the start of Tier 2.
2. **Reference counting vs. tracing GC**. RC integrates simply with exceptions; tracing is faster on average. Pick at the start of Tier 4.
3. **Pure mamba stdlib vs. CPython source ports**. Porting CPython's stdlib Python source as-is could be quick but ties us to its conventions. Pick per-module.
4. **PyPI registry strategy** (Tier 7.9). Curate? Mirror? Test against the top-1000? Pick once Tier 5 is mostly done.
5. **C extension strategy** (Tier 12). Compatible vs. interop-only vs. skip? Pick after Tier 7.10 reveals how many packages actually need C extensions.

---

## Self-host gap audit (what the compiler source actually needs)

The 5,700-line compiler relies on a specific slice of Python. Hitting July means closing **these** features, in roughly this order. Items already shipped are checked.

### Already done

- ✅ Single-target assignment, `+=` family
- ✅ `if/elif/else`, `while`, `for x in range(...)`, `for x in collection`, `break`, `continue`, `pass`
- ✅ `def` with positional args, recursion, early `return`
- ✅ Classes with `__init__`, instance attrs, methods, single inheritance
- ✅ String concat / repeat / `==` / `!=` / `in` / indexing / slicing / `.upper`/`.lower`/`.strip`/`.startswith`/`.endswith`/`.find`/`.count`/`.replace`
- ✅ F-strings as values
- ✅ Dict with str keys and int values
- ✅ `try` / `except` / `raise <str>`
- ✅ `import math` / `from math import ...` against the curated stdlib
- ✅ Multi-arg `print`, `input`, `len`, `int`, `str`, `float`
- ✅ Tuple destructuring `a, b = e1, e2` (incl. swap)

### Blockers for self-host (highest-priority work)

1. ✅ Partial: **`list[str]`, `list[float]`** ship (homogeneous). Still missing: **`list[<instance>]`** (AST node lists), nested lists, mixed-type lists — the compiler's `self.body: list[Stmt]` is the next big hurdle. That needs either an `instance` element type or the full tagged-value runtime.
2. **`dict[str, str]`, `dict[str, <instance>]`** — symbol tables, scope objects, the imported-modules registry. Same blocker class as (1).
3. **First-class tuples and `for k, v in pairs`** — sema/codegen iterate over `(name, type)` pairs constantly.
4. **`dataclass` and `field(default_factory=list)`** — every AST node is a `@dataclass`. Either implement the decorator or strip dataclasses from the compiler source (cheaper short-term).
5. ✅ Partial: **Default arguments** ship for int / str / True / False / None literals; the `*` keyword-only marker and basic type annotations parse without effect. Still missing: float defaults, keyword args at call sites.
6. **`*args` (variadic)** — `emitf` is the worst offender; many helpers also take `*lines`.
7. **Exception classes** — `CompileError`, `LexError`, `SemaError`. The current `raise <str>` form can't carry structured data (positions, codes). Either lift `raise` to accept instances or rewrite the compiler to thread error info manually.
8. **`isinstance(x, (A, B, C))`** — tuple form for "or" type checks; pervasive in `gen_stmt` / `gen_expr`.
9. **`open(path)` / `read()` / `write()` and `with` statement** — the driver reads source files and writes `.asm`. Today it uses `pathlib.Path.read_text` and the `with` form of `open`.
10. **`sys.argv`, `sys.exit`, `subprocess.run`** — the CLI entry point and the driver. `subprocess.run` is what calls NASM and gcc; without it, no linking.
11. **List comprehensions** — peppered through the parser and codegen. Each can in principle be rewritten as a manual loop, but that's a lot of churn.
12. **`enumerate`, `zip`, `sorted`** — every "walk both lists in parallel" or "produce a sorted output" site.
13. **List methods**: `list.extend`, `list.index`, slicing assignment `xs[:] = ...`.
14. **`hasattr` / `getattr`** — used in a couple of places in sema for `b.arg_types` checks.
15. **`Optional[T]` / `T | None`** — type annotations only; ignorable at runtime once the parser accepts the syntax.

### Things the compiler doesn't use (so we don't need them for self-host)

Generators / `yield`, async / `await`, `with` (for non-file uses), `match/case`, `lambda` (mostly), decorators beyond `@dataclass`, multiple inheritance, `super()`, `**kwargs`, `del`, walrus `:=`, set literals, comprehension `if` clauses (used once or twice — can be unrolled), most of `re`, `json`, `time`, `random`, `os.path`. These all stay on the roadmap for the 99.9% target but **don't** gate the July milestone.

### Measuring progress

A `selfhost/check.py` script will live in the repo (forthcoming). It runs `python -m mamba mamba/__main__.py` against every file in `mamba/*.py` and reports the first file (and line number) that fails. Every PR that lands a roadmap item should move that pointer.

---

## Open questions for product

- **Which Python version are we targeting?** 3.11 syntax (`match/case`)? 3.12 (`type` statement)? 3.13 (free-threading)? Default: 3.11.
- **What's the perf target?** Match CPython? Beat it by 2x? 5x?
- **What error level is "incompatible"?** Hard refusal at compile time, or runtime "feature not implemented" message?
- **What's the support window?** Are pinned Python versions a thing here, or does mamba 1.0 just freeze a feature set?
- **Open source strategy?** Apache 2.0? Bring up the project repo publicly?
- **Funding model for the people doing this?** Five months full-time isn't a side project.
