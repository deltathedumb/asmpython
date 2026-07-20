# asmpython 3.14.0 Release Requirements

**Status:** Normative release contract for `3.14.0`  
**Branch:** `beta/3.14.0`  
**Authority:** This document is the source of truth for whether asmpython 3.14.0 is releasable.

The older `roadmap.md` is historical planning material. It may still describe useful ideas, but it does not define the 3.14.0 release gate where it conflicts with this file, `RESUME.md`, or verified implementation state.

---

## 1. Normative language

The words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

A requirement is complete only when all of the following exist:

1. implementation,
2. tests,
3. target-specific verification where applicable,
4. user-facing diagnostics,
5. documentation,
6. compatibility-inspector reporting,
7. memory-ownership coverage,
8. no known silent miscompilation or silent semantic deviation.

A feature implemented on only one production target is not complete unless the feature is explicitly target-specific by nature.

A passing happy-path test alone does not make a feature complete.

---

## 2. Release identity

asmpython 3.14.0 is a Python 3.14 implementation built around ahead-of-time native compilation, with PyinBin providing the dynamic execution half of the implementation and PortaPy providing the reusable embeddable interpreter product.

The release promise is:

> Write ordinary Python 3.14. Build native software. Preserve dynamic Python behavior when static compilation cannot determine it. Require no hidden CPython installation in produced applications.

3.14.0 is not merely an ARM64 release, an IR rewrite, or a larger standard-library release. It is the release in which asmpython becomes a complete native Python software platform.

---

## 3. Absolute language boundary: do not change Python

### 3.1 No asmpython language dialect

asmpython 3.14.0 MUST NOT add or require:

- new keywords,
- new statement forms,
- altered operator meanings,
- altered scoping rules,
- altered exception semantics,
- altered import semantics,
- source preprocessing that creates syntax invalid under Python 3.14,
- mandatory type annotations,
- mandatory compiler decorators,
- mandatory source rewrites,
- compiler-only grammar extensions,
- `extend` / `retract` or equivalent syntax mutation,
- custom declarations such as `const`, `interface`, `sealed`, `readonly`, or similar language-level additions.

The withdrawn compiler-extension system remains historical and MUST NOT return as a 3.14 feature.

### 3.2 Pythonic extensions are allowed

asmpython MAY provide additional capabilities through ordinary Python mechanisms, including:

- importable standard-library modules,
- functions,
- classes,
- decorators,
- context managers,
- metaclasses,
- configuration files,
- command-line options,
- environment variables,
- packaging metadata.

Such additions MUST use valid Python syntax and MUST NOT silently redefine existing Python behavior.

Compiler-specific APIs MUST be explicit, namespaced, documented, and optional. Ordinary Python programs MUST NOT need to import them.

### 3.3 Python is the behavioral authority

The Python 3.14 language reference and observable CPython 3.14 behavior are the compatibility authority except where behavior is explicitly implementation-defined.

When asmpython differs, it MUST do one of the following:

1. implement the Python behavior,
2. execute the behavior through PyinBin,
3. reject it with a precise diagnostic,
4. document an implementation-defined difference that does not falsely claim compatibility.

Silent approximation is forbidden.

---

## 4. Universal definition of done: no half-finished features

A user-visible feature MUST NOT be advertised as supported until it has:

1. parser support where syntax is involved,
2. semantic-analysis support,
3. native lowering on every claimed native backend,
4. PyinBin behavior where dynamic execution is relevant,
5. correct successful behavior,
6. correct failure and exception behavior,
7. traceback support,
8. memory-management support,
9. compatibility-inspector support,
10. positive tests,
11. negative tests,
12. edge-case tests,
13. cross-engine differential tests where applicable,
14. documentation,
15. release-note coverage,
16. independent binary or runtime verification for low-level work.

Experimental work MAY exist, but it MUST:

- be visibly labeled experimental,
- be disabled by default unless specifically intended as a preview surface,
- be excluded from production compatibility claims,
- fail clearly outside its declared scope,
- never masquerade as complete support.

There MUST be no known silent miscompilations in a 3.14.0 release.

---

## 5. Simple, boringly reliable CLI

### 5.1 Required command surface

The primary CLI MUST provide a stable, documented command family centered on:

```text
asmpython build
asmpython run
asmpython check
asmpython inspect
asmpython test
asmpython doctor
asmpython explain
asmpython version
```

A bare source path MAY remain shorthand for `asmpython build SOURCE` for backward compatibility.

Commands MAY have additional subcommands, but the ordinary workflow MUST remain obvious without compiler knowledge.

### 5.2 One-command builds

The following MUST be sufficient for an ordinary distributable build:

```text
asmpython build app.py
```

or:

```text
asmpython build project.json
```

That single command MUST perform every required stage:

- source discovery,
- dependency discovery,
- static analysis,
- native compilation,
- native/PyinBin partitioning,
- PyinBin source or bytecode packaging,
- runtime inclusion,
- resource inclusion,
- object generation,
- linking,
- bundle generation,
- output validation,
- build-manifest generation.

Users MUST NOT need to run a separate PyinBin packaging command for a normal hybrid build.

### 5.3 CLI reliability requirements

The CLI MUST:

- use stable documented exit codes,
- distinguish user errors from internal compiler failures,
- never print an implementation-language traceback for an ordinary user error,
- support machine-readable JSON output for every command that emits diagnostics,
- support SARIF output for static diagnostics,
- honor `NO_COLOR`,
- behave correctly with spaces, Unicode, and non-ASCII paths,
- behave correctly from any working directory,
- avoid leaving corrupt or misleading output after failure,
- build into temporary paths and atomically replace final artifacts,
- preserve a previous valid output when a rebuild fails,
- clean temporary files unless asked to retain them,
- clearly state which target, backend, linker, and execution mode were selected,
- clearly state when PyinBin is included,
- clearly state when a dependency is rejected.

### 5.4 Toolchain handling

`asmpython doctor` MUST inspect and report:

- compiler installation,
- active asmpython version,
- host Python used to run the compiler during bootstrap/development,
- target toolchains,
- assemblers,
- linkers,
- SDKs,
- code-signing tools,
- emulator availability,
- required environment variables,
- writable cache/build directories.

For supported cross-compilation targets, asmpython MUST either:

- provide the needed toolchain itself,
- download and pin a verified toolchain,
- or provide a precise one-command installation path.

A missing tool MUST produce a direct actionable diagnostic rather than an indirect linker or subprocess error.

---

## 6. Compiling, quite frankly, just Python

### 6.1 Source compatibility

asmpython MUST accept ordinary Python 3.14 source without requiring an asmpython dialect.

The complete Python 3.14 grammar MUST be parsed. A construct that is not natively compilable MUST still be eligible for PyinBin execution unless it is genuinely unsupported by the Python implementation as a whole.

### 6.2 Behavioral compatibility

The implementation MUST preserve Python behavior for, at minimum:

- arbitrary-precision integers,
- IEEE-754 floating-point behavior where Python specifies it,
- distinct `bool`, `None`, and integer semantics,
- Unicode strings by code point rather than byte approximation,
- bytes, bytearray, memoryview, and buffer behavior,
- lists, tuples, dicts, sets, frozensets, ranges, and slices,
- iteration and iterator invalidation behavior,
- generators and generator finalization,
- coroutines, `async`, `await`, and asynchronous iteration,
- comprehensions,
- closures and `nonlocal`,
- decorators,
- descriptors,
- properties,
- class and static methods,
- multiple inheritance,
- C3 method-resolution order,
- metaclasses,
- `super`,
- `__slots__`,
- special-method lookup,
- exceptions and exception chaining,
- exception groups,
- context managers,
- structural pattern matching,
- annotations and annotation evaluation rules,
- import hooks,
- package imports,
- namespace packages,
- `exec`, `eval`, and dynamic compilation,
- reflection through `getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `type`, and related builtins,
- pickling-visible object behavior where supported by the standard library.

The native compiler MAY specialize representations internally, but observable behavior MUST remain Python-compatible.

### 6.3 Static typing is an implementation technique, not a source restriction

The compiler MAY infer types and specialize code, but MUST NOT require users to make valid Python statically typable.

When static certainty is unavailable, asmpython MUST use one or more of:

- guarded specialization,
- tagged or boxed values,
- runtime dispatch,
- PyinBin execution.

It MUST NOT silently assume a narrower type than Python semantics permit.

---

## 7. Native compiler architecture

### 7.1 Target-neutral IR

The production compiler MUST lower checked Python into a target-neutral intermediate representation before ISA-specific code generation.

The front end MUST NOT contain target-specific instruction strings, register names, relocation assumptions, or calling-convention hacks.

### 7.2 Production native backends

3.14.0 MUST include production-quality native backends for:

- x86-64,
- AArch64 / ARM64.

Each production backend MUST include:

- instruction selection,
- register allocation,
- stack-frame construction,
- calling-convention implementation,
- object generation,
- relocation generation,
- debug/source metadata,
- exception/unwind integration,
- runtime integration,
- executable and library linking.

### 7.3 Object formats and ABIs

The following production combinations are REQUIRED:

- Windows x86-64: PE/COFF and Microsoft x64 ABI,
- Windows ARM64: PE/COFF and Microsoft ARM64 ABI,
- Linux x86-64: ELF64 and System V AMD64 ABI,
- Linux ARM64: ELF64 and AAPCS64,
- macOS x86-64: Mach-O and macOS x86-64 ABI,
- macOS ARM64: Mach-O and Apple AArch64 ABI.

### 7.4 Backend honesty

A backend MUST reject unsupported IR operations before emitting an artifact.

A backend MUST NOT:

- discard unsupported bodies,
- substitute constant return values,
- call hidden CPython execution,
- emit an object that cannot satisfy its runtime symbols,
- claim target support based only on object generation without successful linking and execution.

### 7.5 Legacy backend

The legacy backend MAY remain for compatibility and inline assembly, but it MUST NOT weaken the production release gate.

Any feature exposed through the legacy backend MUST either:

- be fully supported and tested within its declared scope,
- or be explicitly marked legacy-only and excluded from default-backend compatibility claims.

---

## 8. Production target set

### 8.1 Hosted targets

3.14.0 MUST build and run normal executables and libraries for:

- Windows x86-64,
- Windows ARM64,
- Linux x86-64,
- Linux ARM64,
- macOS x86-64,
- macOS ARM64.

### 8.2 Freestanding targets

The systems-level promise additionally requires supported freestanding targets for:

- x86-64 PC environments already supported by asmpython,
- ARM64 Raspberry Pi 4 / 5 or an equivalently documented AArch64 bare-metal platform.

Freestanding support MUST include:

- a bootable artifact,
- allocator support,
- panic handling,
- basic console output,
- memory-mapped I/O,
- interrupts or an explicitly documented first-stage interrupt boundary,
- deterministic startup,
- hardware or emulator execution verification.

### 8.3 Platform claims

A platform is supported only when all required categories work:

- compilation,
- linking,
- startup,
- exceptions,
- memory management,
- standard-library platform services,
- packaging,
- diagnostics,
- debug metadata,
- reproducibility,
- automated tests on real or independently validated hardware/emulation.

Object generation alone is not platform support.

---

## 9. PyinBin: the complete dynamic half of asmpython

### 9.1 Product role

PyinBin MUST be a complete Python interpreter implemented in Python source that asmpython compiles to native code.

PyinBin MUST NOT be:

- a CPython wrapper,
- a subprocess launcher for Python,
- a C/C++/Rust interpreter core,
- a hidden fallback to the build machine's Python installation,
- a stub that supports only imports.

### 9.2 Required interpreter capabilities

PyinBin MUST provide:

- Python 3.14 lexical analysis,
- parsing,
- bytecode or an equivalent portable interpreter representation,
- frames,
- calls,
- closures,
- classes,
- descriptors,
- exceptions,
- generators,
- coroutines,
- imports,
- `exec`,
- `eval`,
- dynamic code creation,
- reflection,
- builtins,
- Python-level standard-library loading,
- module caching,
- relative imports,
- namespace packages,
- import cycles,
- traceback generation,
- deterministic teardown.

### 9.3 Standalone requirement

After bootstrap compilation, PyinBin MUST run without:

- CPython,
- a Python installation,
- host-Python extension loading,
- a network connection,
- source files not intentionally packaged with the program.

### 9.4 Compiler integration

PyinBin MUST be embeddable into normal asmpython-produced applications as the dynamic runtime.

It MUST be usable for:

- dynamic imports,
- runtime-generated source,
- `eval`,
- `exec`,
- reflective code that cannot be statically represented,
- modules intentionally designated for dynamic execution,
- valid Python behavior not yet profitable or possible to compile natively.

Static imports MUST remain native-first. PyinBin MUST NOT become an excuse to interpret entire projects unnecessarily.

---

## 10. Seamless static/dynamic splitting

### 10.1 Automatic partitioning

The compiler MUST automatically determine which code can execute natively and which code requires PyinBin.

Partitioning MAY occur at:

- module boundaries,
- function boundaries,
- class boundaries,
- call sites,
- dynamic import sites,
- dynamic compilation sites.

The chosen boundary MUST preserve Python semantics.

### 10.2 Shared object behavior

Values crossing the native/PyinBin boundary MUST preserve:

- identity,
- aliasing,
- mutation visibility,
- reference ownership,
- exception state,
- class identity,
- descriptor behavior,
- iteration state,
- lifetime,
- cyclic references.

The implementation MAY use proxies, shared handles, tagged values, copying, or conversion internally, but observable Python behavior MUST remain correct.

### 10.3 Bidirectional calls

The following call paths MUST work:

```text
native -> PyinBin
PyinBin -> native
native -> PyinBin -> native callback
PyinBin -> native -> PyinBin callback
```

Re-entry rules, recursion, thread ownership, and callback lifetime MUST be documented and tested.

### 10.4 Cross-boundary exceptions and tracebacks

Exceptions MUST cross both directions without losing:

- exception type,
- message,
- cause,
- context,
- traceback frames,
- source locations.

A mixed native/PyinBin traceback MUST read as one coherent Python traceback.

### 10.5 Boundary visibility

`asmpython inspect` MUST show every static/dynamic boundary and explain why it exists.

No boundary may be inserted silently in a mode that claims fully native compilation.

---

## 11. PortaPy: standalone embeddable Python product

### 11.1 Separate project

PortaPy MUST exist as a separately versioned project and repository, not merely as an internal package inside asmpython.

It MUST be built from the reusable Python-authored interpreter core shared with PyinBin.

### 11.2 Required native artifacts

The companion 3.14 release MUST produce:

- `portapy.dll`,
- `libportapy.so`,
- `libportapy.dylib`,
- public C headers,
- import libraries where required,
- static libraries where practical,
- checksums,
- machine-readable version metadata,
- ABI documentation,
- embedding examples.

### 11.3 Stable public ABI

PortaPy's ABI MUST include:

- ABI version query,
- implementation version query,
- runtime create/destroy,
- versioned configuration structures,
- source execution,
- bytecode execution,
- expression evaluation,
- function calls,
- opaque value handles,
- retain/release,
- checked primitive conversion,
- structured exception and traceback retrieval,
- host output/input callbacks,
- host clock callbacks,
- host filesystem callbacks,
- host import callbacks,
- host module registration,
- interruption/cancellation,
- deterministic teardown,
- documented threading and re-entry rules.

### 11.4 Host bindings

PortaPy MUST be tested from:

- an external C host,
- Java through JNI or an equivalent production-quality Java binding.

The Java binding is a required first-class integration because PortaPy is intended to support JVM applications and projects such as Minecraft mods.

At least one example MUST demonstrate:

- registering host functions,
- executing Python source,
- receiving Python exceptions,
- exchanging values,
- unloading and recreating a runtime.

### 11.5 Isolation

A PortaPy runtime with no host callbacks MUST have no ambient access to:

- filesystem,
- network,
- environment,
- process creation,
- host memory,
- clock sources other than explicitly supplied deterministic services.

This requirement makes PortaPy suitable for sandboxed scripting and modding.

---

## 12. Pythonic systems-level interface

### 12.1 General rule

Systems programming capabilities MUST be provided through ordinary Python libraries, not new syntax.

The final public namespace MUST be coherent and stable. Transitional names MAY exist, but 3.14 documentation MUST identify one canonical namespace.

### 12.2 Hosted systems APIs

The systems library MUST provide Pythonic access to:

- files and directories,
- processes,
- environment variables,
- clocks and timers,
- threads,
- synchronization primitives,
- atomics,
- sockets,
- shared memory,
- memory mapping,
- dynamic libraries,
- native symbol lookup,
- terminal and console control,
- platform and architecture information,
- raw allocation where explicitly requested,
- executable memory where supported and explicitly requested.

### 12.3 Unsafe APIs

Raw memory, pointers, MMIO, port I/O, interrupts, privileged instructions, and direct device access MUST live in an explicit unsafe or hardware-oriented namespace.

Unsafe operations MUST:

- be visually obvious in source,
- validate what can reasonably be validated,
- document alignment and lifetime,
- document privilege requirements,
- fail precisely when unavailable,
- never be exposed accidentally on unsupported targets.

### 12.4 Freestanding APIs

The systems library MUST provide Pythonic freestanding interfaces for:

- boot entry,
- UART,
- framebuffer access,
- MMIO,
- interrupts,
- timers,
- memory regions,
- volatile reads/writes,
- architecture-specific instructions,
- panic and halt behavior.

Architecture-specific features MUST be importable through explicit architecture namespaces rather than changing Python syntax.

### 12.5 Capability inspection

The systems library MUST expose a way to inspect whether a capability exists on the current target.

Unsupported capabilities MUST raise precise platform-appropriate errors rather than becoming no-ops.

---

## 13. Standard library compatibility

### 13.1 Scope

3.14.0 MUST target the public Python 3.14 standard library, not a small hand-picked subset.

Every public standard-library module MUST be placed into one of these measured categories:

- fully compatible,
- compatible through native services,
- compatible through PyinBin,
- platform-inapplicable,
- intentionally unsupported with a documented reason.

There MUST be no uncategorized public module.

### 13.2 Pure-Python modules

Applicable pure-Python standard-library modules MUST execute under asmpython's combined native/PyinBin implementation.

Static merging SHOULD be used where practical. PyinBin MAY execute dynamic portions.

### 13.3 Native-service modules

Modules requiring operating-system or low-level services MUST use audited native bindings or Python-authored wrappers over those bindings.

Behavior MUST match Python semantics even when implementation differs from CPython.

### 13.4 Required areas

The release MUST include production support for the standard-library areas needed by real applications, including:

- filesystem and path handling,
- subprocess and process control,
- threading and synchronization,
- multiprocessing where supported,
- sockets and networking,
- SSL/TLS,
- HTTP clients and servers,
- serialization,
- compression and archives,
- regular expressions,
- text encodings and Unicode data,
- date, time, timezone, and calendar handling,
- logging,
- argparse,
- importlib,
- inspect,
- traceback,
- dataclasses,
- typing runtime behavior,
- asyncio,
- concurrent futures,
- sqlite3 or an explicitly documented equivalent native binding,
- testing utilities required by the conformance suite.

---

## 14. Package and PyPI compatibility

### 14.1 Normal Python package workflow

Python packages MUST be installed through ordinary Python packaging tools and discovered from normal environments and `site-packages`.

asmpython MUST NOT require a separate private package ecosystem for Python packages.

### 14.2 Pure-Python packages

Pure-Python packages MUST be eligible for:

- static compilation,
- hybrid native/PyinBin execution,
- resource packaging,
- normal package-relative imports,
- package metadata access.

### 14.3 Wheels and resources

The build system MUST understand:

- wheels,
- source distributions where build isolation is available,
- package data,
- importlib resources,
- namespace packages,
- entry points,
- optional dependencies,
- environment markers.

### 14.4 Native extension policy

3.14.0 MUST have a deliberate native-extension compatibility strategy.

At minimum:

- platform-native libraries MUST be callable through the FFI,
- CPython-specific extensions MUST be detected precisely,
- the compatibility inspector MUST identify the exact native module and ABI requirement,
- unsupported extensions MUST never be mistaken for pure Python.

Additionally, 3.14 SHOULD provide compatibility for Python `abi3` / Stable-ABI extension modules without embedding CPython. If complete Stable-ABI compatibility is not achieved, the exact implemented ABI surface and remaining blockers MUST be published.

### 14.5 Compatibility scourer

The existing compatibility scourer / pytest scout MUST be a supported release tool.

It MUST independently measure:

- CPython baseline behavior,
- native asmpython behavior with fallback disabled,
- PyinBin behavior,
- combined hybrid behavior.

It MUST publish machine-readable reports and exact diffs.

---

## 15. Compatibility inspector

### 15.1 Required command

The CLI MUST provide:

```text
asmpython inspect SOURCE_OR_PROJECT
```

with human-readable and JSON output.

### 15.2 Required report categories

The inspector MUST report separately:

- native-ready code,
- PyinBin-executed code,
- shared native/PyinBin objects,
- external native libraries,
- pure-Python dependencies,
- native extension dependencies,
- unsupported behavior,
- target-specific blockers,
- untested or uncertain behavior.

### 15.3 Required explanations

For every dynamic boundary or blocker, the inspector MUST report:

- source location,
- reason,
- selected execution engine,
- affected target,
- likely resolution where one exists.

### 15.4 Percentages

The inspector MUST NOT collapse all compatibility into one misleading number.

It MUST report at least:

- native coverage,
- PyinBin coverage,
- combined coverage,
- unsupported coverage,
- unverified coverage.

### 15.5 Performance and memory analysis

The inspector MUST also identify:

- native/PyinBin transition sites,
- boxed or dynamically typed hot paths,
- likely allocation-heavy paths,
- target-specific slow paths,
- missing optimization opportunities,
- external calls that prevent optimization.

---

## 16. Excellent errors and Python-quality tracebacks

### 16.1 Compile-time diagnostics

Compiler diagnostics MUST include:

- file,
- line,
- column,
- source excerpt,
- caret or range,
- stable error code,
- clear explanation,
- relevant inferred types or symbols,
- import chain where relevant,
- actionable resolution where one exists.

Multiple independent semantic errors SHOULD be reported in one pass.

### 16.2 Runtime tracebacks

Native runtime exceptions MUST produce Python-style tracebacks with:

- source filenames,
- source line numbers,
- function names,
- exception type,
- exception message,
- chaining and cause.

PyinBin tracebacks MUST follow the same format.

Mixed native/PyinBin execution MUST produce one coherent traceback.

### 16.3 Internal compiler failures

An internal compiler failure MUST:

- be labeled as an asmpython bug,
- avoid blaming user source,
- print a concise failure summary,
- write a diagnostic bundle,
- include compiler version, target, backend, linker, stage, and normalized IR when available,
- avoid including unrelated private source by default,
- provide a reproducible command where possible.

### 16.4 Native debug metadata

Production targets MUST support source-level debug information consumable by the platform's normal tools:

- GDB on Linux,
- LLDB on macOS,
- WinDbg or compatible Windows debuggers.

Optimized builds MUST preserve enough mapping for meaningful tracebacks.

---

## 17. Good memory management

### 17.1 General requirement

3.14.0 MUST have real memory management suitable for long-running applications.

Leaking all allocations is not acceptable.

The exact implementation MAY use:

- reference counting,
- tracing garbage collection,
- cycle detection,
- region allocation,
- or a documented hybrid.

The algorithm is an implementation choice. Correct lifetime behavior is a release requirement.

### 17.2 Managed values

Memory management MUST cover:

- strings,
- bytes and bytearrays,
- lists,
- tuples,
- dicts,
- sets,
- functions,
- closures,
- generators,
- coroutines,
- exceptions,
- tracebacks,
- classes,
- instances,
- descriptors,
- modules,
- PyinBin frames and values,
- native/PyinBin bridge objects,
- PortaPy handles,
- FFI-owned and host-owned memory.

### 17.3 Cycles and finalization

The implementation MUST handle:

- object cycles,
- cycles crossing native/PyinBin boundaries,
- weak references,
- finalizers,
- generator cleanup,
- coroutine cleanup,
- interpreter shutdown,
- PortaPy runtime destruction.

Finalization ordering and resurrection behavior MUST be compatible with Python where observable.

### 17.4 Ownership contracts

Internal and public APIs MUST distinguish:

- owned references,
- borrowed references,
- transferred references,
- pinned references,
- host-owned buffers,
- runtime-owned buffers.

PortaPy's retain/release API MUST map cleanly onto the underlying lifetime system.

### 17.5 Verification

The release gate MUST include:

- leak testing,
- use-after-free testing,
- double-free testing,
- cycle stress tests,
- repeated runtime create/execute/destroy tests,
- long-running allocation/deallocation tests,
- mixed native/PyinBin lifetime tests,
- FFI ownership tests.

---

## 18. Predictable performance

### 18.1 Honest performance model

asmpython MUST NOT claim that every program is faster than CPython.

It MUST provide predictable, measurable performance characteristics for:

- startup,
- build time,
- steady-state native execution,
- PyinBin execution,
- native/PyinBin transitions,
- memory use,
- binary size,
- library size.

### 18.2 Required optimization pipeline

Production native backends MUST include a real optimization pipeline with, at minimum:

- constant folding,
- constant propagation,
- dead-code elimination,
- dead-store elimination,
- control-flow simplification,
- copy propagation,
- peephole optimization,
- SSA phi elimination,
- register allocation,
- type specialization,
- devirtualization where proven safe,
- inlining where profitable and safe,
- bounds-check elimination where proven safe,
- escape analysis where practical.

### 18.3 Optimization correctness

Every optimization MUST preserve Python semantics, including exceptions and observable side effects.

Optimization MUST NOT remove:

- potentially raising operations,
- descriptor calls,
- import side effects,
- finalization-visible behavior,
- mutation-visible behavior,
- traceback-relevant frames unless explicitly allowed by the selected optimization/debug profile.

### 18.4 Performance regression tracking

A versioned benchmark suite MUST track:

- startup latency,
- compile time,
- native numeric loops,
- object-heavy workloads,
- imports,
- function calls,
- exceptions,
- strings,
- containers,
- PyinBin workloads,
- hybrid boundary cost,
- real applications.

Material regressions MUST block release unless explicitly documented and accepted for a correctness reason.

### 18.5 Profiles

The CLI MUST expose clear profiles such as:

- debug,
- release,
- size-optimized.

Profile behavior MUST be documented and reproducible.

Profile-guided optimization MAY be included, but MUST remain optional and deterministic when a fixed profile is supplied.

---

## 19. Reproducible and inspectable builds

### 19.1 Byte reproducibility

Given identical:

- source,
- dependency lock,
- compiler version,
- target,
- build configuration,
- pinned toolchain,
- supplied profile data,

asmpython MUST produce byte-identical release artifacts.

### 19.2 Sources of nondeterminism

The build MUST normalize or control:

- timestamps,
- temporary paths,
- absolute source paths,
- filesystem traversal order,
- hash iteration order,
- archive member order,
- object and symbol order,
- debug metadata paths,
- build IDs,
- code-signing inputs,
- toolchain version selection,
- environment-dependent feature detection.

### 19.3 Build manifest

Every build MUST be able to emit a machine-readable manifest containing:

- compiler version and commit,
- target,
- backend,
- linker,
- optimization profile,
- all source hashes,
- all dependency hashes,
- toolchain identities,
- bundled PyinBin modules,
- static/dynamic partition decisions,
- external libraries,
- output hashes.

### 19.4 Verification command

The CLI MUST provide a reproducibility verification mode that performs isolated rebuilds and compares every output.

### 19.5 Supply-chain metadata

Release builds MUST be able to emit:

- SBOM metadata,
- checksums,
- provenance metadata,
- dependency licenses,
- signature-ready artifact manifests.

---

## 20. Deterministic self-hosting

### 20.1 Compiler self-hosting

The asmpython compiler MUST compile itself through the production compiler pipeline.

The self-hosted compiler MUST:

- run without CPython,
- compile normal asmpython projects,
- produce behavior equivalent to the bootstrap compiler,
- support every production target needed to build the release.

### 20.2 Fixed-point requirement

A deterministic staged build MUST be demonstrated:

```text
stage 0: trusted bootstrap compiler
stage 1: stage 0 compiles asmpython
stage 2: stage 1 compiles asmpython
stage 3: stage 2 compiles asmpython
```

Stage 2 and Stage 3 release artifacts MUST be byte-identical under the reproducible-build contract.

### 20.3 No hidden bootstrap dependency

Published self-hosted artifacts MUST not require CPython after build.

Bootstrap instructions MUST be documented separately from normal end-user installation.

---

## 21. Public APIs, ABI, FFI, and library output

### 21.1 Stable APIs

3.14.0 MUST define and version:

- backend registration API,
- linker registration API,
- multi-language integration API,
- compiler invocation API,
- diagnostic API,
- inspector report schema,
- build-manifest schema,
- runtime ABI,
- PortaPy ABI.

Breaking changes MUST be intentional, documented, and versioned.

### 21.2 Real library output

`--type library` MUST produce a genuinely callable library, not merely a shared container holding a whole-program `main` entry.

The compiler MUST provide an ordinary Pythonic way, such as an importable decorator, to mark exported functions.

Exported functions MUST support:

- stable symbol naming,
- C-compatible primitive signatures,
- opaque handles for Python objects,
- callbacks,
- error reporting,
- target calling conventions,
- generated headers or equivalent metadata.

### 21.3 FFI

The FFI MUST support the practical platform C ABI surface required by systems programming and package interoperability, including:

- integers of explicit widths,
- floating-point values,
- pointers,
- strings with explicit encodings and lengths,
- arrays,
- structs,
- unions where safe and specified,
- callbacks,
- variadic functions where supported,
- dynamic symbol lookup,
- library loading,
- ownership annotations,
- platform calling conventions.

FFI failures MUST become precise Python exceptions.

### 21.4 ABI tests

Every public ABI MUST have:

- external-host tests,
- symbol/export inspection,
- invalid-input tests,
- version-negotiation tests,
- layout tests,
- cross-language tests.

---

## 22. Security and isolation

### 22.1 No hidden execution

Produced applications MUST NOT silently execute source through:

- CPython,
- the build machine's Python,
- a system Python,
- an undeclared subprocess,
- a downloaded interpreter.

### 22.2 PyinBin and PortaPy sandboxing

PyinBin and PortaPy MUST support capability-based isolation.

Hosts MUST be able to control:

- filesystem access,
- network access,
- imports,
- environment access,
- process creation,
- clocks,
- randomness,
- memory limits,
- instruction or time limits,
- cancellation.

### 22.3 Resource limits

Untrusted embedded code MUST be interruptible.

Resource exhaustion MUST fail with controlled errors rather than corrupting the host process where technically possible.

### 22.4 Compiler robustness

The compiler front end, package reader, bytecode loader, object writers, and linkers MUST be fuzz-tested.

Malformed source, bytecode, object files, packages, and manifests MUST not cause memory corruption.

### 22.5 Dependency integrity

Downloaded toolchains and dependencies MUST be verified by cryptographic hash or signature.

The release process MUST publish checksums and provenance for official artifacts.

---

## 23. Measurable compatibility

### 23.1 Internal suite

Every required internal test MUST pass on every production backend and target.

There is no acceptable release baseline containing known unexplained failures.

### 23.2 CPython conformance

The release MUST run the official CPython 3.14 test corpus against:

- CPython baseline,
- native asmpython with PyinBin fallback disabled,
- PyinBin,
- combined hybrid execution.

Every discovered test/module MUST be recorded.

Failures MAY be excluded only when they test CPython implementation internals rather than Python behavior, and every exclusion MUST include a documented reason.

### 23.3 Differential semantics

The conformance system MUST compare more than stdout.

Where applicable it MUST compare:

- exit status,
- stdout,
- stderr,
- return values,
- exception type,
- exception message,
- traceback shape,
- mutations,
- filesystem effects,
- serialized results.

### 23.4 Real-package corpus

The compatibility scourer MUST run a versioned real-package corpus covering:

- command-line tools,
- web libraries,
- serialization,
- parsing,
- networking,
- testing frameworks,
- async code,
- object-heavy applications,
- packages with optional native extensions.

The exact corpus and package versions MUST be published so results are reproducible.

### 23.5 Public compatibility report

3.14.0 MUST ship with a generated compatibility report showing:

- native pass rate,
- PyinBin pass rate,
- combined pass rate,
- standard-library status,
- package-corpus status,
- target status,
- known implementation-defined differences.

Compatibility claims MUST link to evidence.

---

## 24. Testing and verification discipline

### 24.1 Required test types

The project MUST maintain:

- parser tests,
- semantic tests,
- IR tests,
- optimizer tests,
- encoder tests,
- register-allocation tests,
- object-format tests,
- relocation tests,
- linker tests,
- runtime tests,
- exception tests,
- memory tests,
- PyinBin tests,
- PortaPy tests,
- FFI tests,
- CLI integration tests,
- package tests,
- reproducibility tests,
- self-host tests,
- security/fuzz tests.

### 24.2 Independent low-level verification

Hand-encoded instructions, object formats, relocations, unwind data, and ABI shims MUST be checked against independent platform tools or specifications.

Examples include:

- assembler bit comparison,
- disassembler inspection,
- `readelf`,
- `objdump`,
- `otool`,
- PE/COFF inspection,
- linker cross-checks,
- debugger or syscall tracing,
- real or QEMU execution.

Reasoning about low-level code without independent verification is insufficient.

### 24.3 Sanitizers and analysis

Where implementation components can be checked by platform sanitizers or equivalent tools, CI MUST include them.

Static analysis, type checking, and linting MUST run on compiler and runtime sources.

### 24.4 Long-running tests

The release gate MUST include long-running tests for:

- memory stability,
- repeated imports,
- repeated dynamic compilation,
- exception-heavy workloads,
- thread creation/destruction,
- event loops,
- PortaPy runtime recreation,
- hybrid native/PyinBin calls.

---

## 25. Build speed, incremental builds, and cache correctness

A simple CLI does not justify rebuilding an entire large project unnecessarily.

3.14.0 MUST provide a content-addressed build cache for reusable stages where correctness permits.

The cache MUST:

- key on source content rather than timestamps alone,
- include compiler version,
- include target and profile,
- include dependency hashes,
- include relevant environment/toolchain identity,
- be safe under concurrent builds,
- detect corruption,
- be clearable through the CLI,
- never reuse an artifact across incompatible configurations.

Incremental builds MUST preserve reproducibility.

A clean rebuild and a cached rebuild MUST produce identical final artifacts.

---

## 26. Concurrency, async, and runtime services

Because these are normal Python capabilities, 3.14.0 MUST provide production behavior for:

- `threading`,
- thread-local storage,
- locks,
- conditions,
- semaphores,
- queues,
- futures,
- `asyncio`,
- subprocess integration,
- signals where supported,
- cancellation,
- interpreter shutdown with active tasks.

The implementation MAY use a GIL, per-runtime locks, fine-grained locks, or another strategy, but observable Python behavior and documented safety guarantees MUST be correct.

PortaPy MUST document whether separate runtime instances may execute concurrently and under what rules.

---

## 27. Installation, release artifacts, and updates

### 27.1 Installation

The normal compiler installation SHOULD be available through ordinary Python packaging during bootstrap/development, including PyPI release distribution through the dedicated release branch policy.

Installation MUST not require manual copying of repository internals.

### 27.2 Official artifacts

Official 3.14.0 releases MUST publish:

- compiler packages,
- self-hosted compiler executables where supported,
- runtime libraries,
- PyinBin executable/runtime artifacts,
- PortaPy artifacts from its separate project,
- public headers,
- documentation,
- checksums,
- signatures or signature-ready provenance,
- SBOMs,
- compatibility reports,
- benchmark reports.

### 27.3 Version consistency

All artifacts MUST report consistent implementation versions and ABI versions.

The release process MUST reject mismatched tags, branches, package metadata, and embedded versions.

---

## 28. Documentation and governance

### 28.1 Source of truth

This file defines 3.14.0 release readiness.

`RESUME.md` records current implementation state.

Historical roadmaps MUST NOT override this file.

### 28.2 Required documentation

The release MUST document:

- installation,
- CLI,
- project configuration,
- supported targets,
- compiler architecture,
- static/dynamic splitting,
- PyinBin,
- PortaPy relationship,
- systems library,
- standard-library status,
- package compatibility,
- memory model,
- FFI,
- public APIs and ABIs,
- performance model,
- reproducible builds,
- security model,
- known implementation-defined differences,
- migration from prior asmpython releases.

### 28.3 Generated status

Feature and compatibility tables SHOULD be generated from tests and manifests rather than manually copied between documents.

Stale status claims MUST be treated as documentation bugs.

### 28.4 Demos

Demos are valuable but are not 3.14.0 release blockers.

They may be produced over time independently of the release gate.

---

## 29. Required 3.14.0 release deliverables

A 3.14.0 release candidate MUST include all of the following:

1. production x86-64 and ARM64 compiler backends,
2. production Windows, Linux, and macOS targets for both architectures,
3. supported freestanding x86-64 and ARM64 paths,
4. complete one-command hybrid builds,
5. PyinBin embedded as the dynamic execution runtime,
6. separately released PortaPy native libraries,
7. C and Java/JNI PortaPy integrations,
8. coherent Pythonic systems library,
9. broad measured Python 3.14 standard-library compatibility,
10. normal pip/site-packages package integration,
11. compatibility inspector,
12. Python-quality native, PyinBin, and mixed tracebacks,
13. real memory management,
14. reproducible builds,
15. deterministic self-hosting,
16. real callable library output and stable FFI,
17. public compatibility report,
18. public benchmark report,
19. complete platform CI evidence,
20. no known silent miscompilations,
21. no advertised half-finished features.

---

## 30. Final release gate

asmpython 3.14.0 MUST NOT be tagged final until all statements below are true:

- [ ] Ordinary Python 3.14 source requires no asmpython dialect.
- [ ] No Python grammar or semantics were changed.
- [ ] All compiler-specific functionality is exposed Pythonically through libraries, decorators, configuration, or CLI options.
- [ ] `asmpython build` performs complete native/hybrid packaging in one command.
- [ ] CLI behavior and exit codes are stable and tested.
- [ ] x86-64 and ARM64 production backends pass all required suites.
- [ ] Windows x86-64 and ARM64 are verified.
- [ ] Linux x86-64 and ARM64 are verified.
- [ ] macOS x86-64 and ARM64 are verified.
- [ ] Required freestanding targets boot and execute verified programs.
- [ ] PyinBin is complete, Python-built, embedded, and independent of CPython.
- [ ] Native/PyinBin object, call, exception, traceback, and lifetime boundaries are correct.
- [ ] PortaPy is separately versioned and produces verified DLL/SO/dylib artifacts.
- [ ] PortaPy C and Java/JNI hosts pass conformance tests.
- [ ] The systems-level API is coherent, Pythonic, and documented.
- [ ] All public Python 3.14 standard-library modules are categorized and measured.
- [ ] Package and wheel handling is production-ready.
- [ ] Native extensions are precisely detected and the supported ABI surface is documented.
- [ ] The compatibility inspector reports native, PyinBin, combined, unsupported, and unverified coverage separately.
- [ ] Native, PyinBin, and mixed tracebacks are Python-quality.
- [ ] Real memory management passes leak, cycle, lifetime, and teardown tests.
- [ ] Reproducibility verification produces byte-identical outputs.
- [ ] The compiler reaches a deterministic self-hosting fixed point.
- [ ] Public APIs and ABIs are versioned and externally tested.
- [ ] Library output exports callable functions through a documented ABI.
- [ ] Security, isolation, cancellation, and malformed-input tests pass.
- [ ] The internal test suite has zero unexplained failures.
- [ ] CPython conformance results are complete and published.
- [ ] The real-package compatibility corpus results are published.
- [ ] Performance and memory benchmark reports are published.
- [ ] Release artifacts include checksums, provenance, SBOMs, and consistent versions.
- [ ] Documentation matches verified behavior.
- [ ] No known silent miscompilation remains.
- [ ] No incomplete feature is advertised as complete.

---

## 31. Explicit prohibitions

The following are never acceptable shortcuts for completing 3.14.0:

- changing Python syntax to make compilation easier,
- changing Python semantics for performance,
- requiring annotations for valid programs,
- silently interpreting an entire program while calling it native,
- using CPython behind PyinBin or PortaPy,
- accepting wrong output because it is close,
- emitting an artifact for unsupported IR,
- claiming a platform from compile-only evidence,
- calling memory leaks temporary in the final release,
- reporting one combined compatibility percentage that hides native failures,
- declaring a feature complete without failure-path behavior,
- declaring a backend complete without independent execution evidence,
- keeping known silent miscompilations for a later patch.

---

## 32. Release philosophy

The scope of 3.14.0 is intentionally large. The constraint is not how ambitious the release may be. The constraint is that asmpython remains Python.

The release may radically improve how Python is:

- compiled,
- optimized,
- deployed,
- embedded,
- inspected,
- debugged,
- used for systems programming,
- used without CPython.

It may not solve those problems by inventing a different language and calling it Python.
