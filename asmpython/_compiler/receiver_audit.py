"""Locate the semantic rule enforcing receiver parameter spelling."""

from __future__ import annotations

from pathlib import Path

from . import sema


def main() -> int:
    path = Path(sema.__file__).resolve()
    lines = path.read_text(encoding="utf-8").splitlines()
    matches: list[int] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if (
            "first parameter" in lowered
            or "must take" in lowered
            or "params[0]" in lowered
        ):
            matches.append(index)

    for index in matches:
        start = max(0, index - 15)
        end = min(len(lines), index + 16)
        print("MATCH", index + 1)
        for line_number in range(start, end):
            print(str(line_number + 1) + ":" + lines[line_number])

    print("FOUND", len(matches))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
