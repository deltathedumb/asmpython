# asmpython Error Codes

Every diagnostic emitted by the asmpython compiler carries a short code in
brackets that identifies the exact error category.  For example:

```
hello.py:5:3: semantic error: [E002] undefined function 'greet'
  greet("world")
  ^
```

To look up what a code means, pass it to `--explain`:

```sh
asmpython --explain E002
# [E002] Call to an undefined function.  The function may not be imported …
```

---

## Code scheme

| Prefix | Phase | Range |
|--------|-------|-------|
| `L` | Lexer | L001 – L099 |
| `P` | Parser | P001 – P099 |
| `E` | Semantic analyser | E001 – E099 |

---

## Lex errors (L)

These are detected while the source file is being tokenised, before any
parsing happens.

| Code | Short name | When it fires |
|------|------------|---------------|
| L001 | Inconsistent indentation | The indentation level on this line does not match any enclosing block. |
| L002 | Unexpected character | The source contains a character that cannot start any valid token. |
| L003 | Unterminated string literal | The closing quote (`"` or `'`) is missing before end-of-line or end-of-file. |
| L004 | Newline in string literal | A single-quoted string spans more than one physical line.  Use triple quotes for multi-line strings. |
| L005 | Unterminated f-string | The closing quote of an f-string is missing. |
| L006 | Newline in f-string | An f-string spans more than one physical line.  F-strings must fit on one line. |
| L007 | Invalid float literal | The text does not represent a valid floating-point number. |
| L008 | Invalid escape sequence | The string contains a `\X` escape that is not recognised. |
| L009 | Invalid integer literal | Malformed hex (`0x`), octal (`0o`), or binary (`0b`) integer prefix. |
| L010 | Unterminated raw string | The closing quote of a raw string (`r"…"`) is missing. |

### Example — L001

```python
def f():
    x = 1
      y = 2   # <-- extra spaces don't match outer block
```

```
hello.py:3:1: lex error: [L001] inconsistent indentation
      y = 2
^
```

---

## Parse errors (P)

These are detected while the token stream is being structured into an AST.

| Code | Short name | When it fires |
|------|------------|---------------|
| P001 | Unexpected token | The parser encountered a token that does not fit any valid rule at this position. |
| P002 | Expected token | A specific keyword, operator, or punctuation was required but something else was found. |
| P003 | Invalid assignment target | The left-hand side of `=` is not a name, subscript, or attribute path. |
| P004 | Invalid decorator | The decorator expression uses unsupported syntax. |
| P005 | Invalid pattern | A `match`/`case` pattern uses a construct that is not supported. |
| P006 | Missing module name | `from import ...` with no module name between the two keywords. |
| P007 | Invalid default argument | Only literals and simple names are supported as default parameter values. |
| P008 | Invalid annotation | The type annotation uses syntax the compiler cannot handle. |
| P009 | Multi-line parenthesised import | `from X import (\n  a, b\n)` — use separate `from X import Y` lines instead. |

### Example — P003

```python
1 + 2 = x   # cannot assign to an expression
```

```
hello.py:1:7: parse error: [P003] cannot assign to this expression
1 + 2 = x
      ^
```

---

## Semantic errors (E)

These are detected during semantic analysis: after the AST is built, before
code generation.  The analyser resolves names, infers types, and enforces the
constraints of the asmpython type system.

### Name resolution (E001 – E005)

| Code | Short name | When it fires |
|------|------------|---------------|
| E001 | Undefined name | A bare name is used but was never defined or imported in the current scope. |
| E002 | Undefined function | A call `foo(…)` where `foo` has not been defined anywhere reachable. |
| E003 | Function redefined | Two `def` statements with the same name appear at the same scope level. |
| E004 | Class redefined | Two `class` statements with the same name appear at the same scope level. |
| E005 | No such module | `import X` or `from X import Y` where `X` is not a known stdlib or project module. |

#### Example — E001

```python
print(message)   # 'message' was never defined
```

```
hello.py:1:7: semantic error: [E001] undefined variable 'message'
print(message)
      ^
```

### Type errors (E011 – E019)

| Code | Short name | When it fires |
|------|------------|---------------|
| E011 | Type mismatch | An expression's type is incompatible with what is expected. |
| E012 | Binary op type | A binary operator (`+`, `*`, `<`, …) is used between operands of incompatible types. |
| E013 | Unary op type | A unary operator is applied to an incompatible type. |
| E014 | F-string segment type | A slot inside `f"…{expr}…"` has a type that cannot be converted to a string (`type`, `module`, …). |
| E015 | Return type mismatch | The value returned by a function does not match its declared `-> T` return type. |
| E016 | Index type | The index value has the wrong type (e.g. a `str` where an `int` is required). |
| E017 | Index object type | Attempting to subscript a value that does not support indexing. |
| E018 | Iter type | Iterating (`for x in …`) over a value that is not a list, tuple, or other iterable. |
| E019 | Assignment type | The right-hand side of `=` is not compatible with the target's declared type. |

#### Example — E012

```python
x: int = 1
y: str = "a"
z = x + y      # int + str is not supported
```

```
hello.py:3:5: semantic error: [E012] '+' not supported between int and str
z = x + y
    ^
```

#### Example — E014

```python
class Color:
    pass

c = Color()
print(f"color={c.__class__}")   # __class__ returns a 'type' object, not a scalar
```

```
hello.py:5:8: semantic error: [E014] f-string segment cannot be a type
print(f"color={c.__class__}")
       ^
```

> **Fix:** convert the value to a string first: `f"color={str(c)}"`.

### Call errors (E021 – E027)

| Code | Short name | When it fires |
|------|------------|---------------|
| E021 | Wrong arg count | The number of positional arguments passed does not match the function signature. |
| E022 | Wrong arg type | An argument's type is not compatible with the parameter type. |
| E023 | Varargs unpack | `func(*args)` where `args` is a `*args` vararg parameter — the element types are not known at compile time. |
| E024 | Not callable | A value that is not a function or class is called like one. |
| E025 | Format arg count | `"… %s …" % (a, b)` — the number of `%` conversions does not match the tuple length. |
| E026 | Format arg type | A `%d` / `%f` conversion receives an argument of the wrong type. |
| E027 | Format literal | The left-hand side of `%` is not a string literal — dynamic format strings are not supported. |

#### Example — E021

```python
def add(a: int, b: int) -> int:
    return a + b

add(1, 2, 3)   # takes 2 args, got 3
```

```
hello.py:4:1: semantic error: [E021] add() takes 2 argument(s), got 3
add(1, 2, 3)
^
```

### Control flow (E031 – E033)

| Code | Short name | When it fires |
|------|------------|---------------|
| E031 | Break outside loop | `break` appears outside any `while` or `for` loop. |
| E032 | Continue outside loop | `continue` appears outside any `while` or `for` loop. |
| E033 | Return outside function | `return` appears at module top-level or inside a class body. |

### Class / attribute (E041 – E045)

| Code | Short name | When it fires |
|------|------------|---------------|
| E041 | No attribute | `obj.x` where the type of `obj` has no field or method named `x`. |
| E042 | Not an exception | A `raise` expression is not an exception class or instance. |
| E043 | Index assignment | `obj[k] = v` where the type of `obj` does not define `__setitem__`. |
| E044 | super() outside class | `super()` used in a function that is not a method. |
| E045 | @staticmethod outside class | `@staticmethod` applied to a top-level function. |

### Collection / structural (E051 – E055)

| Code | Short name | When it fires |
|------|------------|---------------|
| E051 | Heterogeneous list | A list literal contains elements of two or more different types.  asmpython lists must be homogeneous. |
| E052 | Heterogeneous tuple (in) | `x in (a, b)` where the tuple elements have mixed types. |
| E053 | Unpack count mismatch | `a, b = expr` where `expr` does not have exactly two elements. |
| E054 | Dict key type | A dict operation requires a `str` key but received something else. |
| E055 | Set element type | Set elements must be strings in asmpython v1. |

#### Example — E051

```python
items: list = [1, "two", 3]   # int and str mixed
```

```
hello.py:1:10: semantic error: [E051] list elements must all have the same type
items: list = [1, "two", 3]
              ^
```

### Assembly directives (E061 – E063)

| Code | Short name | When it fires |
|------|------------|---------------|
| E061 | Invalid asm operand | An operand string passed to an `Assembly.*` method is empty or malformed. |
| E062 | Unrecognised register | An operand looks like a register name but is not a valid x86-64 register. |
| E063 | include() argument | `include()` requires a string literal package name as its only argument. |

### Miscellaneous (E071 – E073)

| Code | Short name | When it fires |
|------|------------|---------------|
| E071 | zip() args type | `zip()` arguments must be lists or tuples. |
| E072 | enumerate() arg type | `enumerate()` argument must be a list. |
| E073 | Match pattern | An unsupported `match`/`case` pattern construct was encountered. |

---

## Using error codes in editor integration

When the compiler is invoked with `--check --json`, the JSON diagnostic
object includes a `"code"` field:

```json
[{"phase": "semantic", "message": "undefined function 'greet'", "line": 5, "col": 3, "code": "E002"}]
```

A clean file produces `[]`.

The VS Code extension surfaces these codes as hover text and displays the
`--explain` description inline.

---

## Looking up a code from the command line

```sh
asmpython --explain E031
# [E031] 'break' used outside a loop.

asmpython --explain L003
# [L003] Unterminated string literal: the closing quote is missing …

asmpython --explain P002
# [P002] Expected a specific token (keyword, operator, or punctuation) but found something else.
```

Exit code is `0` if the code is known, `1` if it is not.
