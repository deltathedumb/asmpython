# 3.14 Resume

Short, current-state-only. Full history lives in `git log`; this file points to
where the 3.14.0 push stands now rather than serving as a chronological journal.

## Directive

Versioned **3.14.0** (matching CPython's version number), not 2.0.0. ARM64
requires a target-neutral compiler architecture rather than a parallel target
subclass, so this is a real architecture release even where the Python surface
is unchanged. See `roadmap.md` for the complete release definition.

Standing instruction: work continuously through the punch list without pausing
for routine check-ins. Checkpoint through commits and this file. Use no more
than 1-2 subagents at once. Long suites must run through a background execution
mechanism rather than synchronously.

## Architecture (current)

- `asmpython/_compiler/ir_lower.py` lowers checked AST into the target-neutral
  SSA IR in `asmpython/_compiler/ir.py`.
- `asmpython/_backends/x86_64/` remains the default/reference native backend.
- `asmpython/_backends/ternary/` is the experimental second IR backend.
- `asmpython/_backends/arm64/` is the active in-progress backend; exact status
  is below.
- `--backend legacy` remains the NASM-text path required for `@assembly_func`.
- `asmpython/pyinbin/` is the fallback Python bytecode VM.
- The compiler-syntax-extension system is withdrawn and archived under
  `archived/extensions/`; do not resurrect it without an explicit request.

## Test baseline

Last known full native baseline, recorded 2026-07-19:

- `python -m tests.runner --backend x86-64 --no-pyinbin-fallback -j 8`
- `python -m tests.runner -j 8`

Both are **454/461**, with the same seven known failures:

- `296_collections_namedtuple.py`
- `439`/`447` closures/nonlocal
- `440`/`63` str/tuple unpack
- `53_dynamic_import.py`
- `75_assembly_func.py` (legacy-only by design)

Zero regressions is the bar for sema, IR-lowering, or production-backend
changes. Current work is isolated to the experimental ARM64 package/runtime,
but both complete suites must still be re-run before ARM64 is registered as a
normal backend.

## ARM64 backend — Stage 1 in progress

### Stage 0 toolchain bring-up — DONE

WSL2 with GNU AArch64 binutils and QEMU assembled, linked, traced, and executed
a freestanding probe through the real `exit(42)` path. Outer Windows/WSL shell
exit-code propagation is unreliable; trust QEMU/strace.

### Encoder — DONE

`encoder.py` is independently checked bit-for-bit against GNU assembler output;
70/70 encodings match. This verification caught and fixed a real silent `cset`
destination-register bug.

### Register allocator — DONE

`regalloc.py` ports linear scan, loop liveness, Belady eviction, and call-crossing
allocation to AAPCS64. X13-X15 and V14-V15 are reserved for codegen scratch use.

### Code generator — IMPLEMENTED, FOCUSED-VERIFIED

`codegen.py` covers the current IR instruction surface with an AAPCS64 frame:
fixed `[FP, LR]` record, X29 frame pointer, negative spill/local offsets,
positive incoming-stack-argument offsets, and 16-byte SP alignment.

Verified details include correct SP materialization, X15-based negative stack
addressing, independent X13/X14 spill scratches, and byte-exact integer-add,
alloca/store/load, and spill cases assembled independently with real Clang
AArch64 tooling.

### ELF/object compiler — IMPLEMENTED, LINK-VERIFIED

`elf.py` emits little-endian ELF64 `ET_REL`, `EM_AARCH64=183`, with text/data/
rodata/tdata, RELA, symbols, and string tables. Supported relocations are:

- `R_AARCH64_CALL26` (283)
- `R_AARCH64_ADR_PREL_PG_HI21` (275)
- `R_AARCH64_ADD_ABS_LO12_NC` (277)

`module_codegen.py` compiles whole `IRModule` values and validates every IR op
before codegen so an unknown operation cannot silently become the development
NOP fallback. `elf_inspect.py` validates ARM64 objects and exposes defined and
undefined symbol inspection.

Independent `readelf` and LLVM `ld.lld` validation accepts generated objects;
disassembly confirms direct calls, global addressing, data resolution, and
valid ADRP+ADD relaxation.

### Source/object/executable APIs — EXPERIMENTAL, EXPLICIT

`source_build.py` runs real single-file source through lexer, parser, sema, IR
lowering, and ARM64 object generation.

`linux_link.py` provides native/cross tool discovery, assembly, relocatable
runtime merging, start-object generation, static linking, IR/source executable
building, and diagnostic-preserving failures. It checks program undefined
symbols before tool invocation and rejects source requiring runtime symbols not
in the current compatibility set.

`python -m asmpython._backends.arm64` provides deliberately separate
experimental commands:

- `object SOURCE [-o FILE]`
- `requirements SOURCE [--runtime/--no-runtime]`
- `build SOURCE [-o FILE] [--mode auto|native|cross]`

The package deliberately does **not** define `__module_backend__`; normal
`--backend arm64` registration remains blocked on broader runtime coverage and
full regression gates.

### Freestanding Linux runtime — MODULAR, ASSEMBLY/LINK-VERIFIED

Runtime sources are assembled separately and combined with `ld -r`:

- `abi_shims_linux_arm64.S`
  - `_abi_int_to_base`
  - `%s`/`%%` `printf` through Linux `write`
  - `strlen`
- `abi_strings_linux_arm64.S`
  - `labs`
  - `_abi_str_concat`
  - `_abi_str_concat_dup`
  - `_abi_str_eq`
  - `_abi_str_cmp`

Each allocation-producing slice uses distinct bump-allocated storage rather
than a shared static formatting buffer. The merged runtime object is inspected
after every build: every declared export must exist and the supposedly
freestanding object must have no unresolved symbols.

Independent Clang AArch64 assembly and `ld.lld -r` verification succeeded for
the string/scalar slice. Its cross-object `strlen` calls resolve in the merged
object, and the expected global exports are present.

Execution probes compile real source and require exact output:

- `_verify_source.py`: runtime-free `main() -> 42`
- `_verify_print.py`: `42\n-10|255!\n`, including multiple distinct integer
  conversions in one print call
- `_verify_scalars.py`: bool/None, `hex`/`oct`/`bin`, `abs(int)`, string
  concatenation, equality, inequality, and ordering

`tests/test_arm64_source_codegen.py` locks the scalar probe to its exact six
external symbols so lowering drift fails before assembler/link/execution stages.

### Float formatting — DELIBERATELY NOT FAKED

Float printing lowers to `_abi_float_to_str(F64) -> pointer`. The x86 runtime's
CPython-compatible implementation searches decimal precision and parses each
candidate back until it finds the shortest exact round-trip representation.
A fixed `%.17g`-style substitute would visibly misformat values such as `0.1`.
Therefore `_abi_float_to_str` remains absent from the ARM64 runtime allowlist;
float-printing source fails early with an explicit unsupported-symbol error
instead of linking to a knowingly incorrect formatter.

### Verification workflow

`.github/workflows/arm64-verify.yml` has two independent jobs:

- x86-64 Ubuntu with GNU cross-binutils and `qemu-aarch64 -strace`
- native `ubuntu-24.04-arm` with GNU binutils and `strace`

Both run encoder checks, object/codegen/CLI/link tests, real-source object tests,
and IR/source/print/scalar execution probes.

**Current execution boundary:** assembler correctness, object structure,
relocatable merging, and static linker acceptance are independently confirmed.
Push-triggered native/QEMU results are not exposed by the available GitHub
connector, so do not claim the new generated source/runtime probes executed
successfully until a workflow or WSL2 run is directly observed.

### Not yet done

- Normal driver/backend registration and project/module loading.
- Runtime coverage for float formatting, containers, exceptions, input, and most
  stdlib-facing ABI shims.
- ARM64 unwind/debug metadata.
- Windows ARM64 and macOS ARM64 object/link formats.
- Full x86-64 and legacy regression reruns after ARM64 becomes driver-visible.

**Next concrete step:** continue exact runtime expansion from symbols emitted by
real lowered source. Prefer self-contained semantics such as integer/string
conversion and parsing; keep difficult surfaces (float shortest-round-trip,
exceptions, containers) explicitly gated until a real implementation and
end-to-end probe exist.

## Known gaps / deferred work

- `296_collections_namedtuple.py`
- x86-64 closures/nonlocal (`439`/`447`) and str/tuple unpack (`440`/`63`)
- real memory management/refcounting
- stale self-hosting measurement
- macOS and bare-metal AArch64 targets
- full 3-way native/pyinbin/CPython conformance measurement
- x86-64 built-in linker support for real gcc/g++ bigobj COFF
- `asmpython.mlang` under `--backend legacy`

## Resume notes

- `docs/EXTENSIONS.md` is historical only.
- `docs/ABI.md` is the formal binary ABI reference.
- This may be a shared `beta` workspace; check coordination files and fresh
  blob SHAs before editing.
- `AGENTS.md` contains the cross-cutting rules and recurring bug classes.
