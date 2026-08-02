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

## A name must be assigned on every path

```python
if flag > 0:
    later: int = 42
print(later)        # error[E0032]: may be used before it is assigned
```

Python raises `UnboundLocalError` for this at runtime; saying it at compile
time is strictly better, and it is the same rule. A branch that cannot fall
through does not dilute it, so this is fine:

```python
if c:
    x: int = 1
else:
    return 0
print(x)            # every path here assigned x
```

A loop body may run zero times, so neither the loop variable nor anything the
body assigns counts as assigned afterwards — `for i in range(0): pass` then
`print(i)` is an error here and an `UnboundLocalError` in Python.

## Expressions

Arithmetic, comparison and bitwise operators, `not`/`and`/`or`, conditional
expressions, calls, `int()`/`float()`/`bool()`.

`print` takes any number of arguments, separates them with a space and ends
with a newline, as Python does — `print()` is an empty line.

Augmented assignment (`+=`, `//=`, `**=`, `<<=`, …) is checked as the
operation it stands for, so every rule below applies to it too.

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

## Where static typing shows

Every expression has ONE type. Python's `and`, `or` and `x if c else y`
return whichever operand they picked, and those operands may have different
types; here the expression's type is their unification, so the value is
converted:

```python
0 and 2.5           # Python: 0        here: 0.0
1 if c else 2.5     # Python: 1        here: 1.0
True or 2           # Python: True     here: 1
```

The values are equal — `0 == 0.0` and `True == 1` — and only the type, and so
the printed form, differs. This is not a rough edge to be filed off: it is
what "every expression has one static type" means, and the alternative is
carrying a tag at runtime, which is the thing this compiler exists not to do.

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

The full set the frontend can emit:

| code | what it means |
| --- | --- |
| `E0001` | something other than a function at module level |
| `E0002` | a function defined twice |
| `E0003` | no `main` |
| `E0004` | a duplicate parameter name |
| `E0005` | `*args`, `**kwargs`, or keyword-only/positional-only parameters |
| `E0006` | a default argument |
| `E0007` | a decorator |
| `E0008` | `main` takes no parameters |
| `E0009` | `main` must return `int` |
| `E0010` | a missing type annotation |
| `E0011` | a type this frontend does not have |
| `E0020` | an assignment other than `name = value` |
| `E0021` | a loop target that is not a plain name |
| `E0022` | an unsupported statement |
| `E0023` | a `for` over something other than `range(...)` |
| `E0024` | `range()` with the wrong number of arguments |
| `E0025` | `for ... else` |
| `E0026` | `while ... else` |
| `E0027` | `break` or `continue` outside a loop |
| `E0028` | a `range()` step that is not a literal |
| `E0029` | a `range()` step of zero |
| `E0030` | a name redeclared with another type |
| `E0031` | an undefined name |
| `E0032` | a name used before it is assigned on every path |
| `E0040` | an unsupported expression |
| `E0041` | an operator applied to non-numbers |
| `E0042` | a bitwise operator applied to a float |
| `E0043` | `**` without a literal exponent |
| `E0044` | `**` with a negative exponent |
| `E0045` | an operator this frontend does not lower |
| `E0050` | a call to something other than a name |
| `E0051` | a keyword argument |
| `E0052` | a call to an unknown function |
| `E0053` | a call with the wrong number of arguments |
| `E0054` | a conversion with the wrong number of arguments |
| `E0055` | a conversion of something not numeric |
| `E0060` | a type mismatch |

Each applies wherever the construct appears, including inside an augmented
assignment -- `x **= n` reports `E0043` exactly as `x = x ** n` does.
