"""Structured target triples and CLI normalization.

The public spelling accepts three tokens::

    --target pc windows msvc

Internally the triple is normalized to ``pc-windows-msvc`` so the historical
single-value parser and extension APIs can continue using ordinary strings.
"""
from __future__ import annotations

from dataclasses import dataclass


class TargetTripleError(ValueError):
    pass


_ALIASES = {
    "win": "windows",
    "win32": "windows",
    "win64": "windows",
    "linux-gnu": "linux",
    "osx": "macos",
    "darwin": "macos",
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
}


@dataclass(frozen=True, order=True)
class TargetTriple:
    platform: str
    system: str
    abi: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("platform", self.platform),
            ("system", self.system),
            ("abi", self.abi),
        ):
            if not value or not value.replace("_", "").replace("-", "").isalnum():
                raise TargetTripleError(
                    f"target {field_name} must contain letters, digits, '_' or '-'"
                )

    @property
    def canonical(self) -> str:
        return f"{self.platform}-{self.system}-{self.abi}"

    def as_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "system": self.system,
            "abi": self.abi,
            "canonical": self.canonical,
        }

    @classmethod
    def parse(cls, value: str | list[str] | tuple[str, ...]) -> "TargetTriple":
        if isinstance(value, str):
            raw = value.replace(",", " ").split()
            if len(raw) == 1:
                raw = raw[0].split("-")
        else:
            raw = list(value)
        if len(raw) != 3:
            raise TargetTripleError(
                "target must contain exactly three parts: PLATFORM SYSTEM ABI "
                "(example: --target pc windows msvc)"
            )
        normalized = [_ALIASES.get(item.strip().lower(), item.strip().lower()) for item in raw]
        return cls(*normalized)


def normalize_target_argv(argv: list[str]) -> tuple[list[str], TargetTriple | None]:
    """Normalize three-token ``--target`` syntax into one canonical value.

    A legacy one-token target remains untouched unless it already contains a
    complete three-part triple. Two unflagged values are rejected rather than
    ambiguously consumed as a source/output path.
    """

    output: list[str] = []
    selected: TargetTriple | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--target="):
            value = token.split("=", 1)[1]
            try:
                triple = TargetTriple.parse(value)
            except TargetTripleError:
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
            raise TargetTripleError("--target requires a value or three target parts")
        parts: list[str] = []
        cursor = index + 1
        while cursor < len(argv) and not argv[cursor].startswith("-") and len(parts) < 3:
            parts.append(argv[cursor])
            cursor += 1
        if len(parts) == 3:
            selected = TargetTriple.parse(parts)
            output.extend(("--target", selected.canonical))
            index = cursor
            continue
        if len(parts) == 2:
            raise TargetTripleError(
                "--target received two parts; provide PLATFORM SYSTEM ABI"
            )
        value = parts[0]
        try:
            triple = TargetTriple.parse(value)
        except TargetTripleError:
            output.extend(("--target", value))
        else:
            selected = triple
            output.extend(("--target", triple.canonical))
        index = cursor
    return output, selected


__all__ = ["TargetTriple", "TargetTripleError", "normalize_target_argv"]
