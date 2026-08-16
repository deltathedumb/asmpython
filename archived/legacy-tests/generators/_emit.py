"""Shared case-emission logic for the conformance-probe generators.

`gen_compat_cases.py` and `gen_vm_cases.py` each carry their own copy of this
loop. The probe generators added for the corpus expansion (`gen_std_cases.py`,
`gen_obj_cases.py`, `gen_flow_cases.py`, `gen_proto_cases.py`,
`gen_fmt_cases.py`) share this one instead, so the rules that make a generated
expectation trustworthy live in exactly one place.

Those rules:

* **The expectation comes from CPython, never from a human.** Every case is
  executed by the host interpreter and its real stdout becomes the `# expect:`
  block. A case whose source does not exit 0 is refused, not emitted -- a
  malformed probe fails loudly at generation instead of becoming a bogus
  requirement that some later phase "fixes" by matching the wrong output.

* **The marker precedes the block.** `runner._parse_expect` collects *every*
  `#` line following `# expect:` into the expected stdout, so a `# probes:`
  line placed after it silently becomes an extra expected output line and the
  case can never pass. Header order is enforced here rather than trusted to
  each generator.

* **The expectation must be reproducible.** Each case is run TWICE, in two
  separate interpreter processes, and is refused if the two runs disagree.
  This is not hypothetical: PYTHONHASHSEED is randomized per process, so a
  probe that prints a `set` of strings, or anything derived from `hash()`,
  produces a *different* correct answer on every run. Recording one of them
  pins the corpus to a fluke, and the resulting case fails forever against an
  implementation that is not wrong. The double run costs a second at
  generation time and is the only thing standing between a probe and an
  unfalsifiable expectation.

* **A probe must assert something.** A case that prints nothing passes
  trivially against any implementation, including one that does nothing at
  all. Empty stdout is refused.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class CaseSet:
    """A generator's collection of probes, keyed by case name.

    `marker` is the header keyword each emitted case carries (`probes` for the
    value-model-style probe sets, matching `gen_vm_cases.py`).
    """

    def __init__(self, marker: str = "probes") -> None:
        self.marker = marker
        self.cases: dict[str, tuple[str, str]] = {}

    def case(self, name: str, probes: str, src: str) -> None:
        if name in self.cases:
            raise ValueError(f"duplicate case name: {name}")
        body = src.strip() + "\n"
        # The emitted file is `header + body`, and _parse_expect keeps reading
        # `#` lines until the first line that is not one. A body whose first
        # line is a comment would therefore be absorbed into the expected
        # stdout -- the same trailing-marker trap, from the other side.
        if body.lstrip().startswith("#"):
            raise ValueError(
                f"{name}: case source must not begin with a comment line "
                "(it would be parsed as expected output)"
            )
        self.cases[name] = (probes, body)

    # -- emission -----------------------------------------------------------

    def _run(self, tmp: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(tmp)], capture_output=True, text=True, timeout=30
        )

    def emit(self, out_dir: Path) -> tuple[int, list[tuple[str, str]]]:
        """Write every runnable case to `out_dir`; return (written, skipped)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        skipped: list[tuple[str, str]] = []

        for name, (probes, src) in sorted(self.cases.items()):
            tmp = out_dir.parent / f"_gen_{name}.py"
            tmp.write_text(src, encoding="utf-8")
            try:
                first = self._run(tmp)
                if first.returncode != 0:
                    tail = (first.stderr.strip().splitlines() or ["?"])[-1]
                    skipped.append((name, f"CPython exited {first.returncode}: {tail}"))
                    continue
                # Second process: catches per-process hash randomization and
                # anything else that is not reproducible. See module docstring.
                second = self._run(tmp)
            finally:
                tmp.unlink(missing_ok=True)

            if second.returncode != 0 or second.stdout != first.stdout:
                skipped.append((name, "nondeterministic: two CPython runs disagree"))
                continue
            if not first.stdout.strip():
                skipped.append((name, "probe produced no output"))
                continue

            lines = first.stdout.splitlines()
            # Marker first, `# expect:` second, output last -- see docstring.
            header = [f"# {self.marker}: {probes}", "# expect:"]
            header += [f"# {ln}" if ln else "#" for ln in lines]
            (out_dir / f"{name}.py").write_text(
                "\n".join(header) + "\n" + src, encoding="utf-8"
            )
            written += 1

        return written, skipped


def main(cases: CaseSet, script: str, argv: list[str]) -> int:
    """Entry point shared by every probe generator."""
    if len(argv) < 2:
        print(f"usage: {script} <out dir>", file=sys.stderr)
        return 2
    out_dir = Path(argv[1])
    written, skipped = cases.emit(out_dir)
    print(f"wrote {written} probes to {out_dir}")
    for name, err in skipped:
        print(f"  SKIPPED {name}: {err}")
    return 0 if not skipped else 1
