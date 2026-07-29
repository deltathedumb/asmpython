# APC syntax highlighting

Syntax highlighting for **APC** (ASMPython C) -- ASMPython's low-level systems
language. Applies to `.apc` files.

This extension is grammar-only: it contributes a language id, a TextMate
grammar, and bracket/comment configuration. It runs no code and has no build
step. Diagnostics, build, and run support for `.py` sources live in the
sibling [`asmpython`](../asmpython) extension.

## What it highlights

| Construct | Example |
|---|---|
| Declarations | `func(u32) crc(...)`, `name Checksum { }`, `view Header { }` |
| Parameterized types | `ptr(u8)`, `int(32)`, `rel(Header)`, `slice(u8)`, `Array(Record)` |
| Explicit layout offsets | `len (i32) @4`, `mode (u3) @0.2` |
| Namespace / member access | `Console::print`, `items::element(0)` |
| Bindings | `const`, `let` |
| Control flow | `if (...)`, `for (i = 0..8)`, `while`, `ret` |
| Numbers | `42`, `0xEDB88320`, `0b1011`, `3.14` |
| Comments | `# line comment` |

## Installing locally

```
code --install-extension apc-syntax
```

or copy this directory into `~/.vscode/extensions/` and reload the window.

## Packaging

```
npx @vscode/vsce package
```
