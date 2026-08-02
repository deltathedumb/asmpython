# asmpython — a retargetable compiler

A language-independent IR with pluggable frontends, backends, targets and
toolchains. Python in, a native executable out, and no half knows about
another.

```
asmpython build prog.py                   # -> prog.exe, ready to run
asmpython build prog.py -O                # optimise first
asmpython build prog.py --backend x86-64 --target x86_64-linux
asmpython build prog.py --emit            # artifacts only; do not link
asmpython build prog.py --emit-ir         # stop at the IR and read it
asmpython run prog.py                     # execute in the reference interpreter
asmpython check prog.py                   # analyse and verify, produce nothing
asmpython ops | types | passes | backends | frontends | targets | toolchains
```

## Layout

```
src/asmpython/
  diagnostics/   spans, structured diagnostics, terminal rendering
  ir/            types, opcodes, module, cfg, builder, verifier, printer,
                 parser, interpreter
  passes/        pass manager with invariant checking, and transforms
  frontend(s)/   source -> IR         (python: an annotated subset)
  backend(s)/    IR -> artifacts      (c; x86-64; arm64)
  target(s)/     the platforms        (x86_64-*, aarch64-*, c)
  link/          artifacts -> program (cc; baremetal; none)
  plugins/       third-party registrations: manifest, resolution, install
  driver/        options, pipeline, command line
```

`legacy/asmpython/` is the pre-rewrite compiler, kept for its code generation
and not maintained. It answers to the same import name, and two packages
cannot share one — so the rewrite owns `asmpython` and the old tree needs
`PYTHONPATH=legacy`. See [legacy/README.md](legacy/README.md).

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
[docs/BACKENDS.md](docs/BACKENDS.md).

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

## Extending it

| you want | read | register with |
| --- | --- | --- |
| a language | [docs/FRONTENDS.md](docs/FRONTENDS.md) | `asmpython.frontend.register` |
| a code generator | [docs/BACKENDS.md](docs/BACKENDS.md) | `asmpython.backend.register` |
| a platform | [docs/TARGETS.md](docs/TARGETS.md) | `asmpython.target.register` |
| a way to link | [docs/LINKERS.md](docs/LINKERS.md) | `asmpython.link.register` |

[docs/LANGUAGE.md](docs/LANGUAGE.md) describes the Python subset — what it
accepts, and the four places Python and the machine disagree.

## Running the tests

```
python -m pytest tests/asmpython -q
```

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
