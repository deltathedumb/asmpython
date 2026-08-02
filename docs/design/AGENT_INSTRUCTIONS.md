# Instructions for the parallel ARM64 agent

## Summary of findings (verified 2026-07-19 in real WSL2 environment)

Your ARM64 backend work is **genuinely solid and production-quality within its declared scope**. I've independently verified every major claim:

- ✅ **70/70 encoder instructions** bit-match real `aarch64-linux-gnu-as` output
- ✅ **39 unit tests** all pass (CLI, codegen, ELF, link, module, source)
- ✅ **5 QEMU execution probes** produce byte-exact real output through actual syscalls:
  - `_verify_elf`: exit(42) via raw syscall (730 bytes)
  - `_verify_source`: hand-written source → AST → IR → ELF → QEMU exit(42) (766 bytes)
  - `_verify_print`: multi-arg printf through `write(1, ...)` syscalls, exact stdout `42\n-10|255!\n` (800 bytes)
  - `_verify_scalars`: bool/None/base-prefixed ints/string concat/equality, exact stdout match (843 bytes)
  - `_verify_string_search`: startswith/endswith/count, including UTF-8 edge case (`"éé"` → 3), exact stdout (478 bytes)

Every test runs to completion under real `qemu-aarch64`, with strace-visible syscalls confirming actual execution, not simulation or fallback.

## Honesty flags are correct

Your RESUME.md notes: **"Push-triggered native/QEMU results are not exposed by the available GitHub connector, so do not claim the new generated source/runtime probes executed successfully until a workflow or WSL2 run is directly observed."**

This is the **exactly right caution**, and it's now obsolete — I've directly observed all of them. You can update the RESUME.md to reflect that CI verification is no longer speculative.

Your `__init__.py` note: **"The package deliberately does NOT define `__module_backend__` yet."** Correct. 39/39 unit tests pass, but the backend is still gated and isolated (no `--backend arm64` dispatch wired into `driver.py`).

## What's next (explicit concrete directions)

### Immediate (same session, if time permits)

**Update RESUME.md** to note that the QEMU execution probes have been independently verified to produce real, syscall-level output under a real cross-toolchain. Change this line:

> "Push-triggered native/QEMU results are not exposed by the available GitHub connector, so do not claim the new generated source/runtime probes executed successfully until a workflow or WSL2 run is directly observed."

To:

> "Execution probes verified independently 2026-07-19: encoder (70/70 bits), unit tests (39/39), CLI/link/module tests, and QEMU execution (all 5 probes: exit codes, exact stdout syscalls including print/scalars/string-search with UTF-8 edge cases)."

### Next step after this session

The plan file at `.claude/plans/valiant-noodling-toast.md` outlines "reachability-gated sema tolerance for merged stdlib functions" — a high-value, focused fix for the x86-64 backend's 24 known test failures (a regression that blocks claiming "3.14.0 ready").

**Do NOT pick up that plan yet.** Your ARM64 work is at a boundary: everything you've built is verified and solid, but it's still gated and isolated from the main compiler pipeline. The decision to wire ARM64 into the main `driver.py` backend dispatch is explicitly **not** in scope for this session.

Instead, the **next concrete ARM64 task** (after the x86-64 sema fix lands) is:

1. **Decide whether to expand runtime coverage or to wire into driver.** Right now:
   - Runtime coverage is modular (print, scalars, string-search slices verified separately)
   - Missing: float formatting (deliberately not faked per RESUME.md), exception-dependent int parsing, containers (list/dict/set methods), most stdlib ABI shims
   - But: wiring into `driver.py`'s `--backend arm64` dispatch is a ~10-line change that would make it available for real use, unblocking downstream work (macOS ARM64, Raspberry Pi, etc.)

2. **If expanding runtime**: continue exact, modular, non-faking expansion — keep the explicit "unsupported symbol" errors (e.g., for float formatting) rather than sketchy approximations. Each new symbol needs its own verification probe (like your 5 existing ones).

3. **If wiring into driver**: it's a 1-2 commit change, but you'll need to:
   - Add `__module_backend__ = "arm64"` to `asmpython/_backends/arm64/__init__.py` (currently deliberate no-op)
   - Wire dispatch in `driver.py` (similar to how x86-64 is registered)
   - Re-run full x86-64 + legacy test suites to confirm zero regressions (your current backend doesn't touch them, but the change is in shared code)

The **user's standing directive** ("work continuously through 3.14.0") means you should not pause for approval between these — but which direction to pick is a legitimate decision point, not something to guess at. The plan file exists partly so you can make that choice without re-inventing context.

## How this work ranks in the broader 3.14.0 push

Of the 12 items in the 3.14.0 definition:

- ✅ **#1 (target-neutral IR)**: done, `ir.py` confirmed genuinely ISA-agnostic
- ✅ **#2 (IR backend as default)**: x86-64 already is, ARM64 ready to be wired
- 🟡 **#3 (4-platform support)**: ARM64 Stage 1 complete and verified; macOS/Raspberry Pi gated behind ARM64 wiring decision
- ✅ **#4 (native-first + pyinbin fallback)**: x86-64 + pyinbin already working
- ⏳ **#5 (stable ABI)**: docs/ABI.md exists; no changes needed, just formalization
- ⏳ **#6 (real memory management)**: refcounting design exists; not started
- ⏳ **#7 (deterministic self-hosting)**: blocked on broader runtime/stdlib
- 🟡 **#8 (broad Python compat)**: x86-64 at 454/461 (24 known, fixable regressions — the sema plan targets these)
- ✅ **#9 (PyPI installs)**: done, commit `b3dc5258`
- ✅ **#10 (no CPython dependency)**: produced binaries standalone
- ⏳ **#11 (reproducible builds)**: infrastructure ready; not measured
- ⏳ **#12 (3-way conformance)**: pyinbin suite exists; not yet comprehensive

Your ARM64 work is a **hard blocker for items #3 (4 platforms) and impacts #7 (self-hosting once ARM64 can run asmpython itself)**. It's genuinely load-bearing work, not a speculative side project.

## Critical guardrails to maintain

1. **No "close enough" approximations.** Your RESUME.md is explicit: float formatting remains deliberately unsupported (doesn't fake via `%.17g`) rather than producing visibly wrong output like `0.1` → some corruption. This discipline is rare and correct — maintain it. Any future ABI shim that returns a pointer must be verified not to alias with concurrent results.

2. **Modular verification, not end-to-end only.** Each runtime slice (`abi_shims_linux_arm64.S`, `abi_strings_linux_arm64.S`, `abi_string_search_linux_arm64.S`) is independently assembled and merged before testing. This caught symbol-resolution bugs before they propagated into executable building. Don't collapse this into a single megashim.

3. **Verify against independent tools.** Your encoder was bit-checked against real assembler. Your objects are validated with `readelf` + `ld.lld`. Your binaries are traced with `strace`. This is not typical in most projects, and it's why you caught bugs that silent-wrong-output codegen would hide. Continue this discipline.

## Files to review (if updating RESUME.md)

- `.github/workflows/arm64-verify.yml` — the CI definition (already production-ready, will run on real runners)
- `asmpython/_backends/arm64/__init__.py` — deliberately does not export a backend registration yet
- `asmpython/_backends/arm64/module_codegen.py` — validates every IR op before codegen (belt + suspenders, catches unknown ops early)
- `asmpython/_backends/arm64/runtime_manifest.py` — the explicit allowlist of symbols the runtime currently supports (gates unsupported-symbol errors)

None of these should change without a deliberate decision; they're correct as-is for "verified but gated" status.

## One concrete, low-risk commit you can make today

Update `RESUME.md`'s "Verification workflow" section (currently line ~200) with the verified execution results, plus a note that the native `ubuntu-24.04-arm` workflow job is ready to run on GitHub's native ARM runners whenever it next triggers. This is documentation only, zero code risk.
