"""Compile one case (or one snippet) with the asmpython shim and show both sides.

A triage tool, not part of scoring. `harness.py --filter` runs a case and tells
you it failed; this prints the compiler's own stderr, which is where a refusal
explains itself, and diffs want/got line by line.

    python conformance/try.py numeric/int/pow-with-modulus
    python conformance/try.py -e "print(2 ** 100)"
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import harness  # noqa: E402


def _load_shim(name: str):
    spec = importlib.util.spec_from_file_location(
        f"shim_{name}", _HERE / "shims" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?", help="case id, e.g. numeric/int/pow-with-modulus")
    ap.add_argument("-e", "--expr", help="compile this source instead of a case")
    ap.add_argument("--shim", default="asmpython")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    shim = _load_shim(args.shim)
    if args.expr:
        tmp = Path(tempfile.mkdtemp()) / "snippet.py"
        tmp.write_text(args.expr + "\n", encoding="utf-8")
        path, want = tmp, None
    else:
        if not args.case:
            ap.error("give a case id or -e SOURCE")
        path = _HERE / "cases" / (args.case + ".py")
        if not path.exists():
            raise SystemExit(f"no such case: {path}")
        want = harness.parse_case(path).expect.splitlines()

    try:
        out, err, rc = shim.run(str(path), args.timeout)
    except TimeoutError as exc:
        print(f"TIMEOUT: {exc}")
        return 2

    if rc is None:
        print("=== REFUSED ===")
        print(err.strip()[-6000:])
        return 1
    if err.strip():
        print("=== stderr ===")
        print(err.strip()[-4000:])
    got = out.splitlines()
    if want is None:
        print("=== stdout ===")
        print("\n".join(got))
        return 0
    print("=== want / got ===")
    for i in range(max(len(want), len(got))):
        w = want[i] if i < len(want) else "<missing>"
        g = got[i] if i < len(got) else "<missing>"
        print(f"{'  ' if w == g else '! '}{w!r:40} {g!r}")
    return 0 if got == want else 1


if __name__ == "__main__":
    raise SystemExit(main())
