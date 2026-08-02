# Writing a backend

A backend turns verified IR into artifacts. This is everything you need to
know, and it is deliberately one document — if writing a backend required
reading a second, the second one would be the bug.

## The interface

```python
from asmpython.backend import Backend, Target, register
from asmpython.ir import Module

class MyBackend(Backend):
    name = "my-machine"
    description = "emits code for the My-Machine ISA"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        ...

register(MyBackend())
```

That is the whole contract. One method, returning `{filename: contents}`.

## What you may assume

`module` has passed `verify()`. Every one of these holds, and **you should
write no code checking them**:

1. Every block ends in exactly one terminator, and no terminator appears
   anywhere but last.
2. Every branch target names a block in the same function.
3. Every register read is declared, and written on every path that reaches
   the read.
4. Every register has one type for its whole life.
5. Operand counts and types match the opcode.
6. `ty` is a type the opcode permits.
7. Comparisons define an `i1`; `branch` reads an `i1`.
8. Every `call` names a function in the module.
9. The entry block has no predecessors — put your prologue there without
   checking.
10. A non-void function ends every path in `ret` with a value of that type.

If one of these is ever false, that is a compiler bug, not input you must
survive. Report it rather than working around it.

## The shape of every backend

```python
for function in module.defined_functions():
    emit_prologue(function)
    for block in function.blocks:
        emit_label(block.label)
        for ins in block.instructions:
            match ins.op:                 # <- the entire job
                case Op.ADD: ...
                case Op.LOAD: ...
                case _:
                    raise NotImplementedError(ins.op)
    emit_epilogue(function)
```

`backends/c/emit.py` is exactly this, complete, in about 200 lines. Read it
before writing anything. `backends/arm64/emit.py` is the same shape for a real
machine, and its header lists what differs from x86-64 and what each
difference costs — three-operand arithmetic, no memory operands, no 64-bit
immediate, no remainder instruction, and a stack pointer that must stay
aligned at all times rather than only at a call.

The `case _` matters. An unhandled opcode must fail loudly at build time; a
silent fallthrough emits nothing and produces a program that is wrong in a way
no test names.

## Reading an instruction

```python
%3 = i64.add %1, %2      Instruction(op=Op.ADD, ty=i64, dst=3, args=[1, 2])
```

- **`ins.ty`** is the width to operate at. For a comparison it is the type of
  the *operands* — the result is always `i1`.
- **`ins.args`** are always register ids. Never literals, never nested
  expressions. Look a register's type up with `function.register_type(reg)`.
- **`ins.imm`** carries the literal for `CONST` and the byte count for
  `ALLOCA`, and is `None` otherwise.
- **`ins.span`** is where it came from in the user's source, for diagnostics.

Signedness is on the **type**, not the opcode. There is one `DIV`; it is signed
on `i*`, unsigned on `u*`, and floating on `f*`. Same for `SHR`, `LT`, `LE`,
`GT`, `GE`.

There are **no phi nodes**. Registers are mutable, so where SSA would need one
the frontend has already assigned the same register on both paths.

## Registers: you may ignore them entirely

The simplest correct backend gives every virtual register its own stack slot
and never allocates a machine register. That is what `backends/c` does, and it
is the right place to start — allocation is an optimisation, and starting with
it produces backends that are subtly wrong under register pressure, which is
the hardest case to debug.

When you want it:

```python
from asmpython.backend import RegisterFile, allocate, verify_allocation

alloc = allocate(function, RegisterFile(
    general=("rax", "rcx", "rdx", "rbx", "rsi", "rdi"),
    callee_saved=frozenset({"rbx"}),
    reserved=frozenset({"rsp", "rbp"}),
))

alloc.location(reg)          # InRegister("rcx") or InSlot(16)
alloc.frame_size             # bytes to reserve
alloc.used_callee_saved      # save these in the prologue, and only these
```

**Run `verify_allocation` under a debug flag.** It checks the two properties
that fail silently: that no two simultaneously-live values share a register,
and that nothing live across a call sits in a caller-saved one. The second is
the nastier bug — the callee only destroys the value when it happens to use
that register, so it appears intermittently.

This is not hypothetical. The tree this replaces had two hand-synchronised
copies of the liveness analysis; one received a fix the other did not, and on a
single test program one backend produced **949,336** conflicting register pairs
against the other's zero. Nothing was checking.

## Testing it

Diff against the reference interpreter. It is the executable specification, so
any disagreement is your bug, localised to one program:

```python
from asmpython.ir.interpreter import Interpreter
from io import StringIO

out = StringIO()
expected = Interpreter(module, out=out).run("main")
# ... run your artifact, compare stdout and the exit value
```

Start with hand-written IR rather than compiled Python. Twenty lines you wrote
yourself is a far better first test than a program that requires a frontend, a
runtime and your backend to all be correct simultaneously:

```bash
asmpython build prog.py --emit-ir -o prog.ir     # then edit prog.ir freely
asmpython run prog.ir                            # what it SHOULD do
```

## Targets

`emit` receives a `Target` rather than baking one in, so one code generator can
serve several platforms. Targets live in their own registry — see
[TARGETS.md](TARGETS.md) — and a backend names the one it defaults to:

```python
class MyBackend(Backend):
    name = "my-machine"
    default_target = "my-machine-linux"   # a NAME, resolved by the driver
```

**Read the fields; never parse `target.name`.** This backend used to choose
its calling convention with `"windows" in target.name`. It worked for the two
targets that shipped and silently gave System V to everything else — a target
called `uefi-x64` would have compiled, linked, and passed its arguments in the
wrong registers, which appears as corrupted data inside a callee and nowhere
near the call. `target.abi` says what it is.

A backend supporting only one shape ignores the fields it does not vary over.
One it cannot support at all should refuse:

```python
raise BackendUnsupported(
    f"target {target.name!r} declares ABI {target.abi!r}, which this "
    f"backend does not implement")
```

`BackendUnsupported` becomes a diagnostic naming the backend and the target.
Any other exception reaches the user as a traceback with a compiler stack in
it, which reads as "you found a bug in asmpython" when it means "use another
backend".

## Producing a program

`emit` returns artifacts, not an executable. Turning `{filename: bytes}` into
something runnable is a *toolchain*, and the shipped one hands your `.s` or
`.c` to a C compiler driver. See [LINKERS.md](LINKERS.md).

Two things a machine backend owes it:

```python
class MyBackend(Backend):
    self_contained = False    # my artifacts need the runtime linked in
```

and emitting the IR's `main` under `ENTRY_SYMBOL`, not as `main` — it returns
i64 where C requires int, and would collide with the runtime's entry point:

```python
from asmpython.backend import ENTRY_SYMBOL

def symbol(self, name):
    return ENTRY_SYMBOL if name == "main" else name
```

Use one function for that, called at definitions **and** at call sites. Here
they were computed separately, and the moment the entry symbol was renamed,
`main` defined its labels under one name and jumped to another — accepted by
the assembler, rejected by the linker.

## Arguments are a parallel assignment

If your backend moves values into ABI registers, emit the moves in an order
that respects their dependencies:

```asm
movq %rax, %rcx     arg0 (in rax) -> rcx
movq %rcx, %rdx     arg1 was IN rcx, which the line above destroyed
```

Nine arguments summed to 30 instead of 36 that way, in a program that
compiled, linked and ran. Emit any move whose destination is nobody else's
source, repeat, and break a real cycle (`f(b, a)`) through a scratch register.
A memory source cannot be clobbered — but its *destination* is still someone
else's source, which is a second, quieter version of the same bug.

## Checklist

- [ ] `name` and `description` set; `register()` called at import
- [ ] Listed in `asmpython.backend.load_builtin`
- [ ] `case _` raises on an unhandled opcode
- [ ] No defensive checks for the ten invariants above
- [ ] Behaviour read from `target` fields, never parsed out of `target.name`
- [ ] Anything unsupported raises `BackendUnsupported`, not a bare exception
- [ ] `self_contained` set correctly, and the IR's `main` emitted under
      `ENTRY_SYMBOL` by one function used at definitions and call sites
- [ ] Argument moves scheduled, not emitted in argument order
- [ ] Diff-tested against the interpreter on at least: arithmetic at several
      widths, a loop, a call, and a comparison
- [ ] If you allocate registers, `verify_allocation` is clean at small register
      counts — that is where conflicts appear
