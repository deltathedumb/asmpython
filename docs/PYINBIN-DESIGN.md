# Pyinbin Design

## Purpose

Pyinbin is a native Python interpreter implemented in Python source compatible
with the asmpython language subset and compiled by asmpython. The produced
executable must not depend on CPython, a Python installation, or host-Python
extension modules at runtime.

Pyinbin is a distinct execution engine, not an FFI-backed stdlib shim and not an
alias for the compiler's static whole-program import merger. Its implementation
must remain Python-built: lexer, parser, bytecode compiler, virtual machine,
object model, imports, builtins, and standard library are authored in Python and
compiled to native code by asmpython.

A handwritten C/C++/Rust interpreter core or a CPython embedding wrapper does
not satisfy this requirement.

## Runtime Import Contract

The compiler has two import modes:

1. Static merge: project and bundled modules that the compiler can lower are
   merged into the native program at build time. This remains the default.
2. Pyinbin module: a source module selected for runtime interpretation is
   packaged with the executable and loaded by pyinbin when imported.

Static imports are resolved natively first. Only genuinely dynamic imports are
eligible for pyinbin fallback. Python packages installed by the active host
interpreter's pip may contribute pure-Python source during compilation, but the
packaged native program must never silently depend on CPython at runtime.

`pyinbin_imports` remains available as an explicit set of source roots for the
runtime interpreter. Those roots identify modules distributed as Python source;
all descendants under a selected root are resolved through pyinbin's import
system. Regular imports continue through static merging.

Use `asmpython pyinbin package project.json` to produce the current source
bundle. It writes `manifest.json` plus a project-relative `src/` tree; every
manifest entry includes the qualified module name, path, byte size, and SHA-256
digest. The command remains separate from `build` until the native loader and VM
are embedded in each target.

For a native backend rejection, the CLI may attempt the same source through
pyinbin only when the source actually requires dynamic interpretation. A
successful fallback is an execution result, not a fabricated native artifact;
if pyinbin also rejects the source, both diagnostics are reported and the build
fails.

## Architecture

Pyinbin is layered so every interpreter-semantic dependency can be compiled by
asmpython:

1. Source loader: packaged-source lookup, module cache, relative/absolute
   resolution, and import-cycle handling.
2. Lexer and parser: produce an interpreter-specific syntax tree. Existing
   compiler front-end code may be shared only after it is itself compilable and
   free of host-only dependencies.
3. Compiler: lower syntax into portable pyinbin bytecode. A bytecode VM keeps
   interpreter semantics independent of the host ISA and avoids duplicating a
   direct AST evaluator for each target.
4. Virtual machine: frames, evaluation stack, calls, control flow, exception
   unwinding, generators, and coroutine suspension.
5. Object model: integers, floats, strings, bytes, lists, tuples, dicts, sets,
   functions, classes, instances, descriptors, and modules.
6. Builtins and standard library: Python-level implementations plus explicit
   native service bindings where an OS operation is required.
7. asmpython integration adapter: project bundle manifests, compiler fallback
   diagnostics, static-merge handoff, and executable-specific module routing.

The first six layers form the reusable interpreter core. The seventh is tailored
to asmpython and must not leak into the generic VM API.

The VM and object layout are target-neutral. x86-64, AArch64, Windows, Linux,
and macOS differ only in asmpython's generated code and the minimal native ABI/
platform service layer.

## Fully Python-Built Contract

- Interpreter semantics are implemented in Python source only.
- Native export/loader glue may adapt calling conventions and expose symbols,
  but it may not implement parsing, execution, objects, imports, exceptions,
  builtins, or stdlib semantics.
- Bootstrap output must execute without CPython or a Python installation.
- Native-extension modules such as CPython `.pyd`/`.so` modules are not loaded
  through the CPython C API.
- Unsupported syntax or runtime behavior must fail precisely; it must never be
  approximated by delegating to a hidden host interpreter.

## PortaPy Fork

PortaPy is the separately versioned, embeddable DLL/shared-library project forked
from pyinbin's reusable Python-built core.

Pyinbin remains the tailored asmpython integration. PortaPy removes project-
specific bundle/compiler policy and exposes the shared interpreter through a
stable public ABI with opaque handles and host callbacks. The public C ABI is a
thin boundary around native code generated from the Python implementation; it is
not a C implementation of the VM.

Until an intentional divergence is documented, semantic fixes in the shared
core must be portable between pyinbin and PortaPy. Conformance cases should be
shared in a format both projects can execute. Detailed PortaPy requirements and
ABI principles live in `docs/PORTAPY-DESIGN.md`.

## Compatibility Rules

- No CPython subprocess, embedding API, or host-Python module loading is
  permitted in the packaged interpreter.
- Dynamic `import`, `exec`, and `eval` are interpreter features, not compiler
  errors, once their corresponding VM/compiler support lands.
- Imports execute a module exactly once per interpreter and publish a real
  module object through `sys.modules` semantics.
- Each completed language feature requires bytecode, VM, import-path, and
  conformance tests.
- Unsupported syntax is a precise `SyntaxError` or `NotImplementedError` during
  development, never silent misexecution.
- PortaPy and pyinbin must agree on observable behavior for the shared core.

## Delivery Order

1. Package format and explicit runtime-source build metadata.
2. Bytecode data model, serializer, VM frame/evaluation stack, and literal/
   arithmetic/control-flow conformance tests.
3. Names, functions, closures, classes, descriptors, and exceptions.
4. Lists/dicts/sets/tuples, comprehensions, iterators, generators, and async.
5. Import system, source packaging, relative imports, cycles, and stdlib module
   loading.
6. Separate the generic Python-built core from the asmpython adapter layer.
7. Fork the generic core into PortaPy and add its opaque-handle public ABI.
8. Run the shared project suite and dedicated CPython-parity tests, then enable
   runtime import routing by default only after the measured compatibility gate.

## 3.14 Release Gates

3.14 requires all of the following for the interpreter track:

- pyinbin remains integrated with asmpython's static/native import pipeline;
- the reusable interpreter core is cleanly separated from the integration
  adapter;
- PortaPy exists as a separately versioned project built from that Python core;
- PortaPy produces at least a Windows DLL and Linux shared object when the
  corresponding asmpython library targets are available;
- neither artifact requires CPython after bootstrap;
- pyinbin and PortaPy pass the shared conformance corpus;
- PortaPy's public ABI is tested from an external native host;
- `tests/cpython_conformance.py --required --mode pyinbin` records the complete
  discovered-module result alongside the CPython baseline before any full
  compatibility claim.

## Relationship to the Other 3.14 Work

Pyinbin and PortaPy progress alongside the SSA/ARM64/macOS work but do not replace
the target-neutral IR migration. The IR and library-output work are required to
compile the Python-built interpreter into every 3.14 executable/shared-library
target. Pyinbin is required for tailored runtime execution of packaged dynamic
Python source; PortaPy is the reusable embedding product built from the same
interpreter core.
