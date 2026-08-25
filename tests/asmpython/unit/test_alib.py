"""The architecture libraries: shape, honesty, and how much is real.

MOST OF THIS IS STILL DECLARATION. Every alib names what its machine can do;
one backend lowers any of it. So the assertions here are about the
DECLARATION being well-formed and about the lowered fraction being written
down -- `TestTheStubStateIsReported` holds the count, and moving it is how
progress gets recorded rather than noticed later.

That class already earned its keep once: it asserted "nothing is lowered yet",
x86-64 grew an encoder, and it fired the same day.
"""
from __future__ import annotations

from tests import harness

from asmpython.backend import alib

ARCHES = sorted(alib.all_alibs())


def get(arch):
    """The alib for an architecture, or a clear failure.

    A helper rather than a registry call: an alib hangs off the BACKEND that
    can emit it now, so there is no separate table to ask.
    """
    found = alib.by_arch(arch)
    assert found is not None, (
        f"no backend declares an alib for {arch!r}; "
        f"have {sorted(alib.all_alibs())}")
    return found

#: What the user asked asmpython to support. Written out rather than derived
#: from the registry, because a test that reads the same table it checks
#: cannot notice an architecture that was dropped.
REQUESTED = ["apir", "arm32", "arm64", "c", "jvm", "llvm", "pybc", "wasm",
             "x86_32", "x86_64"]


class TestEveryArchitectureHasOne:
    def test_the_requested_set_is_present(self):
        assert ARCHES == REQUESTED

    @harness.cases("arch", ARCHES)
    def test_it_validates(self, arch):
        """Declaration errors are caught here now that nothing registers.

        The old registry validated on `register`. An alib is a class attribute
        on a backend today, so there is no call to hook -- which makes this
        test the only thing standing between a malformed table and a backend
        reaching for a capability that does not exist.
        """
        assert get(arch).validate() == []

    @harness.cases("arch", ARCHES)
    def test_the_module_name_is_arch_dot_alib(self, arch):
        assert get(arch).module_name == f"{arch}.alib"

    @harness.cases("arch", ARCHES)
    def test_every_intrinsic_is_documented(self, arch):
        for i in get(arch).all_intrinsics():
            assert i.doc.endswith("."), f"{arch}.{i.name}: doc is not a sentence"
            assert i.result, f"{arch}.{i.name}: no result type"


class TestTheShapeIsHonest:
    """An alib must not offer what its architecture cannot do."""

    @harness.cases("arch", ["jvm", "pybc"])
    def test_a_managed_target_offers_no_memory_mapped_io(self, arch):
        """No address space means no MMIO, and no pretending otherwise.

        A `mmio32_read` on the JVM could only ever be a lie or a crash. The
        group is ABSENT so that naming it is a compile error against the
        architecture rather than a link error against a symbol.
        """
        caps = get(arch).capabilities
        for cannot in ("mmio", "ports", "sysregs", "interrupts", "emit_raw"):
            assert cannot not in caps, f"{arch} claims {cannot}"

    @harness.cases("arch", ["x86_64", "x86_32"])
    def test_only_x86_has_ports(self, arch):
        assert "ports" in get(arch).capabilities

    @harness.cases("arch", [a for a in ARCHES if a not in ("x86_64", "x86_32")])
    def test_nothing_else_has_ports(self, arch):
        """One address space means devices are memory. Ports are x86's alone."""
        assert "ports" not in get(arch).capabilities

    @harness.cases("arch", ["x86_64", "x86_32", "arm64", "arm32", "c", "llvm"])
    def test_everything_with_an_address_space_has_mmio(self, arch):
        a = get(arch)
        assert "mmio" in a.capabilities
        names = {i.name for i in a.groups["mmio"]}
        for bits in (8, 16, 32, 64):
            assert f"mmio{bits}_read" in names and f"mmio{bits}_write" in names

    def test_privileged_intrinsics_are_marked_freestanding(self):
        """Reading a control register under an OS is a fault, not a value."""
        for arch in ("x86_64", "arm64"):
            a = get(arch)
            for group in ("sysregs", "interrupts"):
                for i in a.groups[group]:
                    assert i.freestanding_only, f"{arch}.{i.name} is not marked"


class TestTheRegistry:
    def test_a_bare_architecture_name_is_not_an_alib(self):
        """`import x86_64` must not hand anyone privileged instructions."""
        assert alib.for_module("x86_64") is None
        assert alib.for_module("x86_64.alib").arch == "x86_64"

    def test_an_unknown_architecture_answers_none(self):
        assert alib.by_arch("pdp11") is None

    def test_a_bad_declaration_is_reported_by_validate(self):
        bad = alib.Alib(arch="t", doc="d", groups={"not-a-capability": ()})
        assert any("not a capability" in p for p in bad.validate())

    def test_every_alib_belongs_to_a_backend(self):
        """THE REASON THIS LIVES ON THE BACKEND NOW.

        An architecture library describes instructions something can emit. As
        a registry of its own it could declare `rdtsc` with no code generator
        anywhere able to produce it, and nothing minded.
        """
        from asmpython import backend as backend_registry
        backend_registry.load_builtin()
        owned = {be.alib.arch for be in backend_registry.available().values()
                 if be.alib is not None}
        assert owned == set(ARCHES)

    def test_every_shipped_target_arch_has_an_alib(self):
        """A platform asmpython SHIPS should have a library for its machine.

        `any` is excluded: the portable-C target deliberately describes no
        machine, and `c.alib` is reached by architecture rather than by that
        target's `arch` field.

        THE SHIPPED LIST, NOT THE LIVE REGISTRY. Reading `available()` made
        this test depend on what else had run in the same worker: the example
        in `archived/docs/TARGETS.md` registers a `riscv64-linux`, the
        documentation suite EXECUTES that example, and this then failed over
        an architecture asmpython does not claim to support. A registry is
        meant to accept outside registrations -- so a test that treats one as
        a closed list is asserting the opposite of the design.
        """
        from asmpython import targets as shipped

        missing = set()
        for name in shipped.__all__:
            t = getattr(shipped, name)
            if t.arch in ("any",):
                continue
            # THROUGH `by_arch`, so an alias counts: `Target.arch` says
            # "aarch64" where the backend is called "arm64".
            if alib.by_arch(t.arch) is None:
                missing.add(t.arch)
        assert not missing, f"architectures with no alib: {missing}"

    @harness.cases("spelling,canonical", [
        ("aarch64", "arm64"), ("amd64", "x86_64"), ("i386", "x86_32"),
        ("armv7", "arm32"), ("wasm32", "wasm"), ("opencir", "apir"),
    ])
    def test_an_alias_reaches_the_same_library(self, spelling, canonical):
        assert alib.by_arch(spelling) is get(canonical)
        assert alib.for_module(f"{spelling}.alib") is get(canonical)


class TestTheStubStateIsReported:
    """How much of each alib is REAL, written down so it cannot drift.

    This said "nothing is lowered yet" and it fired, which is what it was
    for: x86-64 grew an encoder for its intrinsics and the claim stopped
    being true the same day. It is narrowed rather than deleted -- a test
    that only ever asserted zero would have to be thrown away the first time
    anyone made progress, and then nothing would be watching at all.
    """

    #: Backend -> how many of its intrinsics a backend actually lowers.
    #: EVERY CHANGE TO THIS NUMBER IS DELIBERATE. Bumping it is how the
    #: progress gets recorded; a number that went DOWN would be a regression
    #: nothing else in the suite would notice, because an unlowered intrinsic
    #: fails at the call site rather than here.
    LOWERED = {"x86_64": 43}

    def test_the_lowered_count_is_what_is_recorded(self):
        got = {arch: sum(1 for i in a.all_intrinsics() if i.implemented)
               for arch, a in alib.all_alibs().items()}
        got = {arch: n for arch, n in got.items() if n}
        assert got == self.LOWERED, (
            f"lowering moved: {got}. Update LOWERED -- and if a number went "
            f"down, find out why before you do.")

    def test_everything_lowered_belongs_to_a_finished_backend(self):
        """An unfinished backend must not claim to emit anything.

        `ready = False` means `emit` refuses, so an intrinsic marked lowered
        there could never actually be produced -- the declaration would be
        the only evidence, which is the state this whole file exists to keep
        visible.
        """
        from asmpython import backend as backend_registry
        backend_registry.load_builtin()
        for be in backend_registry.available().values():
            if be.alib is None or be.ready:
                continue
            done = [i.name for i in be.alib.all_intrinsics() if i.implemented]
            assert not done, f"{be.name} is unfinished but claims {done}"

    def test_apir_declares_nothing(self):
        """It is a placeholder for a format that has not been identified.

        Empty ON PURPOSE. An invented instruction surface would be
        indistinguishable from the nine written against real documents.
        """
        assert get("apir").all_intrinsics() == ()
