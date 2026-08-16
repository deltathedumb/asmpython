# PortaPy Design and 3.14 Requirements

## Purpose

PortaPy is the reusable, embeddable distribution of pyinbin. It is a separate
project and versioned product whose native build is a DLL/shared library with a
stable public API.

PortaPy is **not** a CPython embedding wrapper, a C/C++ interpreter, or a second
unrelated VM. Its interpreter implementation is forked from pyinbin and remains
written entirely in Python source compatible with the asmpython language subset.
asmpython compiles that Python implementation into the platform-native library:

- `portapy.dll` on Windows,
- `libportapy.so` on Linux,
- `libportapy.dylib` on macOS.

The public C ABI is only a thin host boundary around the Python-built
interpreter. Handwritten native code may provide export annotations, loader
entry points, calling-convention adapters, and unavoidable OS bootstrap glue; it
must not implement parsing, bytecode, evaluation, objects, imports, exceptions,
builtins, or standard-library semantics.

## Relationship to pyinbin

pyinbin remains asmpython's tailored interpreter integration. It owns behavior
specific to compiled asmpython programs, including packaged-source manifests,
static-merge handoff, runtime import routing, project metadata, and compiler
fallback diagnostics.

PortaPy forks the reusable interpreter core:

- lexer and parser,
- interpreter bytecode and serializer,
- compiler from syntax tree to bytecode,
- virtual machine and frames,
- object model,
- exception and generator machinery,
- import engine,
- builtins and Python-level standard library.

PortaPy must not depend on asmpython compiler internals at runtime. pyinbin may
adapt the shared core for asmpython-specific packaging, but the generic VM core
must remain reusable.

Until the projects deliberately diverge, fixes to shared interpreter semantics
must be portable in both directions. Shared conformance cases should live in a
format both projects can consume. A divergence must be recorded rather than
silently allowing two implementations of Python behavior to drift.

## Fully Python-built requirement

Both pyinbin and PortaPy are Python-built interpreters:

1. Their lexer, parser, compiler, VM, object model, import system, builtins, and
   standard library are authored in Python source.
2. That source must compile through asmpython; neither product may require
   CPython after bootstrap.
3. Neither product may load host CPython extension modules or use the CPython C
   API as its execution engine.
4. The PortaPy DLL/shared library must contain the native code generated from
   the Python implementation, not a launcher that starts another interpreter.
5. Native ABI glue must remain semantics-free and independently auditable.

## Public ABI principles

The ABI uses opaque handles and fixed-width primitive types. Host applications
must never depend on PortaPy's internal object layout, VM frame layout, allocator,
or bytecode representation.

The first stable ABI must provide at least:

- ABI and implementation version queries,
- runtime creation and destruction,
- configuration through a size/versioned options structure,
- execution of UTF-8 source and serialized PortaPy bytecode,
- evaluation returning an opaque value handle,
- retain/release operations for returned handles,
- conversion of primitive values through explicit checked functions,
- retrieval of structured exception type/message/traceback data,
- host output, input, clock, filesystem, and import callbacks,
- module registration from source, bytecode, or host callbacks,
- explicit interruption/cancellation,
- deterministic teardown with no callbacks after destruction.

Every exported function returns a status code. Exceptions and diagnostics belong
to the runtime instance and must not cross the ABI as native-language exceptions.
Strings and byte buffers always carry explicit lengths; no API may require a
NUL-terminated Python string.

## ABI versioning

PortaPy versions its implementation separately from the ABI.

- ABI version is an integer queried at runtime.
- Structures begin with `struct_size` and `abi_version` fields.
- New fields may only be appended.
- Existing exported names and field meanings cannot change within an ABI major
  version.
- A host must be able to reject an incompatible library before creating a VM.
- Value/VM handles created by one loaded PortaPy library may not be passed to a
  different loaded copy.

The initial public ABI remains provisional until an external C host and at least
one additional language binding execute the same conformance suite.

## Host callbacks and isolation

Host services are opt-in callbacks supplied when a runtime is created. PortaPy
must run with no ambient filesystem, network, environment, or process access when
those callbacks are absent. This makes an embedded runtime sandboxable without
forking the interpreter.

Callbacks receive a host context pointer and may not retain temporary PortaPy
buffers after returning. Re-entry into the same runtime is forbidden unless the
specific callback is documented as re-entrant. Thread ownership and callback
threading rules are part of the ABI contract, not implementation accidents.

## Memory ownership

- PortaPy owns all opaque runtime/value/error handles it creates.
- Hosts own buffers they pass into calls for the duration of the call.
- PortaPy-owned returned buffers must either remain valid until the next call on
  the same runtime or be released through an explicit API; the final ABI must
  choose one rule per function and document it.
- Reference-count or tracing-GC implementation details remain private.
- Runtime destruction must release every object reachable only from that runtime.

## Build outputs

The PortaPy project must build at least:

- a Windows x86-64 DLL,
- a Linux x86-64 shared object,
- static libraries where the platform toolchain supports them.

ARM64 Windows/Linux and macOS x64/ARM64 follow as asmpython's corresponding
library/object-format targets become production-ready. The source interpreter is
target-neutral; only generated code and ABI glue vary by platform.

Each release ships:

- the library,
- a public C header,
- import libraries where required,
- ABI documentation,
- a tiny C embedding example,
- checksums and machine-readable version metadata.

## Conformance and release gates

PortaPy 3.14 work is not complete merely when a DLL loads. Required gates are:

1. pyinbin's interpreter conformance suite passes against the shared core.
2. PortaPy's C embedding tests execute source, call functions, exchange values,
   surface exceptions, import a host module, and destroy/recreate runtimes.
3. The same Python corpus produces equivalent observable behavior through the
   pyinbin CLI and PortaPy ABI.
4. The library runs without CPython or a Python installation present.
5. Export inspection confirms only the documented ABI is public.
6. ABI misuse tests return errors rather than corrupting memory.
7. Windows DLL and Linux shared-object hosts are verified independently.

Full CPython compatibility claims remain gated on the existing
`tests/cpython_conformance.py` requirement. PortaPy must report unsupported
behavior precisely rather than silently approximating it.

## Delivery order

1. Separate reusable pyinbin core from asmpython-specific packaging adapters.
2. Create the separately versioned PortaPy project from that Python source.
3. Define the provisional opaque-handle C header and generated export shim.
4. Build a Python-authored runtime into a loadable library with create/destroy
   and source execution.
5. Add values, calls, exceptions, callbacks, imports, and bytecode loading.
6. Add Windows/Linux packaging and external-host conformance tests.
7. Stabilize ABI v1 only after two independent host-language integrations.

## Non-goals

- Reimplementing the interpreter in C, C++, Rust, or assembly.
- Embedding CPython behind a new API name.
- Exposing internal Python object pointers or bytecode structures.
- Making PortaPy responsible for asmpython's static compiler or project format.
- Letting PortaPy replace pyinbin's tailored asmpython integration.
