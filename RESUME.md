# 3.14 Resume

Current-state checkpoint only. Full history lives in `git log`.

## Directive

Versioned **3.14.0** (matching CPython), not 2.0.0. ARM64 requires a
target-neutral compiler architecture rather than a parallel target subclass, so
this is a real architecture release even where the Python surface is unchanged.
See `roadmap.md` for the complete release definition.

Standing instruction: work continuously through the punch list without pausing
for routine check-ins. Checkpoint through commits and this file. Use no more than
1-2 subagents at once. Long suites must use a background execution mechanism
rather than blocking synchronously.

## Architecture

- `asmpython/_compiler/ir_lower.py` lowers checked AST into target-neutral SSA IR
  in `asmpython/_compiler/ir.py`.
- `asmpython/_backends/x86_64/` remains the default/reference native backend.
- `asmpython/_backends/ternary/` is the experimental second IR backend.
- `asmpython/_backends/arm64/` is the active in-progress backend.
- `--backend legacy` remains the NASM-text path required for `@assembly_func`.
- `asmpython/pyinbin/` remains the fallback Python bytecode VM.
- Compiler syntax extensions remain withdrawn under `archived/extensions/`.

## Native test baseline

Last full baseline recorded 2026-07-19:

- `python -m tests.runner --backend x86-64 --no-pyinbin-fallback -j 8`
- `python -m tests.runner -j 8`

Both were **454/461**, with the same seven known failures:

- `296_collections_namedtuple.py`
- `439`/`447` closures/nonlocal
- `440`/`63` str/tuple unpack
- `53_dynamic_import.py`
- `75_assembly_func.py` (legacy-only by design)

Zero regressions remains the bar for shared compiler or production-backend
changes. Current ARM64 work is isolated, but the complete suites must be rerun
before ARM64 becomes driver-visible.

## ARM64 Stage 1

### Toolchain, encoder, allocator, codegen, ELF — DONE / FOCUSED-VERIFIED

- WSL2 GNU AArch64 binutils and QEMU assembled, linked, traced, and executed the
  real syscall path.
- `encoder.py` is checked bit-for-bit against GNU assembler: **70/70** encodings
  match. This caught and fixed a silent `cset` destination-register bug.
- `regalloc.py` implements AAPCS64 linear scan, loop liveness, Belady eviction,
  and call-crossing allocation. X13-X15 and V14-V15 are reserved scratches.
- `codegen.py` uses a correct AAPCS64 frame: fixed FP/LR record, X29 frame
  pointer, negative spill/local offsets, positive incoming stack arguments, and
  16-byte SP alignment.
- `elf.py` emits little-endian ELF64 `ET_REL`, `EM_AARCH64=183`, text/data/
  rodata/tdata, RELA, symbols, and string tables.
- Supported relocations: `R_AARCH64_CALL26`, `R_AARCH64_ADR_PREL_PG_HI21`, and
  `R_AARCH64_ADD_ABS_LO12_NC`.
- `module_codegen.py` validates every IR operation before codegen.
- `elf_inspect.py` validates objects and exposes defined/undefined symbols.

Independent assembler, disassembler, `readelf`, and relocatable-link checks
confirm calls, globals, ADRP+ADD relaxation, frames, and symbol resolution.

### Explicit source/object/executable API

`source_build.py` runs single-file source through lexer, parser, sema, IR
lowering, and ARM64 object generation.

`linux_link.py` provides native/cross tool discovery, assembly, relocatable
runtime merging, start-object generation, static linking, source/IR executable
building, and diagnostic-preserving failures. Program undefined symbols are
checked before external tool invocation.

`runtime_manifest.py` is the single source of truth for runtime source order and
per-file symbol ownership. Before assembly, every file must exist in manifest
order and its `.global`/`.globl` declarations must exactly match its exports.
After `ld -r`, the merged ELF is independently checked for all declared exports
and zero unresolved symbols.

Experimental CLI commands:

- `python -m asmpython._backends.arm64 object SOURCE [-o FILE]`
- `python -m asmpython._backends.arm64 requirements SOURCE [--runtime|--no-runtime]`
- `python -m asmpython._backends.arm64 build SOURCE [-o FILE] [--mode auto|native|cross]`

The package deliberately does **not** define `__module_backend__`. There is no
normal `--backend arm64` dispatch yet. Keep this gate until the platform claim
is deliberately expanded and full regression gates are rerun.

## Freestanding Linux runtime

Every slice is separately assembled and manifest-audited before `ld -r`.

### Core and strings

- `abi_shims_linux_arm64.S`
  - `_abi_int_to_base`
  - `%s`/`%%` `printf` through Linux `write`
  - `strlen`
- `abi_strings_linux_arm64.S`
  - `labs`
  - concat/dup/repeat/equality/comparison
  - deterministic 64-bit FNV-1a hashing
  - `removeprefix` / `removesuffix`
- `abi_string_search_linux_arm64.S`
  - startswith/endswith/non-overlapping count
  - find/rfind and start-index find using Unicode code-point indices
- `abi_string_replace_linux_arm64.S`
  - replacement including empty-old insertion at Unicode code-point gaps
- `abi_string_slice_linux_arm64.S`
  - plain slices with Python negative/clamped code-point bounds
- `abi_string_padding_linux_arm64.S`
  - `zfill` with sign placement and Unicode character width

Allocation-producing slices use distinct bump storage, not a shared formatting
buffer. Repetition checks multiplication/addition overflow. UTF-8 operations do
not split complete code points.

### Exact scalar float helpers

- `abi_float_scalar_linux_arm64.S`
  - `fabs`
  - `copysign`
  - `nearbyint` using the current FPCR rounding mode
  - `ceil`, `floor`, and `trunc`
  - `fdim`
  - `nextafter`
- `abi_float_classify_linux_arm64.S`
  - `_math_isnan`, `_math_isinf`, `_math_isfinite`
- `abi_float_angles_linux_arm64.S`
  - `_math_degrees` and `_math_radians` using the established x86 binary64
    constants and the same single-multiply operation
- `abi_float_modf_linux_arm64.S`
  - exact fractional and integral components using exponent masking
  - signed-zero, infinity, NaN, and payload handling
- `abi_float_frexp_linux_arm64.S`
  - exact mantissa/exponent components
  - CLZ-based subnormal normalization, signed zero, infinity, and NaN handling

`math.nextafter` is now a real source binding. The previous C `round` export and
`math.round` probe were removed because CPython has no `math.round`; Python's
rounding API remains the builtin `round` with its own semantics.

These helpers expand useful float computation without enabling float printing.
Float formatting remains separately gated.

### Exact int64 helpers

- `abi_int_math_linux_arm64.S`
  - `_math_gcd`: non-negative Euclidean reduction
  - `_math_lcm`: zero-aware, divide-before-multiply int64 contract

This mirrors the existing fixed-width native runtime contract; it does not claim
CPython arbitrary-precision behavior outside signed-int64 range.

## Verification probes

Each new runtime symbol has a real-source probe with:

1. exact lowered undefined-symbol requirements,
2. pre-link runtime compatibility validation,
3. static freestanding executable construction,
4. exact expected stdout/exit behavior,
5. native and QEMU workflow entries.

Focused tests auto-discover `tests/test_arm64_*.py`. Independent reference models
currently include:

- UTF-8 find/rfind, replacement, slicing, and zfill,
- FNV-1a vectors,
- exact sign/payload float bit operations,
- IEEE-754 classification,
- angle factors and operation order,
- ties-to-even nearbyint vectors,
- exact nextafter one-ULP stepping across 20,000 random pairs,
- thousands of gcd/lcm comparisons in safe int64 range,
- 4,000 finite `modf` bit patterns plus nonfinite cases,
- 10,000 `frexp` bit patterns including subnormals and nonfinite values.

### Directly observed execution boundary

Independent WSL2 verification on 2026-07-19 directly observed:

- encoder verification: 70/70 encodings matched GNU assembler,
- the then-current focused suite: 39/39 tests passed,
- `_verify_elf`, `_verify_source`, `_verify_print`, `_verify_scalars`, and
  `_verify_string_search` completed through real `qemu-aarch64`,
- exact exit/stdout behavior was visible through real Linux syscalls under
  `strace`, including distinct formatting buffers and UTF-8 count behavior.

All later probes have independently verified source lowering surfaces, assembly,
disassembly, relocatable links, static links, and reference models, with native
and QEMU jobs committed. Do **not** claim those later probes executed until a
later workflow or WSL2 observation records them.

## Deliberate gates

### Float formatting — NOT FAKED

`_abi_float_to_str` must produce CPython's shortest exact round-trip decimal.
A fixed `%.17g` substitute is visibly wrong for common values, so the symbol
remains outside the ARM64 allowlist and float-printing source fails early.

### Exceptions and failure-sensitive APIs

`int(text)`, `int(text, base)`, string `index`/`rindex`, invalid fill-character
handling, and other APIs whose failures must raise catchable exceptions remain
gated until the ARM64 exception runtime exists. Success-only substitutes are not
acceptable.

### Containers and platform integration

Still not done:

- list/dict/set runtime coverage,
- input and most stdlib-facing ABI shims,
- normal driver/backend registration and project/module loading,
- unwind/debug metadata,
- Windows ARM64 and macOS ARM64 object/link formats,
- full x86-64 and legacy regression reruns after ARM64 becomes visible.

**Next concrete step:** continue exact non-raising runtime expansion from symbols
emitted by real lowered source. Keep exception-sensitive, arbitrary-precision,
container, and shortest-round-trip formatting surfaces explicitly gated.

## Broader known gaps

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
- This may be a shared `beta` workspace; check fresh blob SHAs before editing.
- `AGENTS.md`, `AGENT_INSTRUCTIONS.md`, and `SESSION_SUMMARY_2026_07_19.md`
  contain cross-cutting rules and the independent verification handoff.
