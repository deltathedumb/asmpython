# asmpython optimizations

Optimization passes ported onto asmpython's language-neutral SSA IR
(`asmpython/_compiler/ir.py`; see `asmpython/_compiler/ir_contract.md` for the IR
contract). Passes are selected with `--passes`, live in `asmpython/_passes/`, and
third-party passes register through `asmpython.compiler_pass.CompilerPass(...)`.

```
asmpython build prog.py --passes o2
asmpython build prog.py --passes constfold,peephole,cse,simplifycfg,dce
asmpython build prog.py --passes help      # list everything registered
```

## Passes

| Pass | Ported from | Does | Differential | State |
|---|---|---|---|---|
| `constfold` | ConstantFolding | Fold constant integer arithmetic/compares, wrapped to the result width | 149/149 | in `o1`/`o2` |
| `sccp` | SCCP | Constant propagation **with reachability** — folds values that are only non-constant on unexecutable paths | 149/149 | in `o2` |
| `peephole` | InstCombine | Algebraic identities + `x*2^k` → `x<<k` | 149/149 | in `o1`/`o2` |
| `reassociate` | Reassociate | Canonicalize commutative operands (constant to the right) | 149/149 | in `o2` |
| `foldchain` | InstCombine | `(x+c1)+c2` → `x+(c1+c2)`, `(x<<a)<<b` → `x<<(a+b)` | 149/149 | in `o2` |
| `identityconv` | InstCombine | Drop `sext`/`zext`/`trunc` between identical types | 149/149 | in `o2` |
| `negfold` | InstCombine | Collapse double `ineg` / double `inot` | 149/149 | in `o2` |
| `cmpfold` | InstCombine | `x <u 0` is always false; `x >=u 0` always true | 149/149 | in `o2` |
| `cse` | EarlyCSE | Block-local common-subexpression elimination | 149/149 | in `o1`/`o2` |
| `loadelim` | GVN | Reuse a prior load of the same pointer in a block | 149/149 | in `o2` |
| `storeforward` | GVN/MemCpyOpt | Forward a stored value to a later load of the same pointer | 149/149 | in `o2` |
| `dse` | DSE | Remove stores overwritten before any possible read | 149/149 | in `o2` |
| `simplifycfg` | SimplifyCFG | Fold constant branches, drop unreachable blocks, repair phis | 149/149 | in `o2` |
| `jumpthread` | JumpThreading | Retarget branches through forwarding-only blocks | 149/149 | in `o2` |
| `blockmerge` | SimplifyCFG | Fuse a block into its sole predecessor | 149/149 | in `o2` |
| `phisimplify` | SimplifyCFG | Collapse phis whose incoming values are all identical | 149/149 | in `o2` |
| `sink` | Sink | Sink pure instructions into their single use block | 149/149 | in `o2` |
| `dce` | DCE | Delete unused side-effect-free instructions | 149/149 | in `o1`/`o2` |
| `adce` | ADCE | Mark-sweep from side-effecting roots | 149/149 | in `o2` |
| `globaldce` | GlobalDCE | Drop globals nothing references | 149/149 | in `o2` |
| `licm` | LICM | Hoist loop-invariant computations into the preheader | 149/149 | in `o2` |
| `loopdelete` | LoopDeletion | Remove loops with no effect and no live result | 149/149 | in `o2` |
| `gvn` | GVN | Global value numbering over the dominator tree | 138/149 | **experimental** |
| `mem2reg` | PromoteMemoryToRegister | Promote stack slots to SSA, phi at dominance frontiers | 146/149 alone | **experimental** |

Aliases: `constprop`/`fold`, `instcombine`/`simplify`, `earlycse`, `cfg`/`simplify-cfg`,
`deadcode`, `promote`/`sroa-lite`.

## Presets

| Preset | Expands to |
|---|---|
| `o1` | `constfold,peephole,cse,dce` |
| `o2` | the full 21-pass certified sequence (see `_passes/__init__.py`) |

Order is not cosmetic and is exactly what the differential sweep validated:
`sccp` establishes constants and reachability, the arithmetic passes
canonicalize so the redundancy passes can match, the CFG passes collapse what
became statically decidable, and `adce`/`globaldce` sweep last. Change the
list only together with a fresh differential run.

Presets contain **only** differential-certified passes. `mem2reg` is deliberately
excluded (see below); a preset must never silently change program behavior.

## Impact

Full preset pipeline, measured across 100 corpus cases:

| Metric | Before | After | Change |
|---|---|---|---|
| Basic blocks | 13,688 | 9,804 | −28.4% |
| Instructions | 80,039 | 58,148 | −27.4% |
| Verifier failures | — | 0 | — |

Most of the block reduction is `simplifycfg` deleting `ir_lower`'s
constant-condition type-dispatch branches (the any-tag machinery emits many
branches that are statically decidable).

Separately, the backend fixes below moved the ordinary suite from **532 → 550**
passing with zero regression. Those were miscompiling ordinary programs
independent of any pass — optimization only made them observable.

## Certification: the differential harness

**The ordinary test suite cannot detect silent miscompiles.** It scored
*identically* with a pass that was actively corrupting output. Every pass must
therefore clear a differential sweep before entering a preset:

1. compile each `tests/cases/*.py` twice — with and without the pipeline,
2. run both binaries,
3. diff stdout and exit code.

Any difference is a miscompile. The harness caught 8/60 divergences on its first
run. This is a hard requirement for any new pass, including third-party ones.

## Register allocator

Optimization exposed two real allocator bugs, both fixed in
`asmpython/_backends/x86_64/regalloc.py` (+ a prologue change in `codegen.py`):

| Fix | Problem | Resolution |
|---|---|---|
| Call-crossing parameters | Parameters were pinned to their incoming ABI argument register (all caller-saved) for their whole lifetime; `crosses_call` was computed but never consulted for them | `_home_param()` homes such a parameter on the stack; `AllocResult.param_spills` + prologue copy |
| `_take_gp` fallthrough | `prefer_callee_saved` was best-effort and fell through to a caller-saved register when the pool was exhausted | `require_callee_saved` returns `None`; `_alloc_gp` homes the value on the stack |

Both were latent because `ir_lower` spills every parameter to a stack slot
immediately, so nothing is live across a call until a pass promotes those slots.

### Control-flow analysis

Loop structure previously came from a block-INDEX RANGE, which both invented
loops (try/except dispatch branches jump backward by index without being loops —
the only reason an explicit `try_regions` exclusion existed) and missed body
blocks (`ir_lower` emits the KeyError raise/ok helper pair at *higher* indices
than the latch). Both are gone: `asmpython/_compiler/cfg.py` is now the single
canonical control-flow analysis — dominators, dominance frontiers,
dominance-checked back edges, and natural loops — shared by the passes and the
backend. A branch is a back edge only when its target **dominates** its source.

That exposed a deeper defect. **Loop-carried values were structurally
mishandled**: the allocator reserves a register at a value's *definition* while
walking blocks in index order, but a loop-carried value is *read* in a block
visited before its definition (phi elimination puts the back-edge copy in the
latch, while the value is computed in a later-emitted body block). Its live range
starts too early — extending the last use cannot fix that. `_loop_carried()` now
identifies these and reserves them before the linear walk.

Symptom was `Counter.total()` returning the first element instead of the sum;
`%t46` and `%t49` had both been assigned RBX.

### Why `mem2reg` is still experimental

The loop-carried defect above is fixed, and with it the original
`250_collections_depth` divergence. But `mem2reg` still diverges on its own —
3 of 149 (`143_unannotated_param_inference`, `164_statistics_module`,
`172_base64_module`), and 4 of 149 combined with `o2`. These are different cases
from the original failure, i.e. further defects the earlier sample did not
surface, not a regression of the fix.

Until they are understood it stays out of the presets — a preset must never
silently change program behavior. Note the shape of the failures (segfault and
`list index out of range`): they point at container/indexing lowering under
promotion, which is worth bisecting per-case the way the earlier ones were.

## Writing a pass

```python
import asmpython
from asmpython._compiler.ir import IRPass

class StrengthReduce(IRPass):
    name = "strength-reduce"
    description = "replace imul by a power of two with a shift"
    requires  = frozenset({"ssa"})          # rejected if placed before mem2reg
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module):
        ...                                  # transform in place
        return changed                       # True if anything changed

asmpython.compiler_pass.CompilerPass("strength-reduce", StrengthReduce())
```

Then `--passes mem2reg,strength-reduce`, or point `--passes` straight at the
plugin file. `requires`/`provides`/`preserves` are a deliberately tiny invariant
system — enough for the manager to reject an impossible ordering, and not
intended to grow into LLVM's analysis-preservation machinery.

A pass must work regardless of which frontend produced the module: it runs below
the IR waist and sees only the neutral vocabulary.

**Constraint for block-deleting passes:** `IRFunc.try_regions` records
`(setjmp_block_index, end_block_index)` as *positional block indices*, read by
regalloc's `_in_try_region`. Deleting a block renumbers them silently, so
`simplifycfg` skips functions that have any — do the same, or remap the indices.

## Roadmap

Ported so far covers LLVM's cheap local tier. Still open, roughly in order:

- **Dominator-based natural loops in regalloc** — unblocks `mem2reg`, and is a
  prerequisite for anything loop-based
- **SCCP** — sparse conditional constant propagation (subsumes `constfold` +
  `simplifycfg` reachability)
- **GVN** — global value numbering, extends `cse` beyond a single block
- **LICM** — loop-invariant code motion
- **Inlining**
- **Graph-coloring register allocation** to replace the linear scan

Licensing note: passes ported from LLVM follow LLVM's algorithms, not its source
text. LLVM is Apache-2.0 with LLVM-exception; any code genuinely derived from it
must retain attribution and license notices.
