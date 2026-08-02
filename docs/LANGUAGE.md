# The Python subset

`frontends/python` compiles a statically typed subset of Python. Programs that
compile run the same under CPython — that is the test suite's actual
definition, and CPython is the oracle for every one of them.

The subset is small on purpose. It is a frontend for a language-neutral IR,
not a Python implementation, and everything it accepts it accepts with
Python's semantics rather than the machine's.

## Types

`int` (64-bit), `float` (double), `bool`, `None`. Every parameter, return and
declaration is annotated.

```python
def area(w: float, h: float) -> float:
    return w * h

def main() -> int:
    print(area(3.0, 4.0))
    return 0
```

`bool` widens to `int` and `int` to `float`, as Python's numeric tower does.
**Nothing narrows implicitly** — losing precision silently is how a program
computes the wrong answer without ever failing:

```python
n: int = 1.5        # error[E0060]: narrowing is never implicit
n: int = int(1.5)   # fine
```

## Where Python and the machine disagree

These are the cases the frontend pays for so no backend has to. Each is
lowered to several instructions, once, here.

| | Python | the machine | 
|---|---|---|
| `//` | floors toward −∞ | truncates toward zero |
| `%` | takes the divisor's sign | takes the dividend's |
| `and`/`or` | yield an **operand**, and short-circuit | — |
| `a < b < c` | evaluates `b` once | — |

```python
-7 // 2    # -4, not -3
-7 % 2     #  1, not -1
-7.5 % 2.0 #  0.5, not -1.5
7.5 // 2.0 #  3.0, not 3.75
```

## Statements

`if`/`elif`/`else`, `while`, `for ... in range(...)`, `break`, `continue`,
`return`, `pass`, assignment, annotated assignment, augmented assignment.

`range` takes 1–3 arguments. **A three-argument `range` needs a literal step**,
because the step's sign decides whether the loop test is `<` or `>`:

```python
for i in range(5, 0, -1):   # fine
for i in range(5, 0, s):    # error[E0028]: step must be a literal
```

Accepting a runtime step would mean picking one comparison and being silently
wrong about the other — every descending loop running zero times and reporting
success.

## Expressions

Arithmetic, comparison and bitwise operators, `not`/`and`/`or`, conditional
expressions, calls, `int()`/`float()`/`bool()`.

`**` needs a **non-negative integer literal** exponent:

```python
x ** 8      # three multiplications, expanded at compile time
x ** n      # error[E0043]: needs a literal integer exponent
x ** -1     # error[E0044]: negative exponents are float-valued
```

Python's `**` is not one operation — `2 ** 10` is an int, `2 ** -1` is the
float `0.5`. A statically typed expression cannot be both, and a runtime
exponent would force a guess.

`int()`, `float()` and `bool()` are conversions, not calls — they lower to a
coercion or to nothing. `bool(x)` is `x != 0`; a narrowing cast would make
`bool(2)` false. A program that defines its own function with one of these
names shadows the conversion.

## Not in the subset

Classes, containers, strings, comprehensions, generators, exceptions,
imports, closures, nested functions, decorators, default and keyword
arguments, `*args`/`**kwargs`, `global`/`nonlocal`, `with`, `match`, `assert`,
`del`, `lambda`, the walrus operator, `@`.

Each produces a diagnostic with a code. **The compiler never raises on Python
it does not support** — a result or a diagnostic, never a traceback. That is
enforced by a test that feeds 42 unsupported constructs at the whole pipeline.

## One deliberate divergence

`print` of a float uses C's `%f`, not Python's `repr`:

```text
asmpython   32.000000
CPython     32.0
```

The frontend's `print` is a call into a small C runtime, and matching Python's
shortest-round-trip float repr is a real algorithm rather than a format
string. What matters is that the reference interpreter and every compiled
binary agree — a reference implementation that disagrees with the thing it is
a reference for is worse than none — so `Interpreter._host` formats the same
way, and the oracle comparison formats CPython's value the same way rather
than pretending the difference is not there.

## Diagnostics

Every error has a code, a position, and usually a suggestion:

```
error[E0044]: `**` with a negative exponent is float-valued
 --> prog.py:2:17
  |
2 |     return 2 ** -1
  |                 ^^ -1
  |
  = help: write `1.0 / (2 ** 1)`
```

One unknown name produces one diagnostic, not one per use: an unresolved type
poisons everything derived from it and every operation on a poisoned type is
silent.
