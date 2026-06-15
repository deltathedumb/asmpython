# Resume — parity-expansion autonomous loop

**Branch:** `parity-expansion`  
**Standing directives:** push after every commit, breadth over depth, full implementations (no stubs), never use `-> "ClassName"` quoted forward-ref annotations.

---

## What was just completed this session

### 1. `for x in custom_object` — `__iter__`/`__next__` protocol (DONE, pushed)
- **Sema** (`sema.py`): detects `instance:ClassName` on For iter; verifies class has `__iter__` and `__next__`; stamps `iter_is_instance = cls_name` on the For node.
- **Codegen** (`codegen.py`): allocates jmpbuf slots in `_collect_locals`; new `_gen_for_iter()` method calls `__iter__`, then loops calling `__next__` with setjmp protection, jumps to end on StopIteration (type 21), re-raises on anything else.
- **Bug fixed this session:** instance pointer was going into `rax` before method calls instead of `_arg_reg(0)` (rcx/rdi). Fixed both the `__iter__` and `__next__` call sites.
- **Test:** `tests/cases/366_custom_iter.py` — passes.

### 2. `x in obj` / `x not in obj` via `__contains__` (DONE, pushed)
- **Sema** (`sema.py` line ~3235): when rhs is `instance:ClassName`, checks for `__contains__`; stamps `dunder_contains_owner` and `dunder_contains_negate` on the Compare node; raises `SemaError` if no `__contains__` defined.
- **Codegen** (`codegen.py`): `_collect_locals` allocates `__contains_needle_<id>` slot; `_gen_compare` dispatches to `__contains__(container, needle)` via platform ABI.
- **Tests:** `tests/cases/367_custom_contains.py` (passes), `tests/cases_fail/for_unpack_non_contains_instance.py` (passes).

---

## In progress at pause point

### `@classmethod` with `cls.field` access — SEGFAULTS (not yet fixed)

**The bug:** When a `@classmethod` is called, codegen passes `null` (0) as `cls` (codegen.py line ~8216):
```asm
xor rcx, rcx   ; cls = null
call Counter__get_total
```
Inside the classmethod, `cls.total` tries to dereference `rcx` (0) → segfault.

**What we know:**
- Class-level variables are stored as static globals with labels like `_cv_Counter.total` in `self.class_var_labels` (codegen.py line ~223).
- Reading `ClassName.total` directly already works — `_gen_attr` checks `class_var_labels` and emits `mov rax, [rel _cv_Counter.total]`.
- The problem is `cls.total` inside a classmethod body — `cls` is treated as an instance dict pointer, so `_gen_attr` falls through to instance dict access on a null pointer.

**Proposed fix (NOT yet implemented):**
In sema, when inside a `@classmethod` body, track `cls_name` (the enclosing class). When sema sees `cls.fieldname` (where `cls` is the first param of a classmethod), resolve it to the class variable `ClassName.fieldname` — rewrite the AST node at sema time to `A.Attr(obj=A.Name(cls_name), name=fieldname)`, so codegen sees `ClassName.fieldname` and hits the existing `class_var_labels` path.

The same rewrite must apply to `cls.field = value` (AttrAssign), so write also hits the static storage path.

**Test to write after fix:**
```python
# tests/cases/368_classmethod_cls_field.py
# expect:
# 0
# 3

class Counter:
    total: int = 0

    @classmethod
    def increment(cls) -> None:
        cls.total = cls.total + 1

    @classmethod
    def get_total(cls) -> int:
        return cls.total

print(Counter.get_total())
Counter.increment()
Counter.increment()
Counter.increment()
print(Counter.get_total())
```

---

## Planned next features (in rough priority order)

### A. Fix `@classmethod` `cls.field` (immediate next)
As described above.

### B. `__bool__` — instance truthiness
`if obj:` / `while obj:` on user instances currently just checks pointer != 0 (always true). Should dispatch to `__bool__` or fall back to `__len__`. Needs:
- Sema: detect `if instance_typed_expr:` and stamp `dunder_bool_owner` if class has `__bool__` or `__len__`
- Codegen: before the branch, call `__bool__()` or `__len__()`, test rax

### C. `try/except/else` — the `else` block
Currently `try/except/else` likely silently drops the `else` block. Should run when no exception was raised. Needs a flag local + check after the setjmp block exits normally.

### D. `**kwargs` capture in function definitions
`def f(**kwargs):` — collect keyword arguments into a dict. Currently only named kwargs at call sites are matched against known params. Full `**kwargs` capture for variadic keyword args is missing.

### E. Stdlib gaps to fill (breadth over depth)
- `io.py` — ~35/61 implemented
- `contextlib.py` — ~13/21 implemented
- `inspect.py` — ~16/35 implemented
- `struct.py` — 7/16 real
- `fractions.py` — ~14/38 implemented

### F. Other protocol methods
- **`__hash__`** — user classes as dict keys
- **`__len__` in bool context** — `if mylist:` when mylist is user class

---

## Recent commits (this session)
```
f75d19ae  Add __contains__ dispatch for 'x in obj' on user-class instances
19f2e77e  Add for-x-in-obj iteration via __iter__/__next__ protocol
```

## Key file locations
- `asmpython/_compiler/sema.py` — type checker / semantic analysis
- `asmpython/_compiler/codegen.py` — code generation (~11,000 lines)
- `asmpython/_compiler/ast_nodes.py` — AST node definitions
- `asmpython/_compiler/target_windows.py` — Windows-specific codegen overrides
- `asmpython/stdlib/__init__.py` — STDLIB_BINDINGS registry
- `tests/cases/` — positive test cases (numbered)
- `tests/cases_fail/` — negative/error test cases
- `tests/runner.py` — test runner (`python -m tests.runner`)

## How to run tests
```
cd C:\Users\Harvey Jass\Downloads\asmpython
python -m tests.runner
```
All 367 tests currently pass.
