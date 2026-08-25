# asmpython — a retargetable compiler

A language-independent IR with pluggable frontends, backends, targets and
toolchains. Python in, a native executable out, and no half knows about
another.

```
asmpython build prog.py                   # -> prog.exe, ready to run
asmpython build prog.py -O                # optimise first
asmpython build prog.py --backend x86-64 --target x86_64-linux
asmpython build prog.py --backend jvm --java-version 21   # -> prog.jar
asmpython build prog.py --emit            # artifacts only; do not link
asmpython build prog.py --emit-ir         # stop at the IR and read it
asmpython run prog.py                     # execute in the reference interpreter
asmpython check prog.py                   # analyse and verify, produce nothing
asmpython ops | types | passes | backends | frontends | targets | toolchains
asmpython libraries                       # where installed packages resolve from
```

## Layout

```
src/asmpython/
  diagnostics/   spans, structured diagnostics, terminal rendering
  ir/            types, opcodes, module, cfg, builder, verifier, printer,
                 parser, interpreter
  passes/        pass manager with invariant checking, and transforms
  frontend(s)/   source -> IR         (python: the language, not a subset)
  backend(s)/    IR -> artifacts      (c; x86-64; arm64; jvm -- and six
                 more registered but unfinished: see `asmpython backends`)
  target(s)/     the platforms        (x86_64-*, aarch64-*, c, jvm)
  link/          artifacts -> program (cc; jar; baremetal; none)
  objects/       what a Python value IS at run time: the object runtime as C,
                 the part of it rewritten in IR, and the floor beneath both
  runtime/       that IR part's source, in asmpython's own machine subset --
                 compiled into every program that needs it, not imported
  plugins/       third-party registrations: manifest, resolution, install
  driver/        options, pipeline, command line
```

Each backend also declares an **alib** — `<arch>.alib`, the low-level library
for the machine it emits: MMIO, ports, barriers, system registers. It hangs
off the backend rather than living in a registry of its own, because an alib
describes instructions something can produce, and the code generator is that
something. `asmpython alibs` lists them and says how much of each is real.

`archived/legacy/asmpython/` is the pre-rewrite compiler, kept for its code
generation and not maintained. It answers to the same import name, and two
packages cannot share one — so the rewrite owns `asmpython` and the old tree
needs `PYTHONPATH=archived/legacy`. See
[archived/legacy/README.md](archived/legacy/README.md).

Four registries — frontends, backends, targets, toolchains — and the
built-ins register through exactly the same call a third party makes. An
extension path the built-ins bypass is one nobody has tested.

A plugin declares what it provides and is installed once:

```python
# my_plugin_module.py
from asmpython.plugins import Plugin, Backend, Target, Frontend, Linker

plugin = Plugin("mypack")
plugin.backends.append(MyBackend())
plugin.add_target(Target("my-machine", arch="my"), aliases=("mm",))

__asmpython_plugin__ = plugin
```

```
asmpython plugin add my_plugin_module     # remembered; loaded every run
asmpython plugin show my_plugin_module    # what it provides, registering none of it
asmpython plugin list | remove
asmpython build prog.py --backend my-backend
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
asmpython plugin invalidate mypack           # one id
asmpython plugin invalidate a,b              # comma-separated
asmpython plugin invalidate a b              # or repeated
asmpython plugin invalidate --all
```

`invalidate` goes back to the origin, re-resolves, and refreshes the cache --
which is how an edited plugin under development is picked up. If the origin is
gone it fails and says so, leaving the cached copy in place: a cache that no
longer matches any real source is exactly the state worth being told about.

For one invocation, or without installing: `--plugin MODULE`, or
`ASMPYTHON_PLUGINS=mypack`. An installed distribution needs none of it if it
advertises an `asmpython.plugins` entry point.

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
an interpreter rather than from the command line. `asmpython libraries` prints
the ones in force and which interpreter they came from; `--host-python PATH`
asks a different installation, and `--no-site-packages` searches none.

Library points are searched **last**: after the bundled standard library, after
the source's own directory, and after every `--import-path`. So a package
installed years ago cannot decide what a name in this program means, and
nothing that resolved before library points existed resolves differently now.

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
built against CPython's C API, so using one needs that API implemented against
this object runtime; loading it is the smaller half, and `dynlib` in
`objects/hostsvc.py` is that half.

## Extending it

| you want | read | register with |
| --- | --- | --- |
| a language | [archived/docs/FRONTENDS.md](archived/docs/FRONTENDS.md) | `asmpython.frontend.register` |
| a code generator | [archived/docs/BACKENDS.md](archived/docs/BACKENDS.md) | `asmpython.backend.register` |
| a platform | [archived/docs/TARGETS.md](archived/docs/TARGETS.md) | `asmpython.target.register` |
| a way to link | [archived/docs/LINKERS.md](archived/docs/LINKERS.md) | `asmpython.link.register` |

[archived/docs/LANGUAGE.md](archived/docs/LANGUAGE.md) describes what the
Python frontend accepts — which is Python, on two paths — and the four places
Python and the machine disagree.

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
ASMPYTHON_AARCH64_BIN=/path/to/aarch64-none-elf/bin
ASMPYTHON_QEMU_BIN=/path/to/qemu
```
