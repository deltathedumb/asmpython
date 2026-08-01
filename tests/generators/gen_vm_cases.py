"""Generate value-model probe cases for asmpython Phase 0.

FAILURE_AUDIT.md attributes ~84 of the known failures to one assumption, stated
in _compiler/codegen.py's own design notes: "All values are 64-bit ints."
Floats, strings, objects, bools, bytes and bigints are all encoded INTO that
word rather than modelled, so a value whose static type is lost reads back as
a raw integer -- often a heap address.

Those 84 failures are spread across cases that each test something else, so
none of them isolates the representation question. These probes do: each one
exercises exactly one property of the value model and nothing more.

Most are EXPECTED TO FAIL today. That is the point -- they are the acceptance
criteria for a corrected value model, not regression guards. Progress on a core
rebuild is measurable as these flipping to pass.

Usage: python gen_vm_cases.py <out dir>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CASES: dict[str, tuple[str, str]] = {}


def case(name: str, probes: str, src: str) -> None:
    CASES[name] = (probes, src.strip() + "\n")


# -- identity of the value, preserved across a dynamic boundary --------------

case("vm_str_through_any", "str survives an opaque round trip", '''
def passthrough(v):
    return v


s = passthrough("hello")
print(s)
print(len(s))
print(s.upper())
''')

case("vm_float_through_any", "float survives an opaque round trip", '''
def passthrough(v):
    return v


f = passthrough(1.5)
print(f)
print(f + 0.25)
''')

case("vm_bool_is_not_int", "bool prints as True/False, not 1/0", '''
def passthrough(v):
    return v


print(True)
print(False)
print(passthrough(True))
b = 1 == 1
print(b)
print([True, False])
''')

case("vm_none_is_not_zero", "None is distinguishable from 0", '''
def maybe(flag):
    if flag:
        return 0
    return None


print(maybe(True))
print(maybe(False))
print(maybe(True) is None)
print(maybe(False) is None)
''')

case("vm_container_heterogeneous", "a list may hold mixed kinds", '''
items = [1, "two", 3.0, True, None]
for v in items:
    print(v)
''')

case("vm_dict_mixed_values", "dict values keep their own kinds", '''
d = {"i": 1, "s": "text", "f": 2.5, "b": False}
print(d["i"])
print(d["s"])
print(d["f"])
print(d["b"])
''')

case("vm_tuple_through_any", "tuple survives an opaque round trip", '''
def passthrough(v):
    return v


t = passthrough((1, "a"))
print(t[0])
print(t[1])
print(len(t))
''')

# -- numeric tower -----------------------------------------------------------

case("vm_int_is_arbitrary_precision", "int does not wrap at 64 bits", '''
big = 2 ** 70
print(big)
print(big + 1)
print(2 ** 64)
print(9223372036854775807 + 1)
''')

case("vm_round_returns_int", "round(x) is int; round(x, n) is float", '''
print(round(2.6))
print(round(2.4))
print(round(2.55, 1))
print(round(3.14159, 2))
r = round(2.6)
print(r + 1)
''')

case("vm_float_repr", "float formatting matches CPython", '''
print(0.1 + 0.2)
print(1.0)
print(1e20)
print(1e-7)
print(3.0 / 2.0)
print(1.0 / 3.0)
''')

case("vm_int_float_division", "/ is true division, // is floor", '''
print(7 / 2)
print(7 // 2)
print(-7 // 2)
print(7 % 3)
print(-7 % 3)
''')

case("vm_float_int_mixing", "int and float mix without losing kind", '''
x = 1
y = 2.5
print(x + y)
print(x * y)
print(float(x))
print(int(y))
''')

# -- byte strings ------------------------------------------------------------

case("vm_bytes_literal", "bytes is a distinct type", '''
b = b"abc"
print(len(b))
print(b[0])
print(b)
''')

case("vm_bytearray_mutable", "bytearray is mutable", '''
ba = bytearray(b"abc")
ba[0] = 122
print(len(ba))
print(ba[0])
print(bytes(ba))
''')

# -- runtime type discrimination --------------------------------------------

case("vm_isinstance_discriminates", "isinstance sees the real runtime kind", '''
def kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return "other"


print(kind(1))
print(kind(1.5))
print(kind("s"))
print(kind(True))
print(kind([1]))
''')

case("vm_type_name_of_value", "type(v).__name__ reports the real kind", '''
print(type(1).__name__)
print(type("s").__name__)
print(type(1.5).__name__)
print(type(True).__name__)
print(type([1]).__name__)
''')

# -- equality and identity of boxed values ----------------------------------

case("vm_str_equality_by_value", "strings compare by value, not address", '''
a = "hello"
b = "hel" + "lo"
print(a == b)
print(a != b)
print(a == "other")
''')

case("vm_value_in_container", "membership uses value equality", '''
names = ["ada", "bob"]
print("ada" in names)
print("carol" in names)
d = {"k": 1}
print("k" in d)
print("z" in d)
''')

# -- the exact shape that leaks a pointer today ------------------------------

case("vm_str_field_via_helper", "a str field read through a helper stays a str", '''
class Leaf:
    def __init__(self, tag):
        self.tag = tag


def newest(seq):
    return seq[len(seq) - 1]


leaves = [Leaf("a"), Leaf("b")]
found = newest(leaves)
print(found.tag)
print("leaf:" + found.tag)
''')

case("vm_str_from_unannotated_return", "an unannotated str return stays a str", '''
def label(n):
    if n > 0:
        return "positive"
    return "non-positive"


v = label(1)
print(v)
print(v.upper())
print(len(v))
''')

case("vm_callable_param_result", "a called-through parameter keeps its result kind", '''
def apply(fn, arg):
    return fn(arg)


def shout(s):
    return s.upper()


def double(n):
    return n * 2


print(apply(shout, "hi"))
print(apply(double, 21))
''')

case("vm_str_in_class_attr_roundtrip", "str stored and re-read from a field", '''
class Box:
    def __init__(self):
        self.slot = ""

    def put(self, v):
        self.slot = v
        return self

    def get(self):
        return self.slot


b = Box().put("stored")
print(b.get())
print(b.get() + "!")
print(len(b.get()))
''')

# -- exception values --------------------------------------------------------

case("vm_exception_str", "an exception carries its message", '''
try:
    raise ValueError("boom")
except ValueError as e:
    print(str(e))
    print(len(str(e)))
''')

case("vm_finally_runs_on_return", "finally executes on the return path", '''
def f():
    try:
        return "from-try"
    finally:
        print("finally-ran")


print(f())
''')

# `finally` runs on EVERY exit path, not just return -- break and continue
# leave a try body too. These are separate probes because the lowering
# unwinds them through a different mechanism (the loop stack, not the
# return path), so fixing one does not fix the other.
case("vm_finally_runs_on_break", "finally executes on the break path", '''
def f():
    for i in [1, 2, 3]:
        try:
            if i == 2:
                break
            print("body", i)
        finally:
            print("finally", i)
    return "done"


print(f())
''')

case("vm_finally_runs_on_continue", "finally executes on the continue path", '''
def f():
    total = 0
    for i in [1, 2, 3]:
        try:
            if i == 2:
                continue
            total = total + i
        finally:
            print("finally", i)
    return total


print(f())
''')

# -- string formatting of non-int values ------------------------------------

case("vm_fstring_kinds", "f-strings render each kind correctly", '''
i = 7
s = "text"
f = 2.5
b = True
print(f"{i}")
print(f"{s}")
print(f"{f}")
print(f"{b}")
print(f"{i} {s} {f} {b}")
''')

case("vm_str_of_each_kind", "str() renders each kind correctly", '''
print(str(7))
print(str("text"))
print(str(2.5))
print(str(True))
print(str(None))
print(str([1, 2]))
''')


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: gen_vm_cases.py <out dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = []
    for name, (probes, src) in sorted(CASES.items()):
        tmp = out_dir.parent / f"_gen_{name}.py"
        tmp.write_text(src, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(tmp)], capture_output=True, text=True, timeout=30
            )
        finally:
            tmp.unlink(missing_ok=True)
        if proc.returncode != 0:
            skipped.append((name, (proc.stderr.strip().splitlines() or ["?"])[-1]))
            continue
        lines = proc.stdout.splitlines()
        # `probes:` must precede `# expect:` -- see gen_compat_cases.py.
        header = [f"# probes: {probes}", "# expect:"]
        header += [f"# {ln}" if ln else "#" for ln in lines]
        (out_dir / f"{name}.py").write_text(
            "\n".join(header) + "\n" + src, encoding="utf-8"
        )
        written += 1

    print(f"wrote {written} value-model probes to {out_dir}")
    for name, err in skipped:
        print(f"  SKIPPED {name}: {err}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
