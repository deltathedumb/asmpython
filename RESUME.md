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

`runtime_manifest.py` is the single source of truth for runtime source order and
symbol ownership. Before assembly, every source file must exist in manifest
order and its `.global`/`.globl` declarations must exactly match its owned
symbols. After `ld -r`, the merged ELF is independently checked again for every
declared export and for zero unresolved symbols.

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
  - `_abi_str_concat` / `_abi_str_concat_dup`
  - `_abi_str_repeat`
  - `_abi_str_eq` / `_abi_str_cmp`
  - `_abi_hash_string` (the existing 64-bit FNV-1a contract)
  - `_abi_str_removeprefix` / `_abi_str_removesuffix`
- `abi_string_search_linux_arm64.S`
  - `_abi_str_starts_with`
  - `_abi_str_ends_with`
  - `_abi_str_count`

Each allocation-producing slice uses distinct bump-allocated storage rather
than a shared static formatting buffer. The string-repetition path checks
multiplication/addition overflow and treats zero/negative counts as empty.
Prefix/suffix removal handles matching, non-matching, empty, and UTF-8 affixes
without splitting a valid encoded code point.

Independent Clang AArch64 assembly and `ld.lld -r` verification succeeded for
all slices. Cross-slice `strlen`, prefix, and suffix calls resolve in the merged
object. Disassembly confirms the FNV-1a constants/loop and the remover AAPCS64
frames/calls. The search slice counts empty substrings by Unicode code-point
count plus one, not raw byte count plus one.

Execution probes compile real source and require exact output:

- `_verify_source.py`: runtime-free `main() -> 42`
- `_verify_print.py`: `42\n-10|255!\n`, including multiple distinct integer
  conversions in one print call
- `_verify_scalars.py`: bool/None, `hex`/`oct`/`bin`, `abs(int)`, string
  concatenation, equality, inequality, and ordering
- `_verify_string_search.py`: `startswith`, `endswith`, non-overlapping `count`,
  ASCII empty-substring count, and UTF-8 empty-substring count (`"éé"` -> 3)
- `_verify_string_repeat.py`: both operand orders, UTF-8 repetition, and zero/
  negative counts
- `_verify_hash.py`: ASCII and UTF-8 vectors locked to the reference FNV-1a
  implementation
- `_verify_string_remove.py`: matching, non-matching, empty, and UTF-8 prefix/
  suffix removal

Focused tests lock every probe to its exact external-symbol set so lowering
drift fails before assembler/link/execution stages. Runtime-manifest tests also
reject source ordering and public-symbol ownership drift before tool discovery.

### Float formatting — DELIBERATELY NOT FAKED

Float printing lowers to `_abi_float_to_str(F64) -> pointer`. The x86 runtime's
CPython-compatible implementation searches decimal precision and parses each
candidate back until it finds the shortest exact round-trip representation.
A fixed `%.17g`-style substitute would visibly misformat values such as `0.1`.
Therefore `_abi_float_to_str` remains absent from the ARM64 runtime allowlist;
float-printing source fails early with an explicit unsupported-symbol error
instead of linking to a knowingly incorrect formatter.

### Integer parsing — DEFERRED UNTIL EXCEPTIONS

`int(text)` and `int(text, base)` lower to `_abi_str_to_int` and
`_abi_str_to_int_base`. Their valid-input loops are straightforward, but invalid
input must raise catchable `ValueError`. A success-only parser would silently
widen compatibility with wrong failure semantics, so both symbols remain gated
until the ARM64 exception runtime exists.

### Verification workflow

`.github/workflows/arm64-verify.yml` has two independent jobs:

- x86-64 Ubuntu with GNU cross-binutils and `qemu-aarch64 -strace`
- native `ubuntu-24.04-arm` with GNU binutils and `strace`

Both run encoder checks, object/codegen/CLI/link tests, real-source object tests,
and IR/source/print/scalar/search/repeat/hash/remove execution probes.

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

**Next concrete step:** continue exact non-raising runtime expansion from symbols
emitted by real lowered source. Keep float shortest-round-trip, exception-
dependent parsing/indexing, containers, and other failure-sensitive surfaces
explicitly gated until their real semantics and end-to-end probes exist.

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
