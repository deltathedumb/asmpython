"""`--bits` chooses within a backend family, and never contradicts silently.

THE RULE. A build names an architecture, a word size and a platform, and any
two of them can imply the third. When two of them disagree the build must SAY
WHICH TWO -- picking one and ignoring the other is how a user ends up holding
a 64-bit object they asked to be 32-bit, with nothing in the output to say so.

WHY THE FAMILY IS RESOLVED BEFORE THE REGISTRY. `x86` and `arm` cannot emit
anything; they are the two architectures, each with a 32- and a 64-bit code
generator. Registering them would put names in the backend list that answer
`emit` with a dispatch, so `uasm plugin backends` would show six entries for
four compilers.
"""
from __future__ import annotations

from tests import harness

from uasm import backend as backend_registry
from uasm import target as target_registry
from uasm.backend.families import (
    DEFAULT_BITS, FAMILIES, MEMBER_OF, SelectionError,
    resolve_backend, resolve_target,
)

backend_registry.load_builtin()


class TestAFamilyNamePicksItsMember:

    @harness.cases("family,bits,expected", [
        ("x86", 64, "x86-64"), ("x86", 32, "x86-32"),
        ("arm", 64, "arm64"), ("arm", 32, "arm32"),
    ])
    def test_the_width_decides(self, family, bits, expected):
        assert resolve_backend(family, bits) == expected

    def test_without_bits_a_family_takes_the_default(self):
        assert resolve_backend("x86", None) == FAMILIES["x86"][DEFAULT_BITS]
        assert resolve_backend("arm", None) == FAMILIES["arm"][DEFAULT_BITS]

    def test_a_target_supplies_the_width_when_bits_does_not(self):
        """`--target aarch64-linux` has already said 64.

        Making the user say it twice would be a second place for the two to
        disagree, which is the thing this module exists to prevent.
        """
        target = target_registry.get("aarch64-linux")
        assert resolve_backend("arm", None, target) == "arm64"

    @harness.cases("name", sorted(MEMBER_OF))
    def test_every_member_is_a_registered_backend(self, name):
        """A family that named something unregistered would answer `--bits`
        with "unknown backend", which reads as the user's mistake."""
        assert name in backend_registry.available()

    @harness.cases("name", ["c", "jvm", "pybc", "llvm", "wasm"])
    def test_a_name_that_is_not_a_family_passes_through(self, name):
        assert resolve_backend(name, None) == name


class TestATriplesContradictionIsReported:

    def test_bits_against_a_concrete_backend(self):
        with harness.raises(SelectionError, match="contradicts --backend") as e:
            resolve_backend("x86-64", 32)
        # THE MESSAGE MUST CARRY THE WAY OUT, or the user is told what they
        # cannot do and left to guess what they can.
        assert "x86-32" in str(e.value) or "--bits 32" in str(e.value)

    def test_bits_against_a_named_target(self):
        with harness.raises(SelectionError, match="contradicts --target"):
            resolve_target("x86-64", 32, "x86_64-linux", target_registry)

    def test_a_width_the_family_does_not_have(self):
        with harness.raises(SelectionError, match="no 16-bit backend"):
            resolve_backend("x86", 16)

    def test_agreement_is_not_a_contradiction(self):
        assert resolve_backend("x86-64", 64) == "x86-64"
        assert resolve_target(
            "x86-64", 64, "x86_64-linux", target_registry) == "x86_64-linux"


class TestTheTargetIsChosenOnlyWhenNothingElseHas:

    def test_a_named_target_is_kept(self):
        assert resolve_target("arm64", None, "aarch64-macos",
                              target_registry) == "aarch64-macos"

    def test_no_target_and_no_bits_leaves_the_backend_default(self):
        assert resolve_target("x86-64", None, None, target_registry) is None

    def test_a_matching_width_leaves_the_backend_default(self):
        """`--bits 64` changes nothing for a backend that was going to be
        64-bit anyway; only a width its default does not have needs a search."""
        assert resolve_target("x86-64", 64, None, target_registry) is None
        assert resolve_target("arm64", 64, None, target_registry) is None


class TestThirtyTwoBitSaysWhatItIsWaitingOn:
    """The refusal is the deliverable until the backend exists.

    A `--bits 32` that answered "no target registered" would send the user to
    look for a platform that is deliberately absent. What is missing is the
    code generator, and before that a `ptr` whose width the target decides --
    so the message names both, in the order the work has to happen.
    """

    @harness.cases("family", ["x86", "arm"])
    def test_it_names_the_backend_and_the_pointer_width(self, family):
        with harness.raises(SelectionError) as caught:
            resolve_target(FAMILIES[family][32], 32, None, target_registry)
        message = str(caught.value)
        assert "not written yet" in message, message
        assert "ptr" in message, message

    @harness.cases("family", ["x86", "arm"])
    def test_selecting_it_is_not_itself_an_error(self, family):
        """Choosing a 32-bit backend must resolve; it is EMITTING that
        refuses. Failing at selection would make the two indistinguishable."""
        assert resolve_backend(family, 32) == FAMILIES[family][32]


class TestTheDefaultWidthIsTheOneTheIrAgreesOn:
    def test_it_is_sixty_four(self):
        """Not arbitrary: `ir/types.py` fixes `ptr` at eight bytes, so 64 is
        the only width the whole compiler currently agrees on."""
        from uasm.ir import types as T
        assert DEFAULT_BITS == T.PTR.bits
