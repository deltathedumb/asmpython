# compyle

Native Python -> x86-64 assembly transpiler. Emits NASM, assembles, and links into a native executable on Windows (PE64) or Linux (ELF64).

## Supported Python subset

- **Numbers**: 64-bit signed integers; 64-bit IEEE-754 floats; hex/bin/octal int literals; `_` separators; `1.5e-3` style floats
- **Strings**: `"..."` and `'...'` literals; nul-terminated; immutable
- **F-strings**: `f"x = {x}, n = {n+1}"` — supported as arguments to `print()`
- **Lists**: `[1, 2, 3]`, `xs[i]`, `xs[i] = x`, `xs.append(x)`, `xs.pop()`, `len(xs)`, `for x in xs:`
- **Arithmetic**: `+ - * / // % == != < <= > >= & | ^ ~ << >>`
- **Numeric promotion**: mixed int/float ops produce float; `/` is true division (Python semantics)
- **Chained comparisons**: `0 < x < 10`
- **Control flow**: `if / elif / else`, `while`, `for x in range(...)`, `for x in <list>`, `break`, `continue`, `pass`
- **Functions**: `def name(a, b)` with `return`; recursion supported
- **Builtins**: `print(...)`, `len(s)`, `int(x)`, `float(x)`, `str(x)`, `input(prompt)`
- **Literals**: `True` (1), `False` (0), `None` (0)
- **Augmented assignment**: `+= -= *= /= //= %= &= |= ^= <<= >>=`

## Diagnostics

Errors point at the offending source line with a caret:

```text
examples/broken.py:2:7: semantic error: undefined variable 'oops'
  print(oops)
        ^
```

## Usage

```sh
python -m compyle hello.py                       # auto-detect host target
python -m compyle hello.py --target linux        # cross-target Linux
python -m compyle hello.py --target windows -o hello.exe
python -m compyle hello.py --emit-asm            # stop after .asm
python -m compyle hello.py --keep                # keep intermediate .o/.obj
```

## Toolchain requirements

- `nasm` on PATH (assembler)
- `gcc` on PATH (linker driver; pulls in the C runtime)

The generated assembly links against libc / msvcrt for low-level primitives (printf, malloc, strlen, atoll, fgets, sprintf, fmod, atof). This is intentional: it lets us build on standard ABIs and reuse work already done in those runtimes.

## Tests

```sh
python -m tests.runner
```

Tests live in `tests/cases/*.py` (positive cases) and `tests/cases_fail/*.py` (cases that must fail compilation). Each file starts with an `# expect:` block (or `# expect-error:` for failures). Inputs to `input()` come from a `# stdin:` block.

## Architecture

- `compyle/lexer.py` — indent-aware tokenizer
- `compyle/parser.py` — recursive-descent parser producing an AST
- `compyle/ast_nodes.py` — dataclass AST + static `expr_type` resolver
- `compyle/sema.py` — semantic analysis (name resolution, arity, type sanity)
- `compyle/codegen.py` — target-agnostic codegen (statements, expressions, list ABI, float arithmetic)
- `compyle/target_linux.py` — Linux ELF64 / System V AMD64 ABI specifics + libc runtime
- `compyle/target_windows.py` — Windows PE64 / MS x64 ABI specifics + msvcrt runtime
- `compyle/driver.py` — invokes NASM and the linker
- `compyle/__main__.py` — CLI entry point
