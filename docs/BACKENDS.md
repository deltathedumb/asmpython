# Writing a backend

A backend turns verified IR into artifacts. This is everything you need to
know, and it is deliberately one document — if writing a backend required
reading a second, the second one would be the bug.

## The interface

```python
from apc.backend import Backend, Target, register
from apc.ir import Module

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
before writing anything.

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
from apc.backend import RegisterFile, allocate, verify_allocation

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
from apc.ir.interpreter import Interpreter
from io import StringIO

out = StringIO()
expected = Interpreter(module, out=out).run("main")
# ... run your artifact, compare stdout and the exit value
```

Start with hand-written IR rather than compiled Python. Twenty lines you wrote
yourself is a far better first test than a program that requires a frontend, a
runtime and your backend to all be correct simultaneously:

```
apc build prog.py --emit-ir -o prog.ir     # then edit prog.ir freely
apc run prog.ir                            # what it SHOULD do
```

## Targets

`emit` receives a `Target` rather than baking one in, so one code generator can
serve several configurations:

```python
Target(name="x86_64-linux", pointer_size=8, little_endian=True,
       stack_alignment=16, object_format="elf")
```

A backend supporting only one shape simply ignores the fields it does not vary
over.

## Checklist

- [ ] `name` and `description` set; `register()` called at import
- [ ] Listed in `apc.backend.load_builtin`
- [ ] `case _` raises on an unhandled opcode
- [ ] No defensive checks for the ten invariants above
- [ ] Diff-tested against the interpreter on at least: arithmetic at several
      widths, a loop, a call, and a comparison
- [ ] If you allocate registers, `verify_allocation` is clean at small register
      counts — that is where conflicts appear
