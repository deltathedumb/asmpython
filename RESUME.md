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
  SSA IR in `asmpython/_compiler/ir.py` (`IRModule`, `IRFunc`, `IRBlock`,
  `IRInstr`).
- `asmpython/_backends/x86_64/` is the default/reference native backend with
  its own encoder, register allocator, code generator, ELF/COFF writers, and
  built-in linkers.
- `asmpython/_backends/ternary/` is an experimental second IR backend and proof
  that the IR interface is genuinely pluggable.
- `asmpython/_backends/arm64/` is the active in-progress backend. Exact status
  is below.
- `--backend legacy` is the older NASM-text pipeline and remains necessary for
  `@assembly_func`; the x86-64 IR backend rejects that construct rather than
  silently miscompiling it.
- `asmpython/pyinbin/` is the fallback Python bytecode VM, not the native
  compiler.
- The compiler-syntax-extension system (`extend`/`retract`/`const`, `@final`,
  `@sealed`, `enum`, `interface`, etc.) is withdrawn. Its implementation is
  archived under `archived/extensions/` and must not be resurrected without an
  explicit user request.

## Test baseline

Last known full native baseline, recorded 2026-07-19:

- `python -m tests.runner --backend x86-64 --no-pyinbin-fallback -j 8`
- `python -m tests.runner -j 8`

Both are **454/461**, with the same seven known failures:

- `296_collections_namedtuple.py` — requires a real `namedtuple` rewrite.
- `439`/`447` — closures/nonlocal, x86-64-only gaps.
- `440`/`63` — str/tuple unpack, x86-64-only gaps.
- `53_dynamic_import.py` — requires an interpreter.
- `75_assembly_func.py` — legacy-only by design.

Zero regressions is the bar for changes touching sema, IR lowering, or a
production backend. The ARM64-only files at the current checkpoint are not
selected by either baseline command, but both complete suites still need to be
re-run before ARM64 is wired into normal driver dispatch.

## Recently shipped (2026-07-19)

- **Real PyPI installation:** `asmpython pypi install/uninstall/list` resolves
  PyPI JSON metadata, verifies sha256, installs pure-Python wheels, refuses
  native wheels and sdists, and wires project packages into pyinbin import
  roots. Verified end-to-end with `six`; correctly rejected `numpy` where no
  compatible pure-Python wheel exists.
- **SemaError exception-code coverage:** all 285 `raise SemaError(...)` sites
  carry an explicit `ErrorCode` mapping, with message text preserved and the
  native baselines unchanged.

## ARM64 backend — Stage 1 in progress

### Stage 0: toolchain bring-up — DONE

WSL2 with `gcc-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu`, and
`qemu-user` successfully assembled, linked, traced, and executed a freestanding
AArch64 ELF probe. The process reached the real `exit(42)`/`exit_group(42)`
path. Do not trust the outer Windows/WSL wrapper's exit code when it disagrees
with QEMU/strace.

### Encoder — DONE

`asmpython/_backends/arm64/encoder.py` is independently checked bit-for-bit
against GNU AArch64 assembler output by `_verify_encoder.py`; 70/70 encodings
match. This caught and fixed a real silent `cset` destination-register bug.

### Register allocator — DONE

`asmpython/_backends/arm64/regalloc.py` ports the architecture-neutral
linear-scan, loop-liveness, Belady eviction, and call-crossing logic to AAPCS64.
X13-X15 and V14-V15 are reserved for codegen scratch use and excluded from the
allocation pools.

### Code generator — IMPLEMENTED AND FOCUSED-VERIFIED

`asmpython/_backends/arm64/codegen.py` covers the current IR instruction
surface. Its frame layout follows AAPCS64: a fixed `[FP, LR]` record, X29 as the
frame pointer, negative offsets for spills/locals, positive offsets for incoming
stack arguments, and 16-byte SP alignment.

Important verified details:

- SP moves use `ADD #0`, not ORR's register-move alias (register encoding 31 is
  XZR under ORR, not SP).
- Negative FP-relative spills/allocas materialize their address through reserved
  X15 before an offset-zero LDR/STR.
- X13/X14 remain independent operand/result scratch registers, preserving the
  two-spilled-operands collision fix.
- `tests/test_arm64_codegen.py` contains byte-exact cases independently
  assembled with real Clang AArch64 tooling: integer add, alloca/store/load,
  and a negative spill round-trip.

### ELF relocatable writer and module compiler — IMPLEMENTED, LINK-VERIFIED

`asmpython/_backends/arm64/elf.py` emits little-endian ELF64 `ET_REL` objects
with `EM_AARCH64=183`, `.text`, `.data`, `.rodata`, `.tdata`, `.rela.text`,
`.symtab`, `.strtab`, and `.shstrtab`. It supports the three relocations emitted
by codegen:

- `R_AARCH64_CALL26` (283)
- `R_AARCH64_ADR_PREL_PG_HI21` (275)
- `R_AARCH64_ADD_ABS_LO12_NC` (277)

`module_codegen.py` compiles a complete `IRModule` to `output.o`, validates the
entire IR operation set before codegen, and rejects unknown operations with
function/block/instruction context rather than allowing codegen's development
NOP fallback to become a silent miscompile. Linux/AAPCS64 is the only accepted
object target. The package deliberately does not define `__module_backend__`
until normal runtime/link support exists.

Focused coverage:

- `tests/test_arm64_elf.py` validates headers, sections, symbols, relocation
  records, and unknown-relocation rejection.
- `tests/test_arm64_module_codegen.py` validates whole-module object emission,
  target/ABI rejection, unknown-op refusal, and that ARM64 is not yet advertised
  as a normal driver backend.
- Independent local `readelf` + LLVM `ld.lld` validation accepted the generated
  layout and linked it into an AArch64 executable. Disassembly confirms
  `_start -> caller -> load_answer`, correct `answer` data resolution, and valid
  ADRP+ADD linker relaxation.

### Real source path — IMPLEMENTED, EXECUTION-PROBED IN CI

`_verify_source.py` exercises the actual front end:

`source -> lexer -> parser -> sema -> ir_lower -> ARM64 ET_REL -> ld -> execute`

Its runtime-free source defines `main() -> int` returning `40 + 2`. The
reachability walker explicitly roots `main`, and the resulting object is checked
to have no text relocations/undefined runtime dependencies. Fast object tests
live in `tests/test_arm64_source_codegen.py`.

### First freestanding runtime slice — IMPLEMENTED, ASSEMBLY/LINK-VERIFIED

`asmpython/_runtime/abi_shims_linux_arm64.S` exports the first symbols required
by real source-level output:

- `_abi_int_to_base(value, base, prefix)`
- `printf(format, ...)` supporting literal bytes, `%%`, and `%s`
- `strlen(text)`

It has no libc dependency. Integer conversions receive distinct 128-byte blocks
from a 1 MiB bump arena; this deliberately avoids the recurring shared-static-
buffer aliasing bug where multiple formatted arguments overwrite one another
before a single print call. Sign precedes prefix (`-0xa`), bases 2..36 are
handled, and magnitude division is unsigned so INT64_MIN is representable.
`printf` handles x1-x7 variadic pointers plus further stack-passed arguments and
writes through the Linux AArch64 `write` syscall.

Independent local verification with Clang's real AArch64 assembler produced a
valid `EM_AARCH64` object. `readelf` recognized its symbols/relocations, and
LLVM `ld.lld` linked a static AArch64 probe with no unresolved symbols.
Disassembly confirms the expected allocator, conversion, formatter, and syscall
sequences.

`_verify_print.py` compiles real source with two print calls, including
`print(-10, 255, sep="|", end="!\\n")`, links the freestanding runtime, and
requires exact stdout `42\n-10|255!\n`. The multi-argument call is intentional:
a shared conversion buffer would fail this test.

### Execution verification

`.github/workflows/arm64-verify.yml` now has two independent jobs:

- x86-64 Ubuntu with GNU cross-binutils + `qemu-aarch64 -strace`
- native `ubuntu-24.04-arm` with GNU binutils + `strace`

Both run encoder verification, byte-exact codegen tests, ELF/module/source object
tests, IR execution, real source execution, and the print-runtime stdout probe.

**Current verification boundary:** assembler correctness, ELF/object structure,
and static linker acceptance are locally confirmed. Native/QEMU execution jobs
are committed and triggered, but push-run results are not exposed by the
available GitHub connector. Do not claim these new generated source/runtime
probes executed successfully until their workflow or a WSL2 run is observed.

### Not yet done

- A reusable ARM64 runtime-object builder integrated with compiler build paths.
- Driver/backend integration for a normal `--backend arm64` source build.
- Runtime coverage beyond integer conversion/basic `%s` print/strlen.
- Richer object metadata such as AArch64 unwind/debug sections.
- Windows ARM64 and macOS ARM64 object/link formats.

**Next concrete step:** make the ARM64 runtime slice buildable through a normal
Python helper and add an object/link API that can produce a Linux AArch64
executable without duplicating verifier logic. Keep it experimental and do not
advertise `__module_backend__` until unresolved runtime-symbol checking and the
full x86-64/legacy regression baselines are green.

## Known gaps / deferred work

- `296_collections_namedtuple.py` needs a real native-friendly `namedtuple`
  implementation.
- x86-64 closures/nonlocal (`439`/`447`) and str/tuple unpack (`440`/`63`) gaps.
- Real memory management/refcounting; all current native allocations leak.
- Self-hosting status is stale and must be re-measured against the current
  architecture.
- macOS x64/ARM64 support.
- Bare-metal Raspberry Pi AArch64 support.
- Full 3-way native/pyinbin/CPython conformance measurement.
- x86-64 built-in linker support for real gcc/g++ bigobj COFF output.
- `asmpython.mlang` support under `--backend legacy`.

## Resume notes

- `docs/EXTENSIONS.md` is historical documentation for the withdrawn extension
  system.
- `docs/ABI.md` is the current formal binary ABI reference.
- This can be a shared `beta` workspace. Check `devthread.txt`, branch history,
  and file SHAs before editing.
- `AGENTS.md` contains the cross-cutting process rules and recurring bug classes.
