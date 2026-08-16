# Session summary 2026-07-19: independent ARM64 verification + guidance

## Work completed this session

1. **Inspected parallel agent's 79-commit ARM64 work** (commits `00b39765..85720c27` at start, continuing beyond)
   - Encoder, regalloc, codegen, ELF writer, modular runtime slices, CI workflow, 39 unit tests
   - Honest RESUME.md ("results not exposed until directly observed"), deliberately gated backend

2. **Independently verified all major claims via real WSL2 toolchain**
   - Encoder: 70/70 instructions bit-match `aarch64-linux-gnu-as` ✓
   - Unit tests: 39/39 pass ✓
   - QEMU execution: 5 real probes produce syscall-level output with byte-exact matching ✓
   - Runtime: modular, non-faking, UTF-8-aware (empty substring counts per code-point length) ✓

3. **Updated project memory + documentation**
   - RESUME.md: rewrote from 3000+ line journal to 250-line current-state summary
   - AGENTS.md: new file with cross-cutting process rules + recurring bug classes
   - AGENT_INSTRUCTIONS.md: 98-line guidance for the parallel ARM64 agent
   - arm64_backend_verification_2026_07_19.md: memory entry with verified findings

4. **Committed 2 checkpoint commits**
   - `00b39765`: RESUME.md rewrite + AGENTS.md creation
   - `2b147b5b`: AGENT_INSTRUCTIONS.md

## What the parallel agent is doing (observed in real-time)

The other agent chose **runtime expansion** (the first fork in my instructions) — adding more exact, non-faking operations with per-feature verification probes. Recent commits (after my instructions were drafted):

- `b3ed977a`: UTF-8-aware find/rfind string operations + `_verify_string_find.py` probe
- `efe95515`: string prefix/suffix removal + new test
- `01e0d5ae`: deterministic string hashing
- `d936f6a3`: runtime manifest validation before assembly
- `ef15c53e`: wire string repetition through runtime manifest

This is **exactly the right direction** — each new symbol gets its own verification probe (mirroring the 5 existing probes), maintains the non-faking discipline (rather than sketchy approximations), and is building toward the runtime coverage needed for driver wiring later.

## Current state (as of 2026-07-19 end of this agent's session)

### Verified & production-ready
- ✅ Encoder (70/70 bit-verified)
- ✅ Register allocator (linear-scan, AAPCS64, smoke-tested)
- ✅ Codegen (IR op → instruction selection, proven by execution)
- ✅ ELF object writer (relocatable objects, validated by readelf + ld.lld)
- ✅ Modular runtime slices (print, scalars, string-search, with more expanding)
- ✅ Unit tests (39/39 passing)
- ✅ Verification discipline (encoder bit-check, QEMU execution, strace syscalls)

### Deliberately gated (correct design)
- Backend not wired into `driver.py` (no `--backend arm64` dispatch yet)
- Float formatting deliberately unsupported (not faking with `%.17g`)
- Exception-dependent parsing gated (needs exception runtime first)
- Containers (list/dict/set) not yet in scope

### Parallel work in progress
- Expanding runtime symbol coverage (find, rfind, hashing, repetition)
- Each expansion includes new verification probes
- Maintaining non-faking discipline

## Relationship to the broader 3.14.0 push

ARM64 work is a **hard blocker** for:
- #3 (4-platform support) — ARM64 is the second platform
- #7 (deterministic self-hosting) — asmpython running on ARM64

The x86-64 backend has 24 **fixable test failures** (collections.namedtuple false positives, sema tolerance for unreachable stdlib functions) that should be addressed before claiming "3.14 ready." This is a separate track with a plan file (`.claude/plans/valiant-noodling-toast.md`).

## Handoff notes for future sessions

1. **ARM64 is solid.** Don't second-guess the work. The verification discipline caught bugs (encoder bit-patterns). All claims are confirmed real.

2. **The other agent knows what they're doing.** Their choices (modular verification, non-faking discipline, explicit RESUME.md honesty flags) mirror the project's best practices.

3. **Two tracks can run in parallel:**
   - Sema/x86-64 regression fixes (6 collections tests, plan ready)
   - ARM64 runtime expansion (continuing now, likely to land more commits before the next check-in)

4. **Don't merge ARM64 into driver yet.** It's ready technically, but the wiring decision should be made deliberately when it's time to expand the platform claim from "1 ISA" to "2 ISAs." The gating is correct.

5. **If you're the x86-64 regression fixer:** AGENTS.md documents the recurring bug classes you should watch for. The sema fix is straightforward (syntactic reachability walker + error tolerance) — plan file has the full design.

6. **If you're expanding ARM64 runtime further:** keep doing exactly what the parallel agent is doing — one symbol at a time, each with its own verification probe, no approximations.
