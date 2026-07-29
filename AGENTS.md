# AGENTS.md — operational guidance for any agent working this repo

Cross-cutting rules and recurring bug patterns that apply regardless of
which specific task you're picking up. For *current project state* (what's
done, what's in progress, what's next), read `RESUME.md` first — this file
is about *how to work here*, not *what to do next*.

## Process rules

- **Work continuously, checkpoint via commits.** The standing directive on
  this project is a "no stop at all" push through the 3.14.0 punch list
  (see `RESUME.md`/`roadmap.md`). Don't pause between tasks waiting for
  approval unless genuinely blocked on a decision only the user can make.
- **Only commit when the user asks**, but on this project the user has
  pre-authorized committing at logical checkpoints during the ongoing
  3.14.0 push — don't let uncommitted work pile up across multiple
  unrelated fixes/features.
- **Tests take a long time. Always run them in the background**, never
  synchronously — `python -m tests.runner --backend x86-64
  --no-pyinbin-fallback -j 8` (default backend) and `python -m
  tests.runner -j 8` (legacy backend) both take multiple minutes.
- **Use only 1-2 subagents at a time if delegating.** A past session
  launched 5 simultaneous background agents and all 5 crashed from
  hitting a session API usage limit.
- **Zero regressions is the bar.** Before considering any change to
  `sema.py`, `ir_lower.py`, or either backend done, re-run both test
  suites and diff the pass count/failure list against the last known
  baseline (see `RESUME.md`'s "Test baseline" section) — not just "did it
  run without error."
- **Multi-agent shared workspace.** This project is sometimes worked by
  more than one agent concurrently against the same git branch (`beta`).
  Check for a `devthread.txt` or similar coordination file, and run `git
  status`/`git log` before assuming a clean tree is yours alone.
- **Don't resurrect the compiler-extension system** (`extend`/`retract`/
  `const`, `@final`/`@sealed`/`enum`/`interface`, etc.) without an
  explicit user request — it was deliberately withdrawn because it
  diverged from "asmpython mirrors CPython with only tiny differences."
  Real implementation is archived under `archived/extensions/` for
  historical reference only.

## Verifying real work, not assumed work

This codebase has a long history of bugs that were **silent** — wrong
output or corrupted state with no crash, no error, nothing to grep for.
Several of the worst were confirmed only via `gdb`/`strace`/disassembly,
not by reading the code and reasoning about it. Two concrete lessons that
generalize:

1. **When porting a hand-encoded binary format (an instruction encoder, an
   object-file writer, an ABI shim) to a new target, verify against a real
   independent tool**, not just careful reading. The ARM64 encoder
   (`asmpython/_backends/arm64/encoder.py`) is cross-checked bit-for-bit
   against real `aarch64-linux-gnu-as` output via `_verify_encoder.py` —
   this caught one real, otherwise-invisible bug (`cset`'s destination
   register field was silently wrong). The same discipline applies to any
   future ELF/PE/Mach-O relocation work, any new ABI shim, any new
   encoder.
2. **When chasing a crash or wrong-output bug in compiled native code,
   don't trust captured stdout alone.** stdio is fully buffered when not
   connected to a real terminal (no `fflush` after each print in generated
   code) — "no output" can mean "crashed immediately" or "crashed after
   doing a lot of work, buffer never flushed." Use `gdb` breakpoints at
   the entry point or specific call sites instead of trusting what a
   piped/redirected run printed.

3. **A pass/fail count cannot see a miscompile.** The suite scores a case that
   failed before and fails after as a no-op no matter how wrong the bytes got,
   so for an ALWAYS-ON change (frontend, sema, ir_lower, codegen, regalloc) the
   suite is not a sufficient gate. Use:

   ```text
   git stash
   python tests/diff_passes.py --mode record --state base.json --sample 150
   git stash pop
   python tests/diff_passes.py --mode check  --state base.json --sample 150
   ```

   It compiles each case twice and diffs actual stdout + exit code. The default
   `--mode passes` builds both variants in ONE process and therefore cannot see
   an always-on change at all. Comparing failure SETS between suite runs is
   still worth doing — a fix and a regression cancel out in a count — but it
   only covers cases that PASS; `diff_passes` is what covers the rest.
   `tests/subset_runner.py` replays a named case list into a private build
   directory when you want a cheap targeted re-check, or a baseline run in a
   `git worktree` alongside a full run.

4. **Confirm you are testing the compiler, not the interpreter.** When the
   native backend rejects a source, `asmpython build` falls back to running it
   under pyinbin and exits 0 with CPython-correct output. Correct output
   therefore does not prove anything compiled. Build with
   `--no-pyinbin-fallback` whenever a result matters. (The message now names
   the rejection reason; it used to say only that the fallback succeeded, and
   two agents independently mis-diagnosed a compiler bug from reading its
   output.)

5. **Suspect your harness before the compiler when a symptom looks
   structurally impossible.** A reported "double bracket" bug — `[1, 'a']`
   printing as `[[1, 'a']]` — turned out to be a test helper that wrapped
   program output in literal brackets with `echo "[$out]"`. It sent one agent
   chasing the container formatter while the real symptom, seen by another,
   was raw pointers. Print the raw bytes before believing a shape.

6. **Read the IR.** Two of the hardest bugs on this branch were settled by
   dumping IR, not by reading source: `%tN:i64 = load(...)` immediately
   followed by `_abi_new_box(-1, %tN)` is an already-boxed value being boxed a
   second time, and no amount of source reading showed it. See
   `_compiler/ir_print.py`.

### Make sure you are testing the code you think you are testing

The runtime is a **prebuilt library**, not part of the program you compile.
Editing `_compiler/codegen.py` changes nothing a test can see until
`python -m asmpython._runtime.build` regenerates it. That build used to decide
staleness by mtime and, when it decided "up to date", printed nothing and
exited 0 — so a stale library looked exactly like a successful rebuild.

It now hashes source **content** (`<archive>.srcdigest`) and invalidates the
stamp before rebuilding, so a failed assemble cannot leave a stamp that
outlives the artifacts it describes. Two habits still worth keeping:

- Before trusting a measurement, confirm your change is actually IN the
  emitted runtime: `grep <your new symbol> asmpython/_runtime/_build/*.asm`.
- Pass `--force` when a result would be expensive to get wrong.

This is not a hypothetical. A stress run, a full corpus run, and the
conclusions drawn from both were once measured against a library that never
contained the change under test — and the write-up confidently reported that
the change was sound under load. **A number produced by the wrong binary is
worse than no number**, because it is indistinguishable from a real one.

Corollary for A/B testing: `git stash` and `git checkout` restore content with
timestamps that defeat mtime checks, and in a shared working tree they will
also take a *different agent's* uncommitted work with them. Stage and revert by
explicit path.

## Recurring bug classes (check for these first when debugging similar symptoms)

These have each appeared **more than once**, in different specific spots,
across this project's history. If you hit a bug that smells like one of
these, check the whole class of call sites, not just the one you found.

- **Global-vs-local write/read mismatch.** A module-scope name (for-loop
  variable, range-for variable, exception binding, walrus target) written
  via the "always local" helper (`ctx.ensure_slot()`) while reads resolve
  it as a module global (or vice versa) — silently reads/writes the wrong
  storage. Found and fixed at least 5 separate times in `ir_lower.py`
  across different statement kinds. When adding a new binding form, check
  both the write-side helper (`_name_ptr` vs `ensure_slot`) and the
  read-side type lookup (`_is_global_name` ordering) agree.
- **Sema stamps the right info, `ir_lower.py` doesn't check it everywhere
  it's stamped.** `dunder_owner`/`dunder_call_owner`/`dunder_contains_owner`
  were each correctly computed by `sema.py` but missed by `ir_lower.py` in
  specific dispatch sites (`A.UnaryOp`, `A.Call.__call__`, `A.Compare`,
  membership `in`/`not in`) — each miss either crashed or silently
  produced wrong output (e.g. comparing instance pointer identity instead
  of calling a real `__eq__`). When adding a new dunder-dispatchable
  construct, grep for every existing `dunder_*_owner` check and confirm
  the new one is wired in everywhere sema stamps it, not just the first
  place that seemed relevant. Also check the *reachability walker*
  separately — a lowering fix alone isn't enough if the walker that
  decides what to actually emit doesn't know to keep the dunder method.
- **Scratch-register collision when both operands of a binary op are
  spilled.** If two operands of the same instruction both need a load
  from a stack spill slot, and both loads target the *same* shared
  scratch register, the second load silently clobbers the first before
  either is read — corrupting `a OP b` into `b OP b`. Fixed via an
  `alt_scratch` parameter routing the second operand through a *different*
  scratch register. This is a general pattern for any two-operand
  codegen helper across any backend, not an x86-64-specific quirk — the
  AArch64 port carries the same `alt_scratch` convention forward for
  exactly this reason.
- **A helper returning a pointer into a shared/static buffer, read
  concurrently with another call to the same helper.** `_abi_*` shims
  that format a value into one process-wide static buffer are only safe
  for one live result at a time — this backend's multi-arg call/print
  lowering routinely formats several values before making one shared
  call, so two "any"-typed formatted values silently alias and the later
  one overwrites the earlier before it's read. Fix is to `dup` the result
  before returning, not to assume one-shot usage.
- **Windows/WSL exit-code propagation quirks.** Directly executing a
  compiled `.exe`, or a `wsl.exe ... ` invocation, through the harness's
  Bash tool can report the wrong (often `0` or `127`) exit code even when
  the real program's exit status is different and correct — confirmed via
  `gdb`/`strace` showing the true value. Don't conclude a program is
  broken purely from a mismatched shell-reported exit code; verify with a
  debugger/tracer attached to the actual process.

## Where things live

- `asmpython/_compiler/` — lexer, parser, sema, IR lowering (`ir_lower.py`),
  the legacy top-level `codegen.py` (NASM-text emission, `--backend
  legacy` only), `driver.py` (backend dispatch), `errors.py`
  (`ErrorCode`/CPython-exception-name mapping), `project.py`
  (`project.json` schema), `pypi.py` (retired compatibility shim; Python
  packages now come from the active interpreter's site-packages), and
  `packages.py` (prebuilt native-binary dependency installer, a separate
  system from Python packaging).
- `asmpython/_compiler/ir.py` — the target-neutral SSA IR itself
  (`IRModule`/`IRFunc`/`IRBlock`/`IRInstr`, the `IRBackend` plugin
  interface).
- `asmpython/_backends/x86_64/` — the default, reference ISA backend.
- `asmpython/_backends/arm64/` — in-progress ARM64 backend (see
  `RESUME.md` for exact status).
- `asmpython/_backends/ternary/` — experimental ternary-VM backend.
- `asmpython/pyinbin/` — the fallback Python bytecode interpreter.
- `asmpython/_runtime/` — runtime object/ABI shim NASM sources and their
  Python build glue (`build.py`), currently x86-64-only outside the
  experimental freestanding ARM64 slices.
- `archived/extensions/` — the withdrawn compiler-syntax-extension system,
  kept for historical reference only.
- `tests/cases/` — positive test programs (must compile+run+match
  expected output); `tests/cases_fail/` — negative test programs (must
  fail to compile with a specific diagnostic); `tests/runner.py` — the
  test harness.
- `roadmap.md` — the versioned feature roadmap (what's in which release).
- `docs/ABI.md` — the formal, versioned binary ABI spec.
- `docs/EXTENSIONS.md` — documents the now-withdrawn extension system,
  historical reference only.

## Memory

A separate, persistent cross-session memory system also exists (outside
this repo, tied to the coding assistant's own state) with more granular
notes — pyinbin object-model gotchas, x86-64 backend block-ordering
hazards, shared-workspace conventions, etc. If you're an agent with access
to that memory system, check it; if not, this file plus `RESUME.md` is
the durable, repo-local equivalent.
