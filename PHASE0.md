# Phase 0 — the measurement instrument

Phase 0 builds the thing every later phase is steered by: a corpus that can tell
a regression from a pre-existing failure, and a set of probes that says
concretely what "the value model is fixed" means.

Nothing here changes the compiler. All of it changes what you can *see*.

---

## 1. The baseline is pinned

`tests/baseline.json` records the exact per-case verdict for the whole corpus,
plus a fingerprint of the tree it was measured on (commit + hash of the
uncommitted diff).

```bash
python -m tests.baseline --record          # run the suite, write the manifest
python -m tests.baseline --check           # run the suite, diff the manifest
python -m tests.baseline --show            # summarize the manifest
python -m tests.baseline --check -- -j 8 --backend x86-64   # flags after `--` go to the runner
```

`--check` classifies every difference and **only REGRESSION is fatal**:

| class | meaning | exit |
|---|---|---|
| `REGRESSION` | was passing, now failing | **1** |
| `FIXED` | was failing, now passing | 0 (re-record to lock in) |
| `NEW` | not in the manifest | 0 |
| `REMOVED` | in the manifest, no longer run | 0 |

### Why this had to exist first

At 766/1085, `python -m tests.runner` cannot answer "did I break something?" A
run reporting 764 is either a 2-case regression or a 2-case reshuffle inside the
319 already-failing cases, and nothing in the output distinguishes them. That
ambiguity — not the failure count — is what makes large-scale change unsafe.

The manifest also fingerprints the tree, because most work here happens on a
dirty tree and two different dirty trees on the same commit produce different
results. A clean diff against a manifest recorded elsewhere is not evidence.

Injecting one regression and one fix into a recorded run shows the failure mode
the count hides:

```text
baseline 766/1085 -> current 766/1085

REGRESSIONS (1) -- was passing, now failing:
  01_hello.py

FIXED (1) -- was failing, now passing:
  211_argparse_module.py
```

The pass count is **identical** and a real regression is present. Any check
based on the total would have reported no change.

**Note:** `FAILURE_AUDIT.md` documents 285 failures at commit `5a9355ad`. The
measured count at the pinned tree is **319**. The audit was already 34 cases
stale — a concrete instance of the problem this manifest solves.

---

## 2. The patch layer's requirements are now tested

The 26 `asmpython/_compiler/*_compat_fixes.py` modules are, collectively, a
specification. Each one is a recorded conformance requirement — some valid
Python that the core passes got wrong — expressed as a monkeypatch that rebinds
a `SemaAnalyzer` method at import time.

That specification was almost entirely untested. The behaviors were guaranteed
only by the patches themselves, so deleting a patch during a rebuild would
silently drop the requirement.

`tests/cases/compat_*.py` — **29 cases**, one per documented requirement — now
pin those behaviors independently of the patches. Run the set with:

```bash
python -m tests.runner -k compat_
```

Every `# expect:` block is generated from real CPython 3.14.6 output by
`tests/generators/gen_compat_cases.py`, never hand-written. Each case carries a
`# guards: <module>` marker naming the patch whose contract it holds.

### What running them revealed

**20 of 29 pass. 9 fail.** The patch modules' docstrings describe *intent*, not
achieved behavior — and the existing 1085-case corpus never caught the gap.

| case | guards | symptom |
|---|---|---|
| `compat_ordered_flow_combined` | `ordered_flow` | `leaf:b` → `8742144` (raw pointer) |
| `compat_analysis_dynamic_return` | `analysis` | `value=x` → `value=5368737804` (raw pointer) |
| `compat_iterable_element_helper` | `iterable_element` | `ADA` → `0` |
| `compat_dynamic_parameter` | `dynamic_parameter` | `HI` → `0` |
| `compat_class_string` | `class_string` | `Alpha` → `(null)` |
| `compat_class_registry` | `class_registry` | segfault `0xC0000005` |
| `compat_metaclass_descriptor_collect` | `metaclass` | segfault `0xC0000005` |
| `compat_type_parameter_specialize` | `type_parameter` | segfault `0xC0000005` |
| `compat_class_value_tuple` | `class_value` | native compile refused; interpreter fallback masked it |

Two findings worth keeping:

**The pointer leak is confirmed, not inferred.** `compat_ordered_flow_combined`
printed `9004256`, `8545504`, `1860832` on three consecutive runs. The value
changes per run, so it is a heap address being printed where a `str` was
expected — not a mistyped constant.

**One failure is masked by the interpreter fallback.**
`compat_class_value_tuple` is rejected by the native backend with:

```text
[E113] int has no method 'tag'
```

`KINDS = (A, B)` then `first = KINDS[0]` then `first().tag()` — the class value
is typed `int` because class values *are* integer RTTI ids. The subset
assumption surfacing as a compile-time refusal. `pyinbin` then runs the program
correctly, so at the CLI it looks like it works.

---

## 3. The value model has explicit acceptance criteria

`FAILURE_AUDIT.md` attributes ~84 of the known failures to a single assumption,
stated in `_compiler/codegen.py`'s own design notes:

> All values are 64-bit ints.

Floats, strings, objects, bools, bytes and bigints are encoded *into* that word
rather than modelled. Those 84 failures are spread across cases that each test
something else, so none of them isolates the representation question.

`tests/cases/vm_*.py` — **26 probes** — isolate it. Each exercises exactly one
property of the value model.

```bash
python -m tests.runner -k vm_
```

**15 of 26 pass today.** The 11 failures are the Phase 1 acceptance set:

| probe | required property | today |
|---|---|---|
| `vm_int_is_arbitrary_precision` | int does not wrap at 64 bits | `2**63` → `-9223372036854775808` |
| `vm_none_is_not_zero` | `None` is distinguishable from `0` | `None` → `0`; `0 is None` → `True` |
| `vm_bool_is_not_int` | `bool` renders as `True`/`False` | → `1`/`0` |
| `vm_bytes_literal` | `bytes` is a distinct type | absent |
| `vm_bytearray_mutable` | `bytearray` is mutable | absent |
| `vm_str_field_via_helper` | a `str` field read via a helper stays a `str` | → raw pointer |
| `vm_tuple_through_any` | tuple survives an opaque round trip | element → raw pointer |
| `vm_container_heterogeneous` | a list may hold mixed kinds | — |
| `vm_callable_param_result` | a called-through parameter keeps its result kind | → `0` |
| `vm_round_returns_int` | `round(x)` → int, `round(x, n)` → float | `round(2.55, 1)` → `2.6` |
| `vm_finally_runs_on_return` | `finally` executes on the return path | does not run |

These are not regression guards. They are the definition of done for the value
model — Phase 1 is complete when they pass.

---

## 4. Regenerating the cases

`tests/generators/` holds both generators. They derive every `# expect:` block
by executing the case under the host CPython, so the corpus can be re-derived
against a future interpreter instead of trusting hand-typed output.

```bash
python tests/generators/gen_compat_cases.py /tmp/out    # 29 patch-layer cases
python tests/generators/gen_vm_cases.py     /tmp/out    # 26 value-model probes
```

Both refuse to emit a case whose source does not run cleanly under CPython, so a
malformed probe fails loudly at generation rather than becoming a bogus
expectation.

**Marker placement matters.** `runner._parse_expect` collects *every* `#` line
following the `# expect:` marker into the expected stdout. The `# guards:` and
`# probes:` markers therefore precede the block, never follow it. A trailing
marker is read as an extra expected output line and fails the case.

---

## 5. Corpus state after Phase 0

| | before | after |
|---|---:|---:|
| cases | 1085 | 1140 |
| passing | 766 | 801 |
| known failures | 319 | 339 |
| patch-layer requirements tested | ~0 | 29 |
| value-model properties isolated | 0 | 26 |

The 20 added failures are not new breakage — they are **previously invisible
breakage**, now named and pinned.

---

## 6. What this changes for the phases that follow

- **Any refactor can now be checked.** `python -m tests.baseline --check`
  answers "did I break something?" in one command.
- **The patch layer can be deleted safely.** Its contract lives in
  `compat_*.py`, not in the patches. Phase 2 deletes the modules and runs
  `-k compat_`; anything that goes red is a requirement that needs rehoming.
- **Phase 1 has a finish line.** The 11 failing `vm_` probes define it.
- **The 9 failing `compat_` cases are triage-ready.** Three are segfaults, which
  are the highest-severity items in the corpus and were not previously
  represented at all.

One gap left deliberately: `program_compat_fixes` is a multi-module project
import-resolution fix and is not expressible as a single-file case. It is the
only one of the 26 modules without a dedicated regression. Covering it needs a
project fixture in the style of `tests/project_import_fixture`.
