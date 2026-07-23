from __future__ import annotations

import json

import asmpython
from asmpython import (
    AccessObject,
    C,
    Class,
    Package,
    Public,
    Subclass,
    abi,
    access,
    aligned,
    const,
    owned,
    override,
)


def test_common_extras_are_exported_from_package_root() -> None:
    assert asmpython.Public is Public
    assert asmpython.AccessObject is AccessObject
    assert asmpython.extras.Public is Public
    assert "Public" in asmpython.__all__
    assert "access" in asmpython.__all__


def test_access_presets_are_immutable_hashable_and_serializable() -> None:
    assert Public.public is True
    assert Package.same_package is True
    assert Subclass.same_class is True
    assert Subclass.subclasses is True
    assert Class.same_class is True
    assert hash(Public) == hash(Public)
    rendered = Public.to_dict()
    assert rendered["public"] is True
    assert json.loads(json.dumps(rendered))["public"] is True


def test_access_composition_and_direct_constructors() -> None:
    debugger = AccessObject.classes("tools.Debugger")
    policy = Package | debugger
    assert policy.composition == "any"
    assert policy.policies == (Package, debugger)
    plugin_only = AccessObject.subclasses_of("Plugin") & AccessObject.package(
        "myapp.plugins"
    )
    assert plugin_only.composition == "all"
    assert plugin_only.policies[0].subclasses is True


def test_decorators_attach_shared_compiler_metadata() -> None:
    @abi(C)
    @access(Public)
    @aligned(8)
    @override(required=True)
    def api(value: int) -> int:
        return value

    metadata = api.__asmpython_metadata__
    assert metadata["public"] is True
    assert metadata["access"] is Public
    assert metadata["abi"] is C
    assert metadata["aligned"] == 8
    assert metadata["override"] == {"required": True}


def test_qualifiers_work_in_runtime_annotations() -> None:
    assert repr(const[int]) == "const[int]"
    assert repr(owned[str]) == "owned[str]"


def test_compatibility_namespaces_remain_importable() -> None:
    from asmpython.extras.abi import C as CompatC
    from asmpython.extras.access import Public as CompatPublic
    from asmpython.extras.interrupts import interhandler
    from asmpython.extras.threading import atomic

    assert CompatC is C
    assert CompatPublic is Public
    assert callable(interhandler)
    assert repr(atomic[int]) == "atomic[int]"
