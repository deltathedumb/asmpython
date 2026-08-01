"""The oracle: derive each case's `# expect:` block by running it under CPython.

    python conformance/regen.py                 # fill in any case missing one
    python conformance/regen.py --force         # rewrite all of them
    python conformance/regen.py --filter numeric/

Expectations are DERIVED, never hand-written. A hand-typed expectation is how a
suite ends up asserting something the reference implementation does not
actually do -- and that failure mode is invisible, because the suite reports it
as an implementation bug forever.

Every case is run TWICE, in separate processes, and disagreement is refused
rather than recorded. That catches:
  - PYTHONHASHSEED, which is per-process: a case printing a set or dict of
    strings has a different correct answer each run;
  - id() or object addresses reaching output;
  - clock reads and anything else ambient.
A case that cannot produce the same answer twice has no business asserting one.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"

# Every field regen must PRESERVE when it rewrites a header. A field missing
# from this tuple is silently dropped on the next regen, so adding a header
# field to the harness without adding it here deletes it from every case.
_FIELD_ORDER = ("tier", "ref", "min-python", "skip")


def _min_python(path: Path) -> tuple[int, ...]:
    """The case's `# min-python:` as (major, minor), or () if absent."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("# expect:") or not s.startswith("#"):
            break
        key, _, val = s[1:].partition(":")
        if key.strip() == "min-python" and val.strip():
            try:
                return tuple(int(p) for p in val.strip().split("."))
            except ValueError:
                return ()
    return ()


def _run_once(path: Path, timeout: int) -> tuple[str, str, int]:
    cp = subprocess.run([sys.executable, str(path)], capture_output=True,
                        text=True, errors="replace", timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def _split_header(text: str) -> tuple[dict[str, str], str]:
    """-> (fields, body). The body is everything from the first non-header,
    non-expect line onward; the old expect block is discarded."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("# expect:"):
            i += 1
            while i < len(lines) and lines[i].strip().startswith("#"):
                i += 1
            break
        if s.startswith("#") and ":" in s:
            key, _, val = s[1:].partition(":")
            if key.strip() in _FIELD_ORDER:
                fields[key.strip()] = val.strip()
                i += 1
                continue
        if s.startswith("#") or not s:
            i += 1
            continue
        break
    return fields, "\n".join(lines[i:]).lstrip("\n")


def regen(path: Path, force: bool, timeout: int) -> str:
    text = path.read_text(encoding="utf-8")
    has_expect = "# expect:" in text
    if has_expect and not force:
        return "kept"

    fields, body = _split_header(text)
    if "tier" not in fields:
        return "SKIP no tier"

    tmp = path.with_suffix(".regen.tmp.py")
    tmp.write_text(body, encoding="utf-8")
    try:
        out1, err1, rc1 = _run_once(tmp, timeout)
        out2, _, rc2 = _run_once(tmp, timeout)
    except subprocess.TimeoutExpired:
        return "FAIL timeout under CPython"
    finally:
        tmp.unlink(missing_ok=True)

    if rc1 != 0:
        return f"FAIL CPython exit {rc1}: {(err1 or '').strip().splitlines()[-1:] or ['?']}"
    if out1 != out2 or rc1 != rc2:
        return ("FAIL nondeterministic under CPython -- two runs disagreed. "
                "Likely PYTHONHASHSEED (set/dict of strings), id(), or a clock.")

    header = [f"# {k}: {fields[k]}" for k in _FIELD_ORDER if k in fields]
    header.append("# expect:")
    header += [f"# {ln}" if ln else "#"
               for ln in out1.replace("\r\n", "\n").rstrip("\n").split("\n")]
    path.write_text("\n".join(header) + "\n" + body.rstrip("\n") + "\n",
                    encoding="utf-8")
    return "wrote"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--filter", default="")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    wrote = kept = failed = gated = 0
    for path in sorted(CASES.rglob("*.py")):
        cid = path.relative_to(CASES).with_suffix("").as_posix()
        if args.filter and args.filter not in cid:
            continue
        # Same gate the harness applies. Deriving an expectation for 3.12
        # syntax on 3.11 would record a SyntaxError as the expected output,
        # which is worse than not recording one: it would then be enforced.
        mp = _min_python(path)
        if mp and sys.version_info[:len(mp)] < mp:
            gated += 1
            continue
        status = regen(path, args.force, args.timeout)
        if status == "wrote":
            wrote += 1
        elif status == "kept":
            kept += 1
        else:
            failed += 1
            print(f"  {cid}: {status}")
    tail = f", {gated} gated by min-python" if gated else ""
    print(f"regen: {wrote} written, {kept} kept, {failed} refused{tail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
