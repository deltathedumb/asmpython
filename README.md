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
  driver/        options, pipeline, command line
```

`legacy/asmpython/` is the pre-rewrite compiler, kept for its code generation
and not maintained. It answers to the same import name, and two packages
cannot share one — so the rewrite owns `asmpython` and the old tree needs
`PYTHONPATH=legacy`. See [legacy/README.md](legacy/README.md).

Four registries — frontends, backends, targets, toolchains — and the
built-ins register through exactly the same call a third party makes. An
extension path the built-ins bypass is one nobody has tested.

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

The C and x86-64 stages need a C compiler on PATH; without one they skip
rather than fail. A machine without gcc can still run everything else, and a
red suite people are told to ignore is worse than a smaller green one.
