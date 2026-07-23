"""Compiler-visible metadata and runtime shims for ASMPython extensions.

The canonical import surface is the package root::

    from asmpython import Public, access, abi, C

``asmpython.extras`` remains as a compatibility namespace.  Every decorator
stores immutable-ish, serializable metadata on ``__asmpython_metadata__`` so
CPython tooling and the native compiler can consume the same declarations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, TypeVar


T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


def _qualified_name(value: object) -> str:
    if isinstance(value, str):
        return value
    module = getattr(value, "__module__", "")
    qualname = getattr(value, "__qualname__", getattr(value, "__name__", repr(value)))
    return f"{module}.{qualname}" if module else str(qualname)


def _freeze_names(values: object) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, (str, bytes)):
        return frozenset((_qualified_name(values),))
    try:
        return frozenset(_qualified_name(value) for value in values)  # type: ignore[arg-type]
    except TypeError:
        return frozenset((_qualified_name(values),))


@dataclass(frozen=True, slots=True, init=False)
class AccessObject:
    """Immutable, hashable access-policy description.

    Permissions are additive for ordinary policies. Composite policies created
    with ``|`` and ``&`` retain their component policies for build manifests and
    static/dynamic enforcement.
    """

    public: bool
    same_module: bool
    same_package: bool
    same_class: bool
    same_instance: bool
    subclasses: bool
    module_refs: frozenset[str]
    package_refs: frozenset[str]
    class_refs: frozenset[str]
    function_refs: frozenset[str]
    references: frozenset[str]
    enforcement: str
    reflection: str
    composition: str
    policies: tuple["AccessObject", ...]

    def __init__(
        self,
        *,
        public: bool = False,
        same_module: bool = False,
        same_package: bool = False,
        same_class: bool = False,
        same_instance: bool = False,
        subclasses: bool = False,
        modules: object = (),
        packages: object = (),
        classes: object = (),
        functions: object = (),
        references: object = (),
        enforcement: str = "error",
        reflection: str = "enforce",
        composition: str = "any",
        policies: tuple["AccessObject", ...] = (),
    ) -> None:
        if enforcement not in {"error", "warn", "audit", "none"}:
            raise ValueError(f"invalid enforcement mode: {enforcement!r}")
        if reflection not in {"enforce", "warn", "allow"}:
            raise ValueError(f"invalid reflection mode: {reflection!r}")
        if composition not in {"any", "all"}:
            raise ValueError(f"invalid access composition: {composition!r}")
        object.__setattr__(self, "public", bool(public))
        object.__setattr__(self, "same_module", bool(same_module))
        object.__setattr__(self, "same_package", bool(same_package))
        object.__setattr__(self, "same_class", bool(same_class))
        object.__setattr__(self, "same_instance", bool(same_instance))
        object.__setattr__(self, "subclasses", bool(subclasses))
        object.__setattr__(self, "module_refs", _freeze_names(modules))
        object.__setattr__(self, "package_refs", _freeze_names(packages))
        object.__setattr__(self, "class_refs", _freeze_names(classes))
        object.__setattr__(self, "function_refs", _freeze_names(functions))
        object.__setattr__(self, "references", _freeze_names(references))
        object.__setattr__(self, "enforcement", enforcement)
        object.__setattr__(self, "reflection", reflection)
        object.__setattr__(self, "composition", composition)
        object.__setattr__(self, "policies", tuple(policies))

    @classmethod
    def module(cls, module: object) -> "AccessObject":
        return cls(modules=(module,))

    @classmethod
    def modules(cls, *modules: object) -> "AccessObject":
        return cls(modules=modules)

    @classmethod
    def package(cls, package: object) -> "AccessObject":
        return cls(packages=(package,))

    @classmethod
    def packages(cls, *packages: object) -> "AccessObject":
        return cls(packages=packages)

    @classmethod
    def cls(cls, class_: object) -> "AccessObject":
        return cls(classes=(class_,))

    @classmethod
    def classes(cls, *classes: object) -> "AccessObject":
        return cls(classes=classes)

    @classmethod
    def subclasses_of(cls, class_: object) -> "AccessObject":
        return cls(classes=(class_,), same_class=True, subclasses=True)

    @classmethod
    def function(cls, function: object) -> "AccessObject":
        return cls(functions=(function,))

    @classmethod
    def functions(cls, *functions: object) -> "AccessObject":
        return cls(functions=functions)

    @classmethod
    def any_of(cls, *policies: "AccessObject") -> "AccessObject":
        return cls(composition="any", policies=tuple(policies))

    @classmethod
    def all_of(cls, *policies: "AccessObject") -> "AccessObject":
        return cls(composition="all", policies=tuple(policies))

    @classmethod
    def ref(cls, path: str) -> "AccessObject":
        return cls(references=(path,))

    def __or__(self, other: "AccessObject") -> "AccessObject":
        if not isinstance(other, AccessObject):
            return NotImplemented
        return AccessObject.any_of(self, other)

    def __and__(self, other: "AccessObject") -> "AccessObject":
        if not isinstance(other, AccessObject):
            return NotImplemented
        return AccessObject.all_of(self, other)

    def to_dict(self) -> dict[str, object]:
        return {
            "public": self.public,
            "same_module": self.same_module,
            "same_package": self.same_package,
            "same_class": self.same_class,
            "same_instance": self.same_instance,
            "subclasses": self.subclasses,
            "modules": sorted(self.module_refs),
            "packages": sorted(self.package_refs),
            "classes": sorted(self.class_refs),
            "functions": sorted(self.function_refs),
            "references": sorted(self.references),
            "enforcement": self.enforcement,
            "reflection": self.reflection,
            "composition": self.composition,
            "policies": [policy.to_dict() for policy in self.policies],
        }


Public = AccessObject(public=True)
Module = AccessObject(same_module=True)
Package = AccessObject(same_package=True)
Subclass = AccessObject(same_class=True, subclasses=True)
Class = AccessObject(same_class=True)
Instance = AccessObject(same_instance=True)
NoAccess = AccessObject()


@dataclass(frozen=True, slots=True)
class ABIObject:
    name: str
    calling_convention: str = "platform"
    infer: bool = True
    options: tuple[tuple[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "calling_convention": self.calling_convention,
            "infer": self.infer,
            "options": dict(self.options),
        }


AutoABI = ABIObject("auto", infer=True)
C = ABIObject("c", calling_convention="platform", infer=True)
System = ABIObject("system", calling_convention="platform", infer=True)
ASMPython = ABIObject("asmpython", calling_convention="asmpython", infer=True)


@dataclass(frozen=True, slots=True)
class QualifiedType:
    qualifier: str
    arguments: tuple[object, ...]

    def __repr__(self) -> str:
        args = ", ".join(getattr(arg, "__name__", repr(arg)) for arg in self.arguments)
        return f"{self.qualifier}[{args}]"


@dataclass(frozen=True, slots=True)
class Qualifier:
    name: str

    def __getitem__(self, item: object) -> QualifiedType:
        args = item if isinstance(item, tuple) else (item,)
        return QualifiedType(self.name, tuple(args))

    def __repr__(self) -> str:
        return self.name


const = Qualifier("const")
readonly = Qualifier("readonly")
limited = Qualifier("limited")
immutable = Qualifier("immutable")
shimmutable = Qualifier("shimmutable")
volatile = Qualifier("volatile")
atomic = Qualifier("atomic")
threadlocal = Qualifier("threadlocal")
owned = Qualifier("owned")
borrowed = Qualifier("borrowed")
pinned = Qualifier("pinned")
unmanaged = Qualifier("unmanaged")
noescape = Qualifier("noescape")
notnone = Qualifier("notnone")


def _metadata(target: object) -> dict[str, object]:
    current = getattr(target, "__asmpython_metadata__", None)
    result = dict(current) if isinstance(current, Mapping) else {}
    setattr(target, "__asmpython_metadata__", result)
    return result


def _mark(target: T, name: str, value: object = True) -> T:
    _metadata(target)[name] = value
    return target


def _simple(name: str) -> Callable[[T], T]:
    def decorator(target: T) -> T:
        return _mark(target, name)
    decorator.__name__ = name
    return decorator


def access(policy: AccessObject, *, broaden: bool = False) -> Callable[[T], T]:
    if not isinstance(policy, AccessObject):
        raise TypeError("access() requires an AccessObject")

    def decorator(target: T) -> T:
        _mark(target, "access", policy)
        _mark(target, "access_broaden", bool(broaden))
        _mark(target, "public", policy.public)
        return target

    return decorator


def abi(value: ABIObject | str = AutoABI, **options: object) -> Callable[[T], T]:
    if isinstance(value, str):
        value = ABIObject(value, options=tuple(sorted(options.items())))
    elif options:
        value = ABIObject(
            value.name,
            calling_convention=value.calling_convention,
            infer=value.infer,
            options=tuple(sorted((*value.options, *options.items()))),
        )

    def decorator(target: T) -> T:
        return _mark(target, "abi", value)

    return decorator


def _optional(name: str, target: T | None = None, **options: object):
    def decorator(value: T) -> T:
        return _mark(value, name, dict(options) if options else True)
    return decorator(target) if target is not None else decorator


def final(target: T) -> T:
    return _mark(target, "final")


def override(target: T | None = None, *, required: bool = False):
    return _optional("override", target, required=required)


def sealed(target: T) -> T:
    return _mark(target, "sealed")


def frozen(target: T | None = None, *, deep: bool = False):
    return _optional("frozen", target, deep=deep)


def stability(**properties: object) -> Callable[[T], T]:
    return lambda target: _mark(target, "stability", dict(properties))


def since(version: str) -> Callable[[T], T]:
    return lambda target: _mark(target, "since", version)


def muse(value: T | str | None = None):
    if callable(value):
        return _mark(value, "muse", True)
    reason = value
    return lambda target: _mark(target, "muse", reason if reason is not None else True)


def raises(*exceptions: object) -> Callable[[T], T]:
    return lambda target: _mark(target, "raises", tuple(_qualified_name(e) for e in exceptions))


def precond(*conditions: Callable[..., bool]) -> Callable[[T], T]:
    return lambda target: _mark(target, "precond", conditions)


def postcond(*conditions: Callable[..., bool]) -> Callable[[T], T]:
    return lambda target: _mark(target, "postcond", conditions)


def invariant(*conditions: Callable[..., bool]) -> Callable[[T], T]:
    return lambda target: _mark(target, "invariant", conditions)


def enforced(target: T | None = None, *, deep: bool = False):
    return _optional("enforced", target, deep=deep)


def inline(target: T | None = None, *, required: bool = False):
    return _optional("inline", target, required=required)


def aligned(bytes_: int) -> Callable[[T], T]:
    if bytes_ <= 0 or bytes_ & (bytes_ - 1):
        raise ValueError("alignment must be a positive power of two")
    return lambda target: _mark(target, "aligned", bytes_)


def threadsafe(value: bool = True) -> Callable[[T], T]:
    return lambda target: _mark(target, "threadsafe", bool(value))


def interhandler(**properties: object) -> Callable[[T], T]:
    return lambda target: _mark(target, "interhandler", dict(properties))


def discard(value: object) -> None:
    del value


pure = _simple("pure")
nomutate = _simple("nomutate")
noexception = _simple("noexception")
nativeonly = _simple("nativeonly")
dynamiconly = _simple("dynamiconly")
noinline = _simple("noinline")
hot = _simple("hot")
cold = _simple("cold")
overload = _simple("overload")
skipoptimize = _simple("skipoptimize")
trace = _simple("trace")
profile = _simple("profile")
packed = _simple("packed")
transparent = _simple("transparent")
opaque = _simple("opaque")
sync = _simple("sync")
mainonly = _simple("mainonly")
sigsafe = _simple("sigsafe")
intersafe = _simple("intersafe")


__all__ = [
    "AccessObject", "Public", "Module", "Package", "Subclass", "Class",
    "Instance", "NoAccess", "access", "ABIObject", "AutoABI", "C", "System",
    "ASMPython", "abi", "QualifiedType", "Qualifier", "const", "readonly",
    "limited", "immutable", "shimmutable", "volatile", "atomic", "threadlocal",
    "owned", "borrowed", "pinned", "unmanaged", "noescape", "notnone", "final",
    "override", "sealed", "frozen", "stability", "since", "muse", "discard",
    "pure", "nomutate", "noexception", "raises", "precond", "postcond",
    "invariant", "enforced", "nativeonly", "dynamiconly", "inline", "noinline",
    "hot", "cold", "overload", "skipoptimize", "trace", "profile", "packed",
    "aligned", "transparent", "opaque", "sync", "threadsafe", "mainonly",
    "sigsafe", "intersafe", "interhandler",
]
