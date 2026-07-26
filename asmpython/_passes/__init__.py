"""Registry and manager for IR optimization passes.

The third registration axis alongside ``_frontends`` and ``_backends``: a pass
is a neutral IR->IR transform (see ``_compiler/ir_contract.md``), selected with
``--passes name1,name2``.

Built-in passes are registered here. Third-party passes register through
``asmpython.compiler_pass.CompilerPass(...)`` and are indistinguishable from
built-ins at the CLI -- the same registry, the same ``--passes`` selection.

The manager tracks a deliberately tiny invariant system (string tags such as
``"ssa"``): a pass declares ``requires``/``provides``/``preserves`` and the
manager refuses an ordering that would run a pass without its prerequisite.
This is not LLVM's analysis-preservation machinery and should not grow into it.
"""

from __future__ import annotations

from collections.abc import Iterable

from .._compiler.ir import IRModule

_REGISTRY: dict[str, object] = {}
_ALIASES: dict[str, str] = {}

#: Convenience pipelines expanded by name in a ``--passes`` spec.
#:
#: These contain only passes that have cleared the differential test (compile
#: the corpus with and without the pipeline, diff runtime output) on every case.
#: ``mem2reg`` is deliberately NOT included: it is correct on the large majority
#: of programs but still miscompiles a small number of them (see its module
#: docstring), and a preset must never silently change behavior. Request it
#: explicitly -- ``--passes mem2reg,constfold,dce`` -- while that is being
#: chased down.
PIPELINES: dict[str, tuple[str, ...]] = {
    # Cheap local cleanup. Certified 149/149.
    "o1": ("constfold", "peephole", "cse", "dce"),
    # The full certified pipeline, in EXACTLY the order the differential sweep
    # validated (149/149). Ordering is load-bearing: sccp establishes constants
    # and reachability, the arithmetic passes canonicalize so the redundancy
    # passes can match, the CFG passes then collapse what became statically
    # decidable, and adce/globaldce sweep last once nothing else will add work.
    # Change this list only together with a fresh differential run.
    "o2": (
        "sccp", "constfold", "reassociate", "peephole", "foldchain",
        "identityconv", "negfold", "cmpfold",
        "cse", "loadelim", "storeforward", "dse",
        "licm", "loopdelete",
        "simplifycfg", "jumpthread", "blockmerge", "phisimplify", "sink",
        "adce", "globaldce",
    ),
}


class PassError(Exception):
    """Raised for an unknown pass name or an impossible pass ordering."""


def register_pass(name: str, pass_obj: object, *, aliases: Iterable[str] = ()) -> None:
    """Register ``pass_obj`` under a canonical name and optional aliases.

    Re-registering a canonical name replaces the previous object, so a
    third-party pass may deliberately override a built-in of the same name.
    """
    if not name:
        raise ValueError("pass name must not be empty")
    _REGISTRY[name] = pass_obj
    for alias in aliases:
        if alias and alias != name:
            _ALIASES[alias] = name


def get_pass(name: str) -> object | None:
    """Look up a registered pass by canonical name or alias."""
    found = _REGISTRY.get(name)
    if found is not None:
        return found
    canonical = _ALIASES.get(name)
    if canonical is None:
        return None
    return _REGISTRY.get(canonical)


def registered_names() -> list[str]:
    return list(_REGISTRY.keys())


def registered_aliases() -> dict[str, str]:
    return dict(_ALIASES)


def resolve_spec(spec: str | Iterable[str]) -> list[str]:
    """Expand a ``--passes`` spec ("mem2reg,o1") into a flat list of names."""
    if isinstance(spec, str):
        raw = [part.strip() for part in spec.split(",")]
    else:
        raw = [str(part).strip() for part in spec]
    names: list[str] = []
    for part in raw:
        if not part:
            continue
        if part in PIPELINES:
            names.extend(PIPELINES[part])
        else:
            names.append(part)
    return names


def describe() -> list[tuple[str, str]]:
    """(name, description) for every registered pass -- used by ``--passes help``."""
    out = []
    for name, obj in _REGISTRY.items():
        out.append((name, str(getattr(obj, "description", "") or "")))
    return out


def run_passes(
    module: IRModule,
    spec: str | Iterable[str],
    *,
    initial: Iterable[str] = (),
    verbose: bool = False,
) -> list[str]:
    """Run the passes named by ``spec`` over ``module`` in order.

    ``initial`` is the set of invariants already true of the incoming module
    (the Python frontend hands over memory-SSA, so nothing is assumed by
    default). Returns the list of pass names that reported a change.
    """
    names = resolve_spec(spec)
    invariants: set[str] = set(initial)
    changed: list[str] = []

    for name in names:
        pass_obj = get_pass(name)
        if pass_obj is None:
            known = ", ".join(sorted(_REGISTRY)) or "(none)"
            raise PassError(f"unknown pass {name!r} (have: {known})")

        requires = frozenset(getattr(pass_obj, "requires", ()) or ())
        missing = requires - invariants
        if missing:
            raise PassError(
                f"pass {name!r} requires {', '.join(sorted(missing))} but the IR "
                f"does not provide it here -- reorder --passes (e.g. run "
                f"'mem2reg' first to establish 'ssa')"
            )

        did_change = bool(pass_obj.run(module))
        if did_change:
            changed.append(name)
        if verbose:
            print(f"asmpython: pass {name}: {'changed' if did_change else 'no change'}")

        preserves = frozenset(getattr(pass_obj, "preserves", ()) or ())
        provides = frozenset(getattr(pass_obj, "provides", ()) or ())
        invariants = (invariants & preserves) | provides

    return changed


# Built-in passes, registered after the registry functions exist.
from .arith import (
    FoldChainPass as _FoldChainPass,
    IdentityConvPass as _IdentityConvPass,
    NegFoldPass as _NegFoldPass,
    ReassociatePass as _ReassociatePass,
)
from .cfgopt import (
    BlockMergePass as _BlockMergePass,
    JumpThreadPass as _JumpThreadPass,
    PhiSimplifyPass as _PhiSimplifyPass,
)
from .constfold import ConstFoldPass as _ConstFoldPass
from .cse import CSEPass as _CSEPass
from .dce import DCEPass as _DCEPass
from .gvn import GVNPass as _GVNPass
from .ipo import (
    ADCEPass as _ADCEPass,
    GlobalDCEPass as _GlobalDCEPass,
    SinkPass as _SinkPass,
)
from .licm import LICMPass as _LICMPass, LoopDeletePass as _LoopDeletePass
from .mem2reg import Mem2RegPass as _Mem2RegPass
from .memopt import (
    DSEPass as _DSEPass,
    LoadElimPass as _LoadElimPass,
    StoreForwardPass as _StoreForwardPass,
)
from .peephole import PeepholePass as _PeepholePass
from .sccp import CmpFoldPass as _CmpFoldPass, SCCPPass as _SCCPPass
from .simplifycfg import SimplifyCFGPass as _SimplifyCFGPass

# ── SSA construction ──────────────────────────────────────────────────────
register_pass("mem2reg", _Mem2RegPass(), aliases=("promote", "sroa-lite"))
# ── scalar / arithmetic ───────────────────────────────────────────────────
register_pass("constfold", _ConstFoldPass(), aliases=("constprop", "fold"))
register_pass("sccp", _SCCPPass())
register_pass("peephole", _PeepholePass(), aliases=("instcombine", "simplify"))
register_pass("reassociate", _ReassociatePass(), aliases=("reassoc",))
register_pass("foldchain", _FoldChainPass())
register_pass("identityconv", _IdentityConvPass(), aliases=("idconv",))
register_pass("negfold", _NegFoldPass())
register_pass("cmpfold", _CmpFoldPass())
# ── redundancy ────────────────────────────────────────────────────────────
register_pass("cse", _CSEPass(), aliases=("earlycse",))
register_pass("gvn", _GVNPass())
register_pass("loadelim", _LoadElimPass(), aliases=("rle",))
register_pass("storeforward", _StoreForwardPass(), aliases=("stld",))
# ── dead code ─────────────────────────────────────────────────────────────
register_pass("dce", _DCEPass(), aliases=("deadcode",))
register_pass("adce", _ADCEPass())
register_pass("dse", _DSEPass())
register_pass("globaldce", _GlobalDCEPass())
# ── control flow / placement ──────────────────────────────────────────────
register_pass("simplifycfg", _SimplifyCFGPass(), aliases=("cfg", "simplify-cfg"))
register_pass("jumpthread", _JumpThreadPass(), aliases=("thread",))
register_pass("blockmerge", _BlockMergePass())
register_pass("phisimplify", _PhiSimplifyPass())
register_pass("sink", _SinkPass())
# ── loops ─────────────────────────────────────────────────────────────────
register_pass("licm", _LICMPass())
register_pass("loopdelete", _LoopDeletePass())

del (_ADCEPass, _BlockMergePass, _CmpFoldPass, _ConstFoldPass,
     _CSEPass, _DCEPass, _DSEPass, _FoldChainPass, _GlobalDCEPass,
     _IdentityConvPass, _JumpThreadPass, _LoadElimPass, _Mem2RegPass,
     _NegFoldPass, _PeepholePass, _PhiSimplifyPass, _ReassociatePass,
     _SCCPPass, _SimplifyCFGPass, _SinkPass, _StoreForwardPass,
     _GVNPass, _LICMPass, _LoopDeletePass)


__all__ = [
    "PIPELINES", "PassError", "describe", "get_pass", "register_pass",
    "registered_aliases", "registered_names", "resolve_spec", "run_passes",
]
