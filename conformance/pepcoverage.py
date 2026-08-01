"""Report PEP coverage against the canonical index.

    python conformance/pepcoverage.py            # summary
    python conformance/pepcoverage.py --missing  # what is not covered, and why not
    python conformance/pepcoverage.py --check    # nonzero if an UNCLASSIFIED gap exists

Coverage used to be a number I asserted from memory, and that produced a real
defect: a case named for PEP 3110 (`except E, x:` is a SyntaxError) recorded the
opposite result, because PEP 758 made the comma form legal again in 3.14. It had
no `min-python`, so it would have failed the 3.11-3.13 CI matrix. Reading the
index would have caught it structurally; recalling the index did not.

So the rule here is: every in-scope PEP is either COVERED by a case or
explicitly EXCLUDED with a reason. "Not thought about yet" is a third state and
`--check` fails on it, because an unexamined gap and a deliberate omission look
identical in a coverage percentage.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "peps" / "index.json"
CASES = ROOT / "cases" / "pep"

#: A PEP is IN SCOPE for this suite when it is a Standards Track proposal that
#: shipped (Final/Accepted/Active) in some Python version. Everything else --
#: Rejected, Withdrawn, Deferred, Draft, Informational, Process -- either never
#: became behaviour or was never behaviour to begin with.
#:
#: Superseded is excluded too, but a superseded PEP may still be worth a case:
#: PEP 563 is superseded by 649/749 and is covered anyway, because
#: `from __future__ import annotations` still works and still has to.
IN_SCOPE_STATUS = ("Final", "Accepted", "Active")

#: PEPs whose behaviour is tested under a NON-pep path. The tree is organised by
#: what a case is about, not by which document blessed it, so several PEPs are
#: covered where they belong -- `except E as e` scoping lives in exceptions/,
#: not in a directory named after PEP 3110.
#:
#: Recorded rather than duplicated: adding a second copy under pep/ would make
#: the coverage number look better while testing nothing new, and two copies
#: drift. Each path is VERIFIED to exist, so this cannot rot into a list of
#: claims about files that were renamed away.
COVERED_ELSEWHERE: dict[int, str] = {
    415: "pep/0409-suppress-context/raise-from-none",
    424: "datamodel/length-hint-is-advisory",
    562: "datamodel/module-level-getattr",
    678: "exceptions/notes-are-attached",
    3101: "text/format/nested-and-auto-numbering",
    3102: "functions/keyword-only-and-positional-only",
    3104: "functions/nonlocal-rebinds-enclosing",
    3109: "exceptions/raise-from-sets-cause",
    3118: "pep/0688-buffer-protocol/buffer-dunder",
    3135: "quirks/zero-argument-super-needs-class-cell",
    3137: "text/bytes/bytearray-is-mutable",
}

#: Why an in-scope PEP has no case. Each entry is a promise that the omission
#: was considered, not overlooked. Keep the reasons specific enough that
#: someone can disagree with one.
EXCLUDED: dict[int, str] = {}


def _exclude(reason: str, *numbers: int) -> None:
    for n in numbers:
        EXCLUDED[n] = reason


_exclude(
    "C API only -- not observable from Python source, which is all this suite "
    "can execute",
    311, 353, 384, 436, 445, 489, 523, 539, 573, 587, 590, 620, 623, 624, 652,
    670, 687, 689, 697, 699, 730, 737, 738, 741, 757, 782, 788, 793, 800, 803,
    820, 3121, 3123, 3149,
)
_exclude(
    "packaging, distribution or build metadata -- outside the language",
    229, 250, 301, 376, 425, 441, 488, 552, 561, 566, 632, 685, 700, 704, 706,
    714, 721, 739, 784, 829, 3147,
)
_exclude(
    "requires multiple modules or a real filesystem; cases are single files by "
    "design (see README: no imports beyond the stdlib, no files)",
    273, 302, 328, 338, 366, 420, 451, 471,
)
_exclude(
    "interpreter startup, environment or locale configuration -- not "
    "expressible in a single source file",
    235, 277, 370, 397, 405, 486, 524, 528, 529, 538, 540, 597, 686,
)
_exclude(
    "networking or TLS policy -- no network access in a case",
    466, 476, 493, 644,
)
_exclude(
    "implementation internals with no defined observable behaviour; pinning "
    "them would hold asmpython to a CPython accident",
    412, 442, 456, 509, 617, 626, 659, 683, 684, 703, 744, 768, 779, 831,
)
_exclude(
    "stdlib module addition -- the module's API is library surface, not "
    "language behaviour",
    282, 305, 324, 371, 389, 391, 417, 418, 454, 506, 564, 574, 799, 3144,
    3148, 3154, 3156,
)
_exclude(
    "not yet released in the CPython this suite runs against; add when it ships",
    661, 728, 747, 791, 798, 810, 814,
)
_exclude(
    "operating-system or interpreter-runtime facility with no single-file "
    "Python-level behaviour to pin",
    446, 475, 734,
)
_exclude(
    "Python 2 only -- this suite targets Python 3",
    100, 201, 208, 214, 217, 221, 223, 230, 232, 253, 261, 263, 264, 278, 292,
    293, 307, 331, 358, 383, 3108, 3111,
)


def load() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["peps"]


def covered() -> tuple[set[int], list[str]]:
    """-> (covered PEP numbers, broken COVERED_ELSEWHERE paths).

    A cross-reference that points at a file which no longer exists is worse
    than no cross-reference: it reports the PEP as covered forever.
    """
    out: set[int] = set()
    if CASES.exists():
        for entry in CASES.iterdir():
            m = re.match(r"(\d+)", entry.name)
            if m:
                out.add(int(m.group(1)))
    broken = []
    for num, rel in COVERED_ELSEWHERE.items():
        if (ROOT / "cases" / (rel + ".py")).exists():
            out.add(num)
        else:
            broken.append(f"{num} -> {rel}")
    return out, broken


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--missing", action="store_true",
                    help="list in-scope PEPs with no case")
    ap.add_argument("--check", action="store_true",
                    help="exit nonzero if any gap is neither covered nor excluded")
    args = ap.parse_args(argv)

    peps = load()
    have, broken = covered()
    scope = [p for p in peps
             if p["type"] == "Standards Track"
             and p["status"] in IN_SCOPE_STATUS
             and p["python_version"]]
    scope_nums = {p["number"] for p in scope}

    hit = scope_nums & have
    gap = [p for p in scope if p["number"] not in have]
    unclassified = [p for p in gap if p["number"] not in EXCLUDED]
    extra = sorted(have - scope_nums)

    if broken:
        print("BROKEN cross-references (COVERED_ELSEWHERE points at a missing "
              f"case): {len(broken)}")
        for b in broken:
            print("   ", b)
        print()

    pct = 100.0 * len(hit) / len(scope) if scope else 100.0
    print(f"in scope (Standards Track, {'/'.join(IN_SCOPE_STATUS)}, versioned): "
          f"{len(scope)}")
    print(f"  covered by a case : {len(hit)} ({pct:.1f}%)")
    print(f"  excluded, reasoned: {len(gap) - len(unclassified)}")
    print(f"  UNCLASSIFIED      : {len(unclassified)}")
    if extra:
        print(f"\nalso covered, outside the in-scope set: {extra}")
        for n in extra:
            rec = next((p for p in peps if p["number"] == n), None)
            if rec:
                print(f"  {n}: {rec['status']} -- {rec['title'][:60]}")

    if args.missing or unclassified:
        if unclassified:
            print(f"\nUNCLASSIFIED -- neither covered nor excluded ({len(unclassified)}):")
            for p in sorted(unclassified, key=lambda p: p["number"]):
                print(f"  {p['number']:5} [{p['python_version']:>6}] {p['title'][:70]}")
        if args.missing:
            by_reason: dict[str, list[dict]] = {}
            for p in gap:
                r = EXCLUDED.get(p["number"])
                if r:
                    by_reason.setdefault(r, []).append(p)
            for reason, group in sorted(by_reason.items()):
                print(f"\nexcluded -- {reason} ({len(group)}):")
                print("  " + ", ".join(str(p["number"]) for p in
                                       sorted(group, key=lambda p: p["number"])))

    if args.check and unclassified:
        print("\nFAIL: every in-scope PEP must be covered or explicitly excluded")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
