"""Differential harness for the JVM backend: compile, run, diff against CPython.

CPython is the oracle rather than the x86-64 backend, because "the two backends
agree" is satisfied by both being wrong the same way. Any case here that the
native backend also fails is a compiler bug, not a JVM one -- run it with
`--native` to tell those apart before blaming this backend.

    python tests/jvm_differential.py              # every case
    python tests/jvm_differential.py lists        # cases matching a substring
    python tests/jvm_differential.py --native     # same cases, x86-64 backend
    python tests/jvm_differential.py --keep       # leave the jars for javap
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CASES: "dict[str, str]" = {}


def case(name: str, source: str) -> None:
    CASES[name] = source.strip() + "\n"


# ---------------------------------------------------------------------------
# scalars and control flow
# ---------------------------------------------------------------------------

case("arith", """
def main():
    print(7 + 3, 7 - 3, 7 * 3, 7 // 3, 7 % 3)
    print(-7 // 2, -7 % 2, 7 // -2, 7 % -2)
    print(2 ** 10, abs(-5), max(3, 9), min(3, 9))
main()
""")

case("floats", """
def main():
    print(1.5 + 2.25, 1.5 * 4.0, 7.0 / 2.0)
    print(2.0, 0.5, -0.25)
    print(1.0 / 3.0)
    print(float(3), int(3.9), round(2.5), round(3.5))
main()
""")

case("comparisons", """
def main():
    print(1 < 2, 2 <= 2, 3 > 4, 4 >= 4, 5 == 5, 5 != 5)
    print(True and False, True or False, not True)
main()
""")

case("recursion", """
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
def main():
    print(fib(0), fib(1), fib(10), fib(20))
main()
""")

case("loops", """
def main():
    total = 0
    for i in range(10):
        total = total + i
    print(total)
    n = 0
    while n < 5:
        n = n + 1
    print(n)
    out = 0
    for i in range(10):
        if i == 3:
            continue
        if i == 7:
            break
        out = out + i
    print(out)
main()
""")

case("range_forms", """
def main():
    print(list(range(5)))
    print(list(range(2, 8)))
    print(list(range(0, 10, 3)))
    print(list(range(5, 0, -1)))
main()
""")

# ---------------------------------------------------------------------------
# strings
# ---------------------------------------------------------------------------

case("str_basics", """
def main():
    s = "Hello, World"
    print(s, len(s))
    print(s.upper(), s.lower())
    print(s[0], s[-1], s[1:5], s[:5], s[7:])
    print(s + "!", "ab" * 3)
main()
""")

case("str_methods", """
def main():
    s = "  padded  "
    print("[" + s.strip() + "]")
    print("a,b,c".split(","))
    print("-".join(["x", "y", "z"]))
    print("Hello".replace("l", "L"))
    print("abc".startswith("ab"), "abc".endswith("bc"))
    print("Hello".find("l"), "hello".count("l"))
main()
""")

case("str_predicates", """
def main():
    print("abc".isalpha(), "123".isdigit(), "a1".isalnum())
    print("".isalpha(), " ".isspace())
    print("abc".isupper(), "ABC".isupper())
main()
""")

case("str_numbers", """
def main():
    print(str(42), str(-17), str(0))
    print(int("42"), int("-17"))
    print(len(str(12345)))
main()
""")

# ---------------------------------------------------------------------------
# lists
# ---------------------------------------------------------------------------

case("list_basics", """
def main():
    xs = [1, 2, 3]
    xs.append(4)
    print(xs, len(xs))
    print(xs[0], xs[-1], xs[1:3])
    total = 0
    for x in xs:
        total = total + x
    print(total)
main()
""")

case("list_growth", """
def main():
    xs = []
    for i in range(100):
        xs.append(i)
    print(len(xs), xs[0], xs[50], xs[99])
    print(sum(xs))
main()
""")

case("list_ops", """
def main():
    xs = [3, 1, 2]
    print(sorted(xs))
    ys = [i * i for i in range(6)]
    print(ys)
    print([x for x in range(10) if x % 2 == 0])
    zs = [1, 2]
    zs.extend([3, 4])
    print(zs)
main()
""")

case("list_nested", """
def main():
    grid = [[1, 2], [3, 4]]
    print(grid)
    print(grid[0][1], grid[1][0])
main()
""")

case("list_strings", """
def main():
    names = ["bravo", "alpha", "charlie"]
    print(names)
    print(sorted(names))
    print(len(names), names[0])
main()
""")

# ---------------------------------------------------------------------------
# dicts
# ---------------------------------------------------------------------------

case("dict_basics", """
def main():
    d = {"a": 1, "b": 2}
    d["c"] = 3
    print(len(d), d["a"], d["c"])
    print("a" in d, "z" in d)
    print(d.get("b", 0), d.get("z", -1))
main()
""")

case("dict_iteration", """
def main():
    d = {"one": 1, "two": 2, "three": 3}
    for k in d:
        print(k, d[k])
    print(len(d))
main()
""")

case("dict_growth", """
def main():
    d = {}
    for i in range(50):
        d[str(i)] = i * 2
    print(len(d), d["0"], d["25"], d["49"])
main()
""")

case("dict_update", """
def main():
    d = {"a": 1}
    d["a"] = 99
    print(d["a"], len(d))
main()
""")

# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------

case("class_basics", """
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
    def norm2(self):
        return self.x * self.x + self.y * self.y
def main():
    p = Point(3, 4)
    print(p.x, p.y, p.norm2())
    p.x = 6
    print(p.x, p.norm2())
main()
""")

case("class_many", """
class Counter:
    def __init__(self):
        self.n = 0
    def bump(self):
        self.n = self.n + 1
        return self.n
def main():
    c = Counter()
    for i in range(5):
        c.bump()
    print(c.n)
    d = Counter()
    print(c.n, d.n)
main()
""")

case("class_inherit", """
class Animal:
    def __init__(self, name):
        self.name = name
    def speak(self):
        return "..."
class Dog(Animal):
    def speak(self):
        return "woof"
def main():
    d = Dog("rex")
    print(d.name, d.speak())
main()
""")

# ---------------------------------------------------------------------------
# exceptions
#
# The JVM has no setjmp, so these are the cases that prove the landing-pad
# translation: a raise crossing a frame, a handler that must NOT catch, and
# finally running on every path.
# ---------------------------------------------------------------------------

case("exc_basic", """
def risky(n):
    if n < 0:
        raise ValueError("negative")
    return n * 2
def main():
    try:
        print(risky(5))
        print(risky(-1))
    except ValueError as e:
        print("caught:", e)
    finally:
        print("done")
main()
""")

case("exc_not_raised", """
def main():
    try:
        print("body")
    except ValueError as e:
        print("never")
    print("after")
main()
""")

case("exc_finally_on_success", """
def main():
    try:
        print("ok")
    finally:
        print("cleanup")
    print("end")
main()
""")

case("exc_nested", """
def main():
    try:
        try:
            raise ValueError("inner")
        except ValueError as e:
            print("inner caught:", e)
        print("between")
        raise ValueError("outer")
    except ValueError as e:
        print("outer caught:", e)
main()
""")

case("exc_across_frames", """
def deep(n):
    if n == 0:
        raise ValueError("bottom")
    return deep(n - 1)
def middle(n):
    return deep(n)
def main():
    try:
        middle(5)
    except ValueError as e:
        print("caught from depth:", e)
    print("survived")
main()
""")

case("exc_handler_not_mine", """
def inner():
    try:
        print("inner try")
    except ValueError as e:
        print("inner handler")
    raise ValueError("after inner try")
def main():
    try:
        inner()
    except ValueError as e:
        print("outer caught:", e)
main()
""")

case("exc_in_loop", """
def main():
    total = 0
    for i in range(5):
        try:
            if i % 2 == 0:
                raise ValueError("even")
            total = total + i
        except ValueError as e:
            total = total + 100
    print(total)
main()
""")

case("exc_index", """
def main():
    xs = [1, 2, 3]
    try:
        print(xs[10])
    except IndexError as e:
        print("index error")
    print("done")
main()
""")

# ---------------------------------------------------------------------------
# mixed
# ---------------------------------------------------------------------------

case("mixed_wordcount", """
def main():
    text = "the quick brown fox jumps over the lazy dog the end"
    counts = {}
    for word in text.split(" "):
        counts[word] = counts.get(word, 0) + 1
    keys = sorted(list(counts.keys()))
    for k in keys:
        print(k, counts[k])
main()
""")

case("mixed_matrix", """
def main():
    size = 4
    rows = []
    for i in range(size):
        row = []
        for j in range(size):
            row.append(i * size + j)
        rows.append(row)
    total = 0
    for row in rows:
        for value in row:
            total = total + value
    print(rows)
    print(total)
main()
""")


def run(source: Path, workdir: Path, native: bool) -> "tuple[bool, str]":
    """Compile and run one case, returning (ok, output-or-error)."""
    suffix = ".exe" if native else ".jar"
    artifact = workdir / (source.stem + suffix)
    command = [sys.executable, "-m", "asmpython", "build", str(source), "-o", str(artifact)]
    if not native:
        command += ["--backend", "jvm"]

    built = subprocess.run(command, capture_output=True, text=True)
    if not artifact.exists():
        detail = (built.stderr or built.stdout).strip().splitlines()
        return False, "COMPILE: " + (detail[-1] if detail else "no artifact produced")

    invoke = [str(artifact)] if native else ["java", "-jar", str(artifact)]
    try:
        ran = subprocess.run(invoke, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "RUN: timed out"
    if ran.returncode != 0:
        detail = (_clean(ran.stderr) or _clean(ran.stdout)).strip().splitlines()
        return False, "RUN: " + (detail[0] if detail else f"exit {ran.returncode}")
    return True, _clean(ran.stdout)


def _clean(text: str) -> str:
    # The JVM announces JAVA_TOOL_OPTIONS on both streams when it is set. That
    # is not the program's output; leaving it in makes every case differ, and
    # leaving it in stderr hides the actual exception behind it.
    lines = [ln for ln in text.splitlines() if not ln.startswith("Picked up JAVA_TOOL_OPTIONS")]
    return "\n".join(lines).rstrip()


def main(argv: "list[str]") -> int:
    native = "--native" in argv
    keep = "--keep" in argv
    filters = [a for a in argv if not a.startswith("--")]

    selected = {n: s for n, s in CASES.items() if not filters or any(f in n for f in filters)}
    if not selected:
        print(f"no cases match {filters}")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="jvmdiff-"))
    label = "x86-64" if native else "jvm"
    passed, failed = [], []

    try:
        for name, source in selected.items():
            path = workdir / f"{name}.py"
            path.write_text(source, encoding="utf-8")

            expected = _clean(subprocess.run(
                [sys.executable, str(path)], capture_output=True, text=True).stdout)
            ok, actual = run(path, workdir, native)

            if ok and actual == expected:
                passed.append(name)
                print(f"  ok    {name}")
            else:
                failed.append((name, expected, actual))
                print(f"  FAIL  {name}")
    finally:
        if keep:
            print(f"\nartifacts: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{label}: {len(passed)}/{len(selected)} match CPython")
    for name, expected, actual in failed:
        print(f"\n--- {name}")
        print("  expected:", expected.replace("\n", "\n            ") or "(nothing)")
        print("  actual:  ", actual.replace("\n", "\n            ") or "(nothing)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
