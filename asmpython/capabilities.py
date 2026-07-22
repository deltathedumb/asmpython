"""Public capability and dependency contracts for backends and linkers.

Extensions should describe what their components can actually consume and emit:

    from asmpython import CapabilitySet, Dependency

    capabilities = CapabilitySet(
        targets=("windows-x64", "linux-x64"),
        output_types=("executable", "shared-library"),
        sanitizers=("address", "undefined"),
        speedy_lossy=True,
        abi=True,
        ffi=True,
    )
    dependencies = (Dependency.executable("gcc", reason="links native output"),)

A literal ``"*"`` means that the component accepts any value in that category.
Unknown/unspecified capability sets deliberately default to wildcard compatibility
so existing plugins remain source-compatible; production extensions should publish
an explicit contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


_VALID_DEPENDENCY_KINDS = frozenset({
    "executable", "python-module", "environment", "file",
    "extension", "backend", "linker",
})


def _tuple(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class Dependency:
    """One runtime/build dependency required by a backend or linker."""

    kind: str
    name: str
    version: str | None = None
    optional: bool = False
    reason: str = ""
    probe: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in _VALID_DEPENDENCY_KINDS:
            raise ValueError(
                f"unknown dependency kind {self.kind!r}; choose from "
                + ", ".join(sorted(_VALID_DEPENDENCY_KINDS))
            )
        if not self.name:
            raise ValueError("dependency name must not be empty")
        object.__setattr__(self, "probe", _tuple(self.probe))

    @classmethod
    def from_value(cls, value: "Dependency | Mapping[str, Any] | str") -> "Dependency":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls("executable", value)
        if isinstance(value, Mapping):
            return cls(
                kind=str(value.get("kind", "executable")),
                name=str(value.get("name", "")),
                version=(None if value.get("version") is None else str(value["version"])),
                optional=bool(value.get("optional", False)),
                reason=str(value.get("reason", "")),
                probe=_tuple(value.get("probe")),
            )
        raise TypeError(f"cannot convert {type(value).__name__} to Dependency")

    @classmethod
    def executable(cls, name: str, **kwargs: Any) -> "Dependency":
        return cls("executable", name, **kwargs)

    @classmethod
    def python_module(cls, name: str, **kwargs: Any) -> "Dependency":
        return cls("python-module", name, **kwargs)

    @classmethod
    def environment(cls, name: str, **kwargs: Any) -> "Dependency":
        return cls("environment", name, **kwargs)

    @classmethod
    def file(cls, path: str | Path, **kwargs: Any) -> "Dependency":
        return cls("file", str(path), **kwargs)

    @classmethod
    def extension(cls, extension_id: str, **kwargs: Any) -> "Dependency":
        return cls("extension", extension_id, **kwargs)

    @classmethod
    def backend(cls, name: str, **kwargs: Any) -> "Dependency":
        return cls("backend", name, **kwargs)

    @classmethod
    def linker(cls, name: str, **kwargs: Any) -> "Dependency":
        return cls("linker", name, **kwargs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "optional": self.optional,
            "reason": self.reason,
            "probe": list(self.probe),
        }


@dataclass(frozen=True)
class CapabilitySet:
    """Declarative compatibility contract for one backend or linker."""

    api_version: int = 1
    ir_versions: tuple[str, ...] = ("*",)
    targets: tuple[str, ...] = ("*",)
    output_types: tuple[str, ...] = ("*",)
    sanitizers: tuple[str, ...] = ()
    debug_formats: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    speedy_lossy: bool = True
    abi: bool = False
    ffi: bool = False
    dependencies: tuple[Dependency, ...] = ()

    def __post_init__(self) -> None:
        if self.api_version < 1:
            raise ValueError("capability api_version must be positive")
        for name in (
            "ir_versions", "targets", "output_types", "sanitizers",
            "debug_formats", "features",
        ):
            object.__setattr__(self, name, _tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "dependencies",
            tuple(Dependency.from_value(item) for item in self.dependencies),
        )

    @classmethod
    def from_value(
        cls,
        value: "CapabilitySet | Mapping[str, Any] | None",
        *,
        dependencies: Iterable[Dependency | Mapping[str, Any] | str] = (),
    ) -> "CapabilitySet":
        extra_dependencies = tuple(Dependency.from_value(item) for item in dependencies)
        if value is None:
            return cls(dependencies=extra_dependencies)
        if isinstance(value, cls):
            if not extra_dependencies:
                return value
            return cls(**{
                **value.as_dict(),
                "dependencies": (*value.dependencies, *extra_dependencies),
            })
        if isinstance(value, Mapping):
            embedded = tuple(
                Dependency.from_value(item) for item in value.get("dependencies", ())
            )
            return cls(
                api_version=int(value.get("api_version", 1)),
                ir_versions=_tuple(value.get("ir_versions"), default=("*",)),
                targets=_tuple(value.get("targets"), default=("*",)),
                output_types=_tuple(value.get("output_types"), default=("*",)),
                sanitizers=_tuple(value.get("sanitizers")),
                debug_formats=_tuple(value.get("debug_formats")),
                features=_tuple(value.get("features")),
                speedy_lossy=bool(value.get("speedy_lossy", True)),
                abi=bool(value.get("abi", False)),
                ffi=bool(value.get("ffi", False)),
                dependencies=(*embedded, *extra_dependencies),
            )
        raise TypeError(f"cannot convert {type(value).__name__} to CapabilitySet")

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "ir_versions": list(self.ir_versions),
            "targets": list(self.targets),
            "output_types": list(self.output_types),
            "sanitizers": list(self.sanitizers),
            "debug_formats": list(self.debug_formats),
            "features": list(self.features),
            "speedy_lossy": self.speedy_lossy,
            "abi": self.abi,
            "ffi": self.ffi,
            "dependencies": [item.as_dict() for item in self.dependencies],
        }


__all__ = ["CapabilitySet", "Dependency"]
