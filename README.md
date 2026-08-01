# apc — a retargetable compiler

A language-independent IR with pluggable frontends and backends. Python in,
native code out, and neither half knows about the other.

```
apc build prog.py -O -o prog.c      # source -> IR -> optimise -> backend
apc build prog.py --emit-ir         # stop at the IR and read it
apc run prog.py                     # execute in the reference interpreter
apc check prog.py                   # analyse and verify, produce nothing
apc ops | types | passes | backends | frontends
```

## Layout

```
src/apc/
  diagnostics/   spans, structured diagnostics, terminal rendering
  ir/            types, opcodes, module, cfg, builder, verifier, printer,
                 parser, interpreter
  passes/        pass manager with invariant checking, and transforms
  frontend(s)/   source -> IR       (python: an annotated subset)
  backend(s)/    IR -> artifacts    (c: portable C99)
  driver/        options, pipeline, command line
```

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
every one has a test that breaks it and asserts the verifier notices. See
[docs/BACKENDS.md](docs/BACKENDS.md).

## Where the language lives

`Op.DIV` truncates toward zero, like C and like every machine. Python's `//`
floors. So the Python frontend lowers `//` to a truncating division plus a
correction — five instructions, emitted **once in the frontend** rather than
Python's semantics owed by each backend.

That is the whole argument for a small IR, and it is checked: the test suite
runs each program four ways — CPython, the interpreter on unoptimised IR, the
interpreter on optimised IR, and the C backend compiled and executed — and all
four must agree.

## Running the tests

```
python -m pytest tests/apc -q
```
