"""Backward-compatible structured-target parsing installed by the CLI facade."""
from __future__ import annotations

from pathlib import Path

from . import target_triple as _base


_SOURCE_SUFFIXES = {".py", ".json", ".toml", ".apir"}


def _looks_like_source(value: str) -> bool:
    path = Path(value)
    return path.suffix.lower() in _SOURCE_SUFFIXES or path.exists()


def normalize_target_argv(argv: list[str]):
    output: list[str] = []
    selected = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--target="):
            value = token.split("=", 1)[1]
            try:
                triple = _base.TargetTriple.parse(value)
            except _base.TargetTripleError:
                output.append(token)
            else:
                selected = triple
                output.extend(("--target", triple.canonical))
            index += 1
            continue
        if token != "--target":
            output.append(token)
            index += 1
            continue
        if index + 1 >= len(argv):
            raise _base.TargetTripleError("--target requires a value")

        first = argv[index + 1]
        candidates = argv[index + 1 : index + 4]
        can_be_triple = (
            len(candidates) == 3
            and not any(item.startswith("-") for item in candidates)
            and not _looks_like_source(candidates[1])
            and not _looks_like_source(candidates[2])
        )
        if can_be_triple:
            try:
                triple = _base.TargetTriple.parse(candidates)
            except _base.TargetTripleError:
                pass
            else:
                selected = triple
                output.extend(("--target", triple.canonical))
                index += 4
                continue

        # Preserve historical argparse behavior: consume exactly one target
        # value and leave following positionals/options untouched.
        try:
            triple = _base.TargetTriple.parse(first)
        except _base.TargetTripleError:
            output.extend(("--target", first))
        else:
            selected = triple
            output.extend(("--target", triple.canonical))
        index += 2
    return output, selected


_base.normalize_target_argv = normalize_target_argv


__all__ = ["normalize_target_argv"]
