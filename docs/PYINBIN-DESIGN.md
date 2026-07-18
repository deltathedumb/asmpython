# Pyinbin Design

## Purpose

Pyinbin is a native Python interpreter implemented in asmpython source and
compiled by asmpython. The produced executable must not depend on CPython, a
Python installation, or host-Python extension modules at runtime.

Pyinbin is a 3.14 stretch goal. It is a distinct execution engine, not an
FFI-backed stdlib shim and not an alias for the compiler's existing static
whole-program import merger.

## Runtime Import Contract

The compiler has two import modes:

1. Static merge: project and bundled modules that the compiler can lower are
   merged into the native program at build time. This remains the default.
2. Pyinbin module: a source module marked for runtime interpretation is
   packaged with the executable and loaded by pyinbin when imported.

Runtime interpretation is selected by `pyinbin_imports` until pyinbin reaches
full conformance. A project build packages those roots and executes the entry
through pyinbin; it reports that no native artifact was produced until the
target VM is embedded. It must never silently fall back to CPython.

For a native backend rejection (`NotImplementedError` in code generation), the
CLI now attempts the same source through pyinbin. A successful fallback is an
execution result, not a fabricated native artifact; if pyinbin also rejects the
source, both diagnostics are reported and the build fails.

The eventual project metadata is:

```json
{
  "pyinbin_imports": ["plugins", "third_party.dynamic_module"]
}
```

`pyinbin_imports` identifies module roots that are distributed as Python
source. All descendants under those roots are resolved through pyinbin's
import system. Regular imports continue through static merging.

Use `asmpython pyinbin package project.json` to produce the current source
bundle. It writes `manifest.json` plus a project-relative `src/` tree; every
manifest entry includes the qualified module name, path, byte size, and
SHA-256 digest. The command is intentionally separate from `build` until the
native loader and VM are embedded in each target.

## Architecture

Pyinbin is layered so every dependency can be compiled by asmpython:

1. Source loader: packaged-source lookup, module cache, relative/absolute
   resolution, and import-cycle handling. Relative package imports are now
   executed by the bootstrap loader.
2. Lexer and parser: produce an interpreter-specific syntax tree. Existing
   compiler front-end code may be shared only after it is itself compilable
   and free of host-only dependencies.
3. Compiler: lower syntax into portable pyinbin bytecode. A bytecode VM keeps
   interpreter semantics independent of the host ISA and avoids duplicating a
   direct AST evaluator for each target.
4. Virtual machine: frames, evaluation stack, calls, control flow, exception
   unwinding, generators, and coroutine suspension. The bootstrap VM now has
   typed `raise`/`try` handling and resumable `yield` generators; coroutine
   suspension and full exception/finally parity remain open.
5. Object model: integers, floats, strings, bytes, lists, tuples, dicts,
   sets, functions, classes, instances, descriptors, and modules. The bootstrap
   VM now covers class construction, inheritance, attributes, and bound methods.
   Native-extension modules such as `_io` remain an explicit runtime delivery
   item; they are not silently imported from the host interpreter.
6. Builtins and standard library: Python-level implementations plus explicit
   native bindings where an OS service is needed.

The VM and object layout are target-neutral. x86-64, AArch64, and macOS differ
only in asmpython's generated code and native FFI layer.

## Compatibility Rules

- No CPython subprocess, embedding API, or host-Python module loading is
  permitted in the packaged interpreter.
- Dynamic `import`, `exec`, and `eval` are interpreter features, not compiler
  errors, once their corresponding VM/compiler support lands.
- Imports execute a module exactly once per interpreter and publish a real
  module object through `sys.modules` semantics.
- Each completed language feature requires bytecode, VM, import-path, and
  conformance tests. Unsupported syntax is a precise `SyntaxError` or
  `NotImplementedError` during development, never silent misexecution.

## Delivery Order

1. Package format and explicit `pyinbin_imports` build metadata.
2. Bytecode data model, serializer, VM frame/evaluation stack, and literal/
   arithmetic/control-flow conformance tests.
3. Names, functions, closures, classes, descriptors, and exceptions.
4. Lists/dicts/sets/tuples, comprehensions, iterators, generators, and async.
5. Import system, source packaging, relative imports, cycles, and stdlib
   module loading.
6. Full conformance pass against the project test suite plus dedicated
   CPython-parity tests, then enable runtime import routing by default.

## Relationship To 3.14

Pyinbin progresses alongside the SSA/ARM64/macOS work but does not replace
the target-neutral IR migration. The IR work is required to compile pyinbin
for every 3.14 target; pyinbin is required for runtime execution of packaged
Python-source imports that cannot be statically merged.
