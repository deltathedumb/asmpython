# The asmpython IR: a language-neutral contract

asmpython's SSA IR ([`ir.py`](ir.py)) is **language-neutral**. Nothing in the IR
encodes Python — or any source language's — semantics. A frontend for C, Lua, a
DSL, or Python all target the *same* instruction and type vocabulary; the
backends ([`_backends/`](../_backends/)) consume that vocabulary without knowing
which frontend produced it.

This document is the contract: what belongs in the IR, what does **not**, and how
a new frontend emits well-formed modules.

## The hourglass

```
  Python frontend ─┐
  (C frontend)     ├──►  neutral SSA IR  ──►  x86_64 / x86 / arm64 / ...
  (Lua frontend)   ┘      (ir.py)               backends + runtime
        │                                             │
   lowering owns                               ABI/encoding owns
   LANGUAGE semantics                          MACHINE semantics
```

The IR is the waist. Everything language-specific lives **above** it (in a
frontend's lowering); everything machine-specific lives **below** it (in a
backend). The IR itself is neither.

## What is in the IR (neutral)

**Types** — a small machine-facing system (see `ir.py`'s module docstring):

| Type            | `name`                    | Notes                              |
|-----------------|---------------------------|------------------------------------|
| Signed int      | `i1` `i8` `i16` `i32` `i64` | `signed == True`                 |
| Unsigned int    | `u8` `u16` `u32` `u64`    | `signed == False`                  |
| Float           | `f32` `f64`               |                                    |
| Pointer         | `ptr`                     | always 8 bytes; optional `pointee` |
| Vector          | `v128`                    | SIMD                               |
| Struct          | (advisory name)           | `fields=(...)`, C-style layout     |

`name` is the canonical tag every backend switches on. `kind` / `bits` /
`signed` / `size_bytes` / `align` are derived from it. Construct via the
constants (`I32`, `U8`, `F64`, `PTR`, ...) or helpers `int_type(bits, signed)`,
`float_type(bits)`, `ptr_to(pointee)`, `struct_type(fields, name)`.

**Ops** — an LLVM-class set, already language-neutral:

- arithmetic `iadd isub imul idiv irem` + unsigned `udiv urem`; `ineg`
- bitwise `iand ior ixor inot`; shifts `shl shr sar`
- compares `icmp.{eq,ne,lt,le,gt,ge}` (signed) + `icmp.{ult,ule,ugt,uge}`
- float `fadd fsub fmul fdiv fneg fcmp.*`
- conversions `sext zext trunc sitofp fptosi fpext fptrunc bitcast`
- memory `alloca load store gep global_addr`
- control `br br.t ret`; `const`; `call`
- SIMD `simd.*`

Signedness is carried on **both** the type and the op (`idiv` vs `udiv`); the
type is the source of truth, the op selects the machine instruction.

**Aggregates live in memory.** A struct value is never in a register. Allocate
storage with `alloca <size_bytes>`, address a field with `gep <ptr>,
<field_offset>`, and read/write it with a width-typed `load`/`store`. The type
model computes the numbers:

```python
Point = struct_type([I32, I32], "Point")   # {i32 x; i32 y;}
Point.size_bytes        # 8   -> alloca operand
Point.field_offset(1)   # 4   -> gep offset operand
Point.field_type(1)     # i32 -> load/store width
```

`gep`'s offset operand is a **byte offset** (an `int` constant, or an `IRValue`
added as-is). Scaling an array index by element size is the frontend's job
(`imul` then `gep`), keeping `gep` itself trivial and backend-stable.

## What is NOT in the IR (frontend lowering owns it)

These are **conventions a frontend emits**, not IR features:

- **Boxing / dynamic values** — the Python frontend's tagged-cell layout
  (`BOX_MAGIC`), builtin **type ids**, `any`-typed dispatch. A C frontend emits
  raw `i32`/`ptr` and never boxes.
- **The object/runtime model** — lists, dicts, strings, GC. These are `call`s to
  runtime symbols (`_abi_*`), chosen by the frontend, not IR ops.
- **The int → machine-width choice** — Python maps `int` → `i64`
  (`_ASM_TYPE_TO_IR`); that mapping is a *frontend* decision. C would emit
  `i32`/`u64`/etc. directly.
- **Exceptions** — surfaced as generic setjmp regions (`IRFunc.try_regions`), a
  mechanism, not a Python-specific policy.

If you find yourself wanting to add a Python concept to `ir.py`, it belongs in
lowering instead.

## Writing a frontend

A frontend implements `IRFrontend.parse` (see [`ir.py`](ir.py) and
[`frontend.py`](../frontend.py)) and returns **either**:

1. a typed AST module the existing `ir_lower` consumes (what the Python frontend
   does), **or**
2. an `IRModule` it built directly — the neutral path a non-Python frontend
   uses.

A directly-built module must pass the verifier.

## The verifier

[`ir_verify.py`](ir_verify.py)'s `validate_ir(module)` checks well-formedness
with **no language knowledge** — the contract a backend can assume:

- every block ends in a terminator (`ret`/`br`/`br.t`), and none appears earlier;
- every `IRValue` operand is defined (param or some instruction's result);
- every branch targets an existing block label;
- every value's type is well-formed (parseable kind / valid struct layout);
- no duplicate block labels or function names.

It deliberately does *not* enforce full SSA dominance (the IR uses a memory-SSA
style: locals are `alloca`+`load`/`store`) or per-op typing policy.

Run it opt-in over the built-in pipeline with `ASMPYTHON_VERIFY_IR=1`, or call it
directly:

```python
from asmpython._compiler.ir_verify import validate_ir
validate_ir(module)            # raises IRVerifyError if malformed
errors = validate_ir(m, strict=False)   # -> list[str]
```

## Passes

An optimization pass is a neutral IR->IR transform implementing `IRPass` (see
[`ir.py`](ir.py)), selected with `--passes`:

```
asmpython build prog.py --passes mem2reg,constfold,dce
asmpython build prog.py --passes o2        # preset
asmpython build prog.py --passes help      # list the registry
```

Built-ins live in [`../_passes/`](../_passes/):

| Pass | Ported from | Does | Needs SSA? |
|---|---|---|---|
| `constfold` | ConstantFolding | fold constant integer arithmetic/compares | no |
| `peephole` | InstCombine | algebraic identities + `x*2^k -> x<<k` | no |
| `cse` | EarlyCSE | block-local common-subexpression elimination | no |
| `simplifycfg` | SimplifyCFG | fold constant branches, drop unreachable blocks, repair phis | no |
| `dce` | ADCE/DCE | delete unused side-effect-free instructions | no |
| `mem2reg` | PromoteMemoryToRegister | promote stack slots to SSA, `phi` at dominance frontiers | produces it |

Order matters: `constfold` manufactures the constant conditions `simplifycfg`
folds on, and each rewrite creates newly-dead work for `dce` — which is why the
`o2` preset runs the sequence twice.

`mem2reg` is **experimental** and excluded from the presets — see its module
docstring for the backend liveness gap it exposes. Third-party passes register via
`asmpython.compiler_pass.CompilerPass(...)` and are selected identically; a
`.py` plugin path may be passed straight to `--passes`.

Passes declare a tiny set of invariant tags so the manager can reject an
impossible ordering:

| Field | Meaning |
|---|---|
| `requires` | must hold before the pass runs (e.g. `{"ssa"}`) |
| `provides` | established by the pass (mem2reg provides `"ssa"`) |
| `preserves` | kept intact; anything else is invalidated |

A pass must not depend on which frontend produced the module. Because a pass
runs *below* the waist, it sees only the neutral vocabulary above — never
Python concepts.

**Any new pass must clear a differential test** (compile the corpus with and
without the pass, diff runtime output). The ordinary test suite does not detect
miscompiles on its own.

## Worked example (a struct, end to end)

A `main` that computes `20 + 22` through a `Point` struct, compiled by the
unmodified backend and run natively, returns exit code `42`:

```python
Point = struct_type([I32, I32], "Point")
entry = IRBlock("entry", [
    IRInstr("alloca", s,   [Point.size_bytes]),
    IRInstr("const",  c20, [20]),
    IRInstr("gep",    f0,  [s, Point.field_offset(0)]),
    IRInstr("store",  None,[c20, f0]),
    IRInstr("const",  c22, [22]),
    IRInstr("gep",    f1,  [s, Point.field_offset(1)]),
    IRInstr("store",  None,[c22, f1]),
    IRInstr("load",   v0,  [f0]),
    IRInstr("load",   v1,  [f1]),
    IRInstr("iadd",   s32, [v0, v1]),
    IRInstr("sext",   s64, [s32]),
    IRInstr("ret",    None,[s64]),
])
IRModule(funcs=[IRFunc("main", [], I64, blocks=[entry])])
```
