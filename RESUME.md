# 3.14 Resume

Short, current-state-only. Full history lives in `git log` — this file is a
pointer to *where things stand right now*, not a session journal. (Previous
version of this file grew to 3000+ lines of chronological journal entries;
replaced 2026-07-19 with this compact form. Recover the old version via
`git log -p -- RESUME.md` if archaeology is ever needed.)

## Directive

Versioned **3.14.0** (matches CPython's own version number), not 2.0.0 —
decided because ARM64 support needs codegen restructured around a
target-neutral IR rather than a parallel target subclass, which is a real
architecture change even though the Python-level language surface is
unchanged. See `roadmap.md` for the full 12-point 3.14.0 definition:
target-neutral architecture; IR backend as the default; Windows/Linux/
macOS/ARM64 support; native-first compilation with pyinbin fallback; a
stable public ABI; real memory management; deterministic self-hosting;
broad Python 3.14 language/stdlib compatibility; real PyPI package
installation; no CPython dependency in produced apps; reproducible/
inspectable builds; a 3-way (native/pyinbin/CPython) conformance suite.

Standing instruction: work continuously through this list without
stopping for check-ins between items ("NO STOP AT ALL" push) — checkpoint
via commits + this file, not by pausing. Use only 1-2 subagents at a time
if delegating (past sessions had 5 simultaneous background agents all
crash from hitting a session API usage limit). Tests take a long time —
always launch via the harness's background-execution mechanism, never
synchronously.

## Architecture (current)

- `asmpython/_compiler/ir_lower.py` lowers the checked AST to a
  target-neutral SSA IR (`asmpython/_compiler/ir.py` — `IRModule`/
  `IRFunc`/`IRBlock`/`IRInstr`, genuinely ISA-agnostic, confirmed no x86
  register names anywhere in it). Real, complete op vocabulary: 39 ops
  (confirmed via grep against `ir_lower.py`'s own `IRInstr(...)` call
  sites) — `alloca br br.t call const fadd fcmp.{eq,gt,ne} fdiv fmul fneg
  fptosi fsub gep global_addr iadd iand icmp.{eq,ge,gt,lt,ne} idiv imul
  ineg inot ior irem isub ixor load ret sext shr sitofp store zext`.
- `asmpython/_backends/x86_64/` is the reference ISA backend and the
  **default** backend (`--backend x86-64`): own `encoder.py` (raw
  instruction encoding), `regalloc.py` (linear-scan, Belady eviction),
  `codegen.py` (IR op → machine code), `elf.py`/`elf_linker.py` (Linux),
  `coff.py`/`pe_linker.py` (Windows) — no NASM/gcc dependency at all under
  this backend.
- `asmpython/_backends/ternary/` — a second, much smaller IR backend
  (experimental ternary VM target), proof that the IR is genuinely
  pluggable.
- `asmpython/_backends/arm64/` — **in progress**, see "ARM64 backend"
  below.
- `--backend legacy` — the older NASM-text-emission pipeline
  (`asmpython/_compiler/codegen.py`, top-level, distinct from the
  per-backend `codegen.py` files above). Kept alive specifically for
  `@assembly_func` inline-NASM support, which `--backend x86-64`
  deliberately refuses to build (was a silent miscompile before a
  `driver.py` guard was added; see `docs/ABI.md`).
- `asmpython/pyinbin/` — a from-scratch Python bytecode VM (not the
  native compiler), used as a fallback so arbitrary/stdlib Python can run
  without every language feature being natively compilable yet. Own
  object model (`PyClass`/`PyInstance`), no real CPython metaclass
  machinery — see [[feedback_pyinbin_object_model_gotchas]] in memory for
  recurring bug patterns there.
- The compiler-syntax-extension system (`extend`/`retract`/`const`,
  `@final`/`@sealed`/`enum`/`interface`/etc.) was **withdrawn** — decided
  it diverged too far from "asmpython mirrors CPython with only tiny
  differences." Real implementation archived under `archived/extensions/`
  for reference; `asmpython/_compiler/extensions.py` is now an inert
  stub. Do not resurrect without an explicit user request.

## Test baseline

`python -m tests.runner --backend x86-64 --no-pyinbin-fallback -j 8`
(default backend) and `python -m tests.runner -j 8` (legacy backend) —
both **454/461** as of 2026-07-19, same 7 known/understood failures on
both:

- `296_collections_namedtuple.py` — needs a `namedtuple` rewrite (uses
  dynamic `type()`/`property(lambda...)`, real dynamic-typing features).
- `439`/`447` — closures/nonlocal, x86-64-backend-only gaps.
- `440`/`63` — str/tuple unpack, x86-64-backend-only gaps.
- `53_dynamic_import.py` — architecturally impossible to compile natively
  (needs a real interpreter).
- `75_assembly_func.py` — legacy-only by explicit design (see above).

Zero regressions is the bar for any change touching `sema.py`/
`ir_lower.py`/either backend — re-run both suites and diff against
454/461, not just "did it error."

## Recently shipped (this session, 2026-07-19)

- **`asmpython pypi install/uninstall/list`** (commit `b3dc5258`) — real
  PyPI package installation. `asmpython/_compiler/pypi.py`: resolves
  against PyPI's public JSON API, sha256-verifies every download, only
  installs pure-Python wheels (a wheel with a compiled `.pyd`/`.so`/
  `.dylib` is refused, naming the member), no sdist builds (no arbitrary
  code execution during install), no transitive dependency resolution.
  New `pypi_packages`/`pypi_dir` project.json fields wire installed
  packages into the existing `pyinbin_fallback()` path as import roots.
  Verified end-to-end against real PyPI (`six` install/list/uninstall,
  project.json batch install, correct rejection of `numpy` for having no
  pure-Python wheel).
- **SemaError CPython exception-code coverage: 285/285** (commit
  `e25af445`) — every `raise SemaError(...)` site in `sema.py` now
  carries a `code=ErrorCode.*` argument. 12 new codes added (E141-E152:
  mlang FFI argument-shape errors, 3 internal "unhandled AST node"
  invariant checks, dict/tuple/set element-type coverage gaps,
  interpreter-only-builtin calls, str.rsplit/str.format shape errors).
  Message text unchanged throughout. Verified zero regressions.
- **ARM64 backend, Stage 0 + Stage 1 in progress** — see next section.

## ARM64 backend — in progress

Real toolchain-verified progress, not a design doc. See `roadmap.md`'s
"ARM64 support" section for the fullest detail.

**Stage 0 (toolchain bring-up) — DONE, verified 2026-07-19.** WSL2 (not
WSL1 — QEMU user-mode's `guest_base` address-space reservation reliably
fails under WSL1's syscall-translation layer; the WSL distro had to be
converted via `wsl --set-version Ubuntu 2`, which itself needed
virtualization enabled in host firmware/BIOS first — a one-time
physical/BIOS change, not fixable from software alone). Installed
`gcc-aarch64-linux-gnu`/`binutils-aarch64-linux-gnu`/`qemu-user`. A
hand-assembled AArch64 ELF executable (raw `write`/`exit` syscalls, no
libc) assembled+linked with the real cross-toolchain and executed
correctly under `qemu-aarch64`, confirmed via `strace` showing
`exit_group(42)` and `+++ exited with 42 +++` (note: `wsl.exe`'s own exit
code does NOT propagate correctly through the Bash-tool/PowerShell
boundary — a harness quirk, not a real bug; always trust `strace`/program
output over the outer shell's `$?` when verifying ARM64 execution).

**Stage 1 (real backend implementation) — in progress:**

- `asmpython/_backends/arm64/encoder.py` — **DONE**, committed
  (`0817fe97`, `7b5668d2`). Every encoding verified bit-for-bit against
  real `aarch64-linux-gnu-as` output via
  `asmpython/_backends/arm64/_verify_encoder.py` (run inside WSL2). 70/70
  encodings match. Caught and fixed one real bug this way (`cset`'s Rd
  field was OR'd with a stray `Reg.XZR` constant instead of the actual
  destination register — would have silently corrupted every `CSET`
  emission). Covers: ADD/SUB/AND/ORR/EOR/MUL/SDIV/UDIV (register+immediate
  forms), MOVZ/MOVK 64-bit constant materialization, LDR/STR (X/W/D
  forms), LDP/STP with pre/post-indexed writeback, branches (B/BL/BLR/BR/
  RET/B.cond/CBZ/CBNZ), ADRP/ADR PC-relative addressing, CSEL/CSET,
  SVC/BRK/NOP, and scalar FP (FADD/FSUB/FMUL/FDIV/FNEG/FABS/FSQRT/FMOV/
  FCMP/SCVTF/FCVTZS) plus LSL#12 shifted-immediate ADD/SUB for large stack
  frames.
- `asmpython/_backends/arm64/regalloc.py` — **DONE**, committed
  (`92b074e8`). Ported from `_backends/x86_64/regalloc.py`: the
  linear-scan algorithm, loop-liveness extension (`_last_uses`), Belady
  eviction (`_pick_evict`), and call-crossing analysis
  (`_compute_crosses_call`) are architecture-generic and carried over
  unchanged; only register-pool/ABI constants differ (AAPCS64's single
  shared int/FP argument-register assignment — no SysV/Win64 split to
  replicate, no RCX/CL-pinned-shift hazard to guard against since
  AArch64's shift instructions take the count as an ordinary register
  operand). Smoke-tested against real `IRFunc` values (simple add,
  call-crossing value, 30-value register-pressure spill case) — all
  behaved correctly, including matching the x86-64 allocator's own
  behavior on an identical call-crossing test case.
- `asmpython/_backends/arm64/codegen.py` — **WRITTEN, NOT YET VERIFIED
  OR COMMITTED.** This is the file open in-progress when this resume was
  last written. Ported from `_backends/x86_64/codegen.py`'s full `_instr`
  dispatch, `_call` ABI marshaling, prologue/epilogue. AArch64's 3-operand
  encoding removes several x86-64-specific hazards entirely (no
  dst-must-equal-an-operand constraint, no RAX:RDX-pinned division, no
  RCX/CL-pinned shift) — ported code reflects that, it is NOT a 1:1
  structural mirror. **Before trusting this file**: (1) run
  `python -c "from asmpython._backends.arm64 import codegen"` to confirm
  it imports without error, (2) verify the scratch-register reservations
  (`_SCRATCH`/`_SCRATCH2`/`_SCRATCH3` = X13/X14/X15, `_SCRATCH_FP`/
  `_SCRATCH_FP2` = V14/V15) are excluded from `regalloc.py`'s
  `_GP_POOL`/`_FP_POOL` (last confirmed true for X13-X15 before this note
  was written, via `python -c "from asmpython._backends.arm64 import
  regalloc; print(13 in [int(r) for r in regalloc._GP_POOL])"` → `False`
  — re-verify V14/V15 similarly, not yet done), (3) build a minimal
  end-to-end test: construct a trivial `IRFunc` (e.g. `a+b` returning an
  int), run it through `regalloc.allocate()` → `codegen.compile_func()`,
  and inspect/hand-verify the resulting bytes — no real object-file
  emission (`elf.py` equivalent) or execution test has been done for this
  file yet.
- **Not started**: AArch64 ELF relocation support (`EM_AARCH64`=183,
  `R_AARCH64_*` relocation types — different PC-relative addressing than
  x86's RIP-relative LEA; AArch64 uses the ADRP+ADD page-relative
  two-instruction sequence `codegen.py`'s `global_addr` case already
  emits, each half needing its own relocation:
  `R_AARCH64_ADR_PREL_PG_HI21`=275 for ADRP,
  `R_AARCH64_ADD_ABS_LO12_NC`=277 for ADD — these constants are already
  defined at the top of the new `codegen.py`). AArch64 ports of the
  runtime object and ABI shims (`asmpython/_runtime/build.py` currently
  assumes NASM/x86-64 throughout — this is comparable in size to the
  encoder/regalloc/codegen work itself, not a small addendum). Not wired
  into `driver.py`'s `--backend` dispatch at all yet — `__init__.py` says
  so explicitly; there is nothing here yet that can compile a real
  program end-to-end.

**Next concrete step**: verify `codegen.py` per the checklist above, then
write a minimal `elf.py`-equivalent (even a stub that only handles the
relocation types `codegen.py` actually produces) to get one trivial
function all the way to a real, linkable `.o` file — that's the next real
correctness checkpoint (mirrors how Stage 0's hand-assembled probe program
proved the toolchain; a hand-linked-and-run trivial function proves this
codegen).

## Known gaps / deferred work (not currently being worked)

- **`296_collections_namedtuple.py`** needs a real `namedtuple` rewrite
  (current implementation uses `property(lambda...)` and 3-arg dynamic
  `type()`, which the native compiler can't give meaning to).
- **x86-64 backend closures/nonlocal gaps** (`439`/`447`) and
  **str/tuple unpack gaps** (`440`/`63`) — x86-64-backend-only, legacy
  backend handles these correctly.
- **Real memory management** (refcounting) — approved design exists
  (non-cyclic-first staged rollout), not started. All allocations
  currently leak (fine for short-lived scripts, not for long-running
  processes).
- **Self-hosting** (asmpython compiling itself) — was blocked on an
  argparse API gap; treat that as STALE, architecture has moved on
  significantly since — re-verify current status before trusting any old
  note about this.
- **macOS support** (`--target macos-x64`/`macos-arm64`) — not started,
  gated behind the ARM64 IR work landing first for the Apple Silicon half.
- **Bare-metal Raspberry Pi (AArch64)** — gated entirely behind the
  ARM64 backend above; none of `target_freestanding.py`'s x86 boot code
  (Multiboot1, VGA text, BIOS `INT 13h`) is reusable.
- **3-way conformance suite** (native vs. pyinbin vs. real CPython) —
  `tests/cpython_conformance.py` exists and runs CPython's own `Lib/test`
  suite through pyinbin; treat any cached pass-count from an old memory
  as stale, re-run for current numbers.
- **x86-64 builtin linker can't read real gcc/g++ output** — needs a
  bigobj-format COFF parser (`coff_parse.py` is scoped to NASM-produced
  objects only); `--linker gcc` is unaffected and remains the default
  whenever real external-compiler objects (e.g. from `asmpython.mlang`)
  are in play.
- **`asmpython.mlang` has no `--backend legacy` support** — only works
  under `--backend x86-64` today; deferred as lower priority than x86-64
  parity work.

## Notes for whoever resumes this

- `docs/EXTENSIONS.md` describes the now-withdrawn extension system —
  historical reference only (see "Architecture" above).
- `docs/ABI.md` is the first formal, versioned ABI spec (`@assembly_func`
  calling convention, exact runtime type layouts — corrects a stale
  `about.md` claim that `dict`'s header was 32 bytes; it's actually 40).
- Multi-agent shared workspace: this project is sometimes worked by more
  than one agent concurrently against the same git branch (`beta`). Check
  for a `devthread.txt` or similar coordination file before assuming a
  clean tree is yours alone.
- See `AGENTS.md` (repo root) for cross-cutting operational guidance —
  recurring bug classes, testing conventions, and process rules that
  apply regardless of which specific task you're picking up.
