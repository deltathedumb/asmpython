# uasm — a retargetable compiler

**UIR** -- *a Universal Intermediate Representation* -- with pluggable
frontends, backends, targets and toolchains. Python or C in, a native
executable out, and no half knows about another. The IR is written `.uir`
and shipped as `.uirb`; neither spelling mentions either language, because
nothing about the IR does.

```
uasm build prog.py                   # -> prog.exe, ready to run
uasm build prog.c                    # C, too -- the language, not a subset
uasm build prog.c --include-path inc --define N=4   # the C frontend's own flags
uasm build prog.py -O                # optimise first
uasm build prog.py --backend x86-64 --target x86_64-linux
uasm build prog.py --backend arm --bits 64   # a family; --bits picks the member
uasm build prog.py --backend jvm --java-version 21   # -> prog.jar
uasm build prog.py --backend pybc    # -> prog.pyc, `python prog.pyc` runs it
uasm build lib.py --backend cpyext --library   # -> lib.so/.pyd, `import lib`
uasm build prog.py --emit            # artifacts only; do not link
uasm build prog.py --backend x86-64 --emit-asm   # read the generated code
uasm build prog.py --emit-ir         # stop at the IR and read it
uasm run prog.py                     # execute in the reference interpreter
uasm verify prog.py --json           # compile and verify; diagnostics as JSON
uasm link a.ir b.ir -o all.ir        # join modules at the IR
uasm link a.o b.o -o prog            # or objects, into a program
uasm plugin add mypack               # install a plugin and remember it
uasm plugin backends | frontends | linkers | targets | passes
uasm plugin ops | types | libraries | port
```

Five verbs: `build`, `run`, `verify`, `link`, `plugin`. The listings live
under `plugin` because each answers a question about the installation rather
than about a program, and a plugin is why the answer can differ between two
machines.

## Layout

```
src/uasm/
  diagnostics/   spans, structured diagnostics, terminal rendering
  ir/            types, opcodes, module, cfg, builder, verifier, printer,
                 parser, interpreter
  passes/        pass manager with invariant checking, and transforms
  frontend(s)/   source -> IR         (python and c: each the language, not a
                 subset. c's standard library is C this frontend compiles,
                 sitting on the three platform-floor functions and nothing
                 else, so a C program runs on every backend and in the
                 interpreter)
  backend(s)/    IR -> artifacts      (c; x86-64 and arm64, which encode their
                 own instructions and write ELF, COFF and Mach-O objects with
                 no assembler anywhere in the path; jvm; pybc (.pyc); cpyext
                 (a real CPython extension module, .so/.pyd) -- and five more
                 registered but unfinished: see `uasm backends`. `x86`
                 and `arm` are families: --bits chooses the member)
  target(s)/     the platforms        (x86_64-*, aarch64-*, c, jvm, pybc,
                 x86_64-{linux,windows}-cpyext)
  link/          artifacts -> program (cc; jar; pyc; cpyext; baremetal; none)
  backend/objfile/  bytes -> object file (elf; coff; macho -- one writer per
                 format, shared by every architecture that uses it)
  objects/       what a Python value IS at run time: the object runtime as C,
                 the part of it rewritten in IR, and the floor beneath both
  runtime/       that IR part's source, in uasm's own machine subset --
                 compiled into every program that needs it, not imported
  plugins/       third-party registrations: manifest, resolution, install
  driver/        options, pipeline, command line
```

`archived/legacy/asmpython/` is the pre-rewrite compiler, kept for its code
generation and not maintained. It used to collide on the import name — both
this tree and that one answered to `asmpython`, and two packages cannot
share one, so the rewrite claimed the name and the old tree needed
`PYTHONPATH=archived/legacy` to be reached at all. Renaming this tree to
`uasm` retired that collision; the archived tree still isn't installed
anywhere, so `PYTHONPATH=archived/legacy` is still how you reach it. See
[archived/legacy/README.md](archived/legacy/README.md).

Four registries — frontends, backends, targets, toolchains — and the
built-ins register through exactly the same call a third party makes. An
extension path the built-ins bypass is one nobody has tested.

A plugin declares what it provides and is installed once:

```python
# my_plugin_module.py
from uasm.plugins import Plugin, Backend, Target, Frontend, Linker

plugin = Plugin("mypack")
plugin.backends.append(MyBackend())
plugin.add_target(Target("my-machine", arch="my"), aliases=("mm",))

__uasm_plugin__ = plugin
```

```
uasm plugin add my_plugin_module     # remembered; loaded every run
uasm plugin show my_plugin_module    # what it provides, registering none of it
uasm plugin list | remove
uasm build prog.py --backend my-backend
```

`plugin add` resolves from the working directory, then the Python path, then
`pip` — each switchable with `--cwd 1|0`, `--pypath 1|0`, `--pip 1|0`. pip is
off unless you ask, because a build command should not install software from
the network on its own.

A plugin may also patch the compiler directly (`CompilerPatch`) for what the
registries do not cover — with two sealed targets that can never be patched
and a guarded set needing an explicit, reported `force=True`. See
[archived/docs/BACKENDS.md](archived/docs/BACKENDS.md).

### Replacing and refreshing

`plugin add` on a name that is already installed ASKS before replacing it,
because replacing clears a cached copy that may be the only one left -- the
origin it came from is not guaranteed to still exist. `--yes` and `--no`
answer without asking, and a non-interactive run declines rather than
prompting: a build that blocks forever on a hidden question is worse than one
that stops and names the flag.

An installed plugin is CACHED and loaded from the cache, not from wherever it
was found, so an install keeps working when the original file moves or the
compiler is run from another directory. `origin` stays recorded for exactly
one purpose:

```bash
uasm plugin invalidate mypack           # one id
uasm plugin invalidate a,b              # comma-separated
uasm plugin invalidate a b              # or repeated
uasm plugin invalidate --all
```

`invalidate` goes back to the origin, re-resolves, and refreshes the cache --
which is how an edited plugin under development is picked up. If the origin is
gone it fails and says so, leaving the cached copy in place: a cache that no
longer matches any real source is exactly the state worth being told about.

For one invocation, or without installing: `--plugin MODULE`, or
`UASM_PLUGINS=mypack`. An installed distribution needs none of it if it
advertises an `uasm.plugins` entry point.

## The three decisions

**The type is a field on the instruction, not part of the mnemonic.**
`%3 = i64.add %1, %2` — everything a backend needs is on the line it is
reading. No value-type table, no inference. Baking the width into the name
(WebAssembly's `i32.add`, `i64.add`, …) would multiply the opcode table out to
several hundred entries; a field keeps it at **39**.

**Signedness lives on the type.** One `DIV`, its meaning read from `ty`.

**Registers are mutable and there are no phi nodes.** Where SSA would need one,
a frontend assigns the same register on both paths. That removes the hardest
concept from a backend author's path, at the cost of making read-before-write
possible — so the verifier runs a forward dataflow to a fixed point to catch
exactly that.

## The verifier is a contract

It lists ten invariants a backend may assume *without defensive checks*, and
every one has a test that breaks it and asserts the verifier notices.

## Where the language lives

`Op.DIV` truncates toward zero, like C and like every machine. Python's `//`
floors. So the Python frontend lowers `//` to a truncating division plus a
correction — emitted **once in the frontend** rather than Python's semantics
owed by each backend.

That is the whole argument for a small IR, and it is checked: the test suite
runs each program six ways — CPython, the interpreter on unoptimised IR, the
interpreter on optimised IR, and all three backends compiled, linked and
executed — and all six must agree. The AArch64 one runs under
`qemu-system-aarch64`, which needs no ARM hardware because the target is bare
metal: the image boots directly with `-M virt -kernel`, no guest OS involved.

## Installed packages

`import requests` resolves against the host Python installation's
`site-packages` — a **library point**, which is a search root that came from
an interpreter rather than from the command line. `uasm libraries` prints
the ones in force and which interpreter they came from; `--host-python PATH`
asks a different installation, and `--no-site-packages` searches none.

Library points are searched **last**: after the source's own directory, after
every `--import-path`, and after the bundled standard library. So a package
installed years ago cannot decide what a name in this program means.

The order as a whole is CPython's `sys.path`: the source's own directory, then
`--import-path`, then the standard library, then library points. A file beside
the program therefore *does* displace a standard module of the same name, as
it does under CPython -- and `-P` / `--safe-path` leaves that directory out,
exactly as CPython's `-P` does.

A pip package is ordinary Python source, so it is **spliced exactly as the
bundled standard library is** — mangled, ordered so a dependency precedes its
importer, merged into the one module the frontend knows. There is no import
system at run time and no module objects. The consequence worth stating: the
whole transitive closure has to compile, and a construct one of those files
uses that this compiler does not accept is a gap worth closing rather than a
reason to drop back to C.

A **compiled extension module** — `.pyd`, `.so` — is not source and is refused
with `E0129` naming the file and the distribution it came from, rather than
`E0083` about a file that is plainly sitting right there. It is a native binary
built against CPython's C API, so using a THIRD-PARTY one needs that API
implemented against this object runtime; loading it is the smaller half, and
`dynlib` in `objects/hostsvc.py` is that half. That is the LOAD direction. The
EMIT direction — uasm producing its own `.pyd`/`.so`, rather than
consuming someone else's — is the `cpyext` backend, and does not need CPython's
C-API implemented against this object runtime: it needs only enough of that
API to convert values at the boundary, written once as hand-generated glue
around the ordinary `c` backend's output. See `uasm build --backend
cpyext --library` and `backends/cpyext/emit.py`.

## Native libraries

A shared library with a C ABI is declared, then imported:

```json
[{"module": "user32", "library": "user32.dll", "target_os": "windows",
  "functions": [{"name": "GetSystemMetrics", "args": ["c_int"],
                 "ret": "c_int"}]}]
```

```
import user32
print(user32.GetSystemMetrics(0))     # 1920
```

```sh
uasm build app.py --native-library libs.json
```

A declared function **is a `ctypes` function whose `argtypes` are already
known**, so the declaration fills in exactly the state `CDLL(...)` plus an
`argtypes` and a `restype` assignment would have left behind, and every path
after that — argument checking, conversion, the single `Op.CALL`, the link —
is the same tested code. That is why this is a declaration format and not a
second way to call a native function.

A signature is required and never guessed, for the reason `ctypes` gives: there
is nothing to read a foreign symbol's argument kinds out of, and a wrong one
truncates rather than failing. `"args": []` declares a function that takes
none; omitting `args` is refused. `symbol` renames, so the program may import
`metric` and the linker still sees `GetSystemMetrics`. Two libraries may share
one `module` name scoped by `target_os`, and the one matching the target wins —
an unscoped declaration is the fallback, never the winner.

A declaration cannot shadow a module the compiler already has (`E0131`), and a
library is imported whole rather than from (`E0130`). This is a C ABI only: no
C++ mangling, vtables or exceptions — export an `extern "C"` wrapper — and it
is not a CPython extension module, which is `E0129` above.

## Extending it

| you want | read | register with |
| --- | --- | --- |
| a language | [archived/docs/FRONTENDS.md](archived/docs/FRONTENDS.md) | `uasm.frontend.register` |
| a code generator | [archived/docs/BACKENDS.md](archived/docs/BACKENDS.md) | `uasm.backend.register` |
| a platform | [archived/docs/TARGETS.md](archived/docs/TARGETS.md) | `uasm.target.register` |
| a way to link | [archived/docs/LINKERS.md](archived/docs/LINKERS.md) | `uasm.link.register` |

[archived/docs/LANGUAGE.md](archived/docs/LANGUAGE.md) describes what the
Python frontend accepts — which is Python, on two paths — and the four places
Python and the machine disagree.

## The C frontend

It compiles C, and `src/uasm/frontends/c/__init__.py` lists the four
places it knowingly differs from a hosted implementation on x86-64 Linux —
the whole list, rather than the beginning of one:

  * `long double` is **software**. The format is x86-64's — 80-bit
    extended, sixteen bytes, a 64-bit significand — so `sizeof`, `LDBL_*`
    and every printed digit agree with a hosted compiler; the arithmetic is
    a support unit written in C, because the IR has `f32` and `f64` and a
    third width would have to be implemented by every backend. The `l`
    functions in `<math.h>` compute in double and are accurate to about a
    double's precision; `sqrtl`, the conversions and the four operators are
    exact.
  * `_Imaginary` is not there, which is conforming rather than missing:
    imaginary types are Annex G, supported only by an implementation that
    says so, and gcc has never had them either. `_Complex` is complete,
    Annex G's multiply and divide included.
  * A local that is not `volatile` **survives** a `longjmp`. C says its
    value is indeterminate; here the frame never went away, so it keeps what
    it had — `setjmp` is compiled into a branch and every call site into a
    check, rather than into a saved machine frame there is no way to name.
  * `localtime` **is** `gmtime`. The host services can say what time it is
    and cannot say what the local offset from UTC is, so the calendar is UTC
    and `tm_isdst` is 0 — not unknown, not in effect.

Everything else is implemented — VLAs, flexible array members, bit-fields,
anonymous members, `_Generic`, designated initialisers, compound literals,
`__VA_OPT__`, K&R definitions, statement expressions, and GNU's `__typeof__`
and `__restrict` spellings because real headers use them — and the
preprocessor is Prosser's algorithm, which is to say it agrees with the standard's own
worked examples rather than with an opinion about what recursive macro
expansion should mean.

**The standard library is C, compiled by this frontend**, from
`frontends/c/include/`. Computing and printing sit on the three floor
functions and nothing else, which is what makes such a program run on every
backend and in the IR interpreter: a backend that can run a Python program can
already run a C one. Reading a file, asking the time, looking at the
environment or running another program reach `objects/hostsvc.py`'s optional
groups, and a backend whose target has not got one refuses such a program by
name at compile time rather than leaving an undefined symbol for the linker.
`printf`'s floating-point conversion is exact rather than approximate, because
every finite double is a terminating decimal and `%f` of `1e300` has a right
answer with 301 digits in it — and `strtod` is exact in the other direction,
so the round trip through `%.17g` recovers the value it started with.

Several translation units in one build: `uasm build main.c --c:unit parse.c
--c:unit emit.c` compiles each on its own and joins the modules, which is what
`cc main.c parse.c emit.c` does and means the same things by it — a `static`
in one file is not the one of that name in the next, and two definitions of
one external name is an error naming both files. It is a flag rather than
several positional sources because the driver hands each frontend one source,
and that is what names the output and roots the search path.

All thirty-one headers C23 requires are there. One of them refuses with a
reason rather than being absent — `<threads.h>`, explaining what the IR
cannot express and what to write instead — because a missing file is a
mystery and a refusal is an answer.
`<stdatomic.h>` is supported and `<threads.h>` is not, which is not a
contradiction: with one thread every operation is already atomic.

Three paths are compared for every test program — the host's `cc`, the
reference interpreter, and the C backend's output compiled by `cc` — and all
three must agree on output and exit status. One program goes further and is
also built through the **x86-64** backend, which encodes its own instructions
and writes its own ELF object with no assembler in the path, and through the
**jvm** backend into a jar; both print what `cc` prints, which is the point of
a language-independent IR stated as a test rather than as a claim.

Struct layout is checked against the host compiler's own `sizeof` and
`_Alignof`, because a struct's layout is an ABI and a frontend can be
self-consistently wrong about one.

## Running the tests

```
python -m tests.harness
```

**CPython 3.14 or newer**, and not because the suite is fussy: the Python
frontend parses a user's program with the HOST's `ast`, so an older
interpreter rejects `except A, B:` and the rest of 3.14 as syntax errors
against valid programs. The suite compares every program with the CPython
running it, so below 3.14 it is the ORACLE that is wrong, and it says so
several hundred times.

The C and x86-64 stages need a C compiler on PATH, and the AArch64 stage needs
`aarch64-none-elf-gcc` and `qemu-system-aarch64`; without them those tests
skip rather than fail. A machine without any of it can still run everything
else, and a red suite people are told to ignore is worse than a smaller green
one.

Neither AArch64 tool puts itself on PATH after an unzip on Windows, so the
usual install locations are checked as well, and either can be pointed
somewhere else:

```
UASM_AARCH64_BIN=/path/to/aarch64-none-elf/bin
UASM_QEMU_BIN=/path/to/qemu
```
