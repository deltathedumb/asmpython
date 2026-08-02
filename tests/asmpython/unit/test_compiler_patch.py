"""CompilerPatch: the escape hatch, and the two things it will not do.

A registry covers the four extension points asmpython anticipated. A patch
covers everything else, which means the interesting tests are not "does it
work" but "what does it refuse, and can it be undone" -- a patch system
without those is a fork with extra steps.
"""
from __future__ import annotations

import pytest

from asmpython.plugins import CompilerPatch, PatchError
from asmpython.plugins import patch as patch_module


@pytest.fixture(autouse=True)
def clean():
    """No patch survives a test. Every one of these edits global state."""
    yield
    patch_module.revert_all()


def target_function(x: int) -> int:
    return x * 2


HERE = f"{__name__}.target_function"


class TestTheFourKinds:
    def test_replace(self):
        CompilerPatch(HERE, replace=lambda x: 99).apply()
        assert target_function(1) == 99

    def test_wrap_receives_the_original_first(self):
        CompilerPatch(HERE, wrap=lambda orig, x: orig(x) + 1).apply()
        assert target_function(10) == 21

    def test_before_observes(self):
        seen = []
        CompilerPatch(HERE, before=seen.append).apply()
        assert target_function(3) == 6 and seen == [3]

    def test_after_can_change_the_result(self):
        CompilerPatch(HERE, after=lambda result, x: result + x).apply()
        assert target_function(4) == 12

    def test_two_wraps_compose(self):
        """Which is why `wrap` is the one to reach for.

        Both plugins run and neither knows about the other. Two `replace`s
        cannot do this, and that is the whole reason they are a conflict.
        """
        CompilerPatch(HERE, wrap=lambda o, x: o(x) + 1).apply()
        CompilerPatch(HERE, wrap=lambda o, x: o(x) * 10).apply()
        assert target_function(1) == 30            # (1*2 + 1) * 10

    def test_giving_no_action_is_refused(self):
        with pytest.raises(PatchError, match="does nothing"):
            CompilerPatch(HERE)

    def test_giving_two_actions_is_refused(self):
        """Otherwise the order they run in is a detail of this class."""
        with pytest.raises(PatchError, match="pick one"):
            CompilerPatch(HERE, replace=print, wrap=print)


class TestItCanBeUndone:
    def test_revert_restores_the_original(self):
        p = CompilerPatch(HERE, replace=lambda x: 0)
        p.apply()
        assert target_function(5) == 0
        p.revert()
        assert target_function(5) == 10
        assert not p.applied

    def test_revert_all_unwinds_in_reverse(self):
        CompilerPatch(HERE, wrap=lambda o, x: o(x) + 1).apply()
        CompilerPatch(HERE, wrap=lambda o, x: o(x) + 100).apply()
        patch_module.revert_all()
        assert target_function(5) == 10

    def test_applying_twice_is_a_no_op(self):
        p = CompilerPatch(HERE, wrap=lambda o, x: o(x) + 1)
        p.apply()
        p.apply()
        assert target_function(1) == 3        # wrapped once, not twice


class TestWhatItRefuses:
    @pytest.mark.parametrize("target", [
        "asmpython.plugins.patch.apply_all",
        "asmpython.plugins.patch.CompilerPatch.check",
        "asmpython.plugins.store.write",
        "asmpython.plugins.store.remove",
    ])
    def test_sealed_targets_are_never_patchable(self, target):
        """Not a risk judgement -- a structural one.

        A patch that can disable the check makes every other protection
        advisory, and one that can rewrite the plugin store can reinstall
        itself after `plugin remove`.
        """
        with pytest.raises(PatchError, match="sealed"):
            CompilerPatch(target, replace=print).apply()

    @pytest.mark.parametrize("target", [
        "asmpython.ir.verifier.verify",
        "asmpython.backend.base.register",
        "asmpython.target.registry.register",
    ])
    def test_guarded_targets_need_force(self, target):
        with pytest.raises(PatchError, match="guarded"):
            CompilerPatch(target, replace=print).apply()

    def test_force_allows_a_guarded_target(self):
        p = CompilerPatch("asmpython.ir.verifier.verify",
                          replace=lambda m: None, force=True)
        p.apply()
        assert p.applied

    def test_a_forced_patch_says_so_when_described(self):
        """`plugin show` prints this. Silent force is the same as no guard."""
        p = CompilerPatch("asmpython.ir.verifier.verify",
                          replace=lambda m: None, force=True, reason="why not")
        assert "forced" in p.describe() and "why not" in p.describe()

    def test_everything_else_is_open(self):
        """Deliberately. Guessing which internals someone needs, in advance,
        is wrong in the direction that makes people fork."""
        p = CompilerPatch("asmpython.passes.transforms.DeadCodeElimination.run",
                          wrap=lambda o, *a, **k: o(*a, **k))
        p.apply()
        assert p.applied


class TestBadTargets:
    def test_a_module_is_not_a_target(self):
        with pytest.raises(PatchError, match="names a module"):
            CompilerPatch("asmpython.ir.printer", replace=print).apply()

    def test_a_missing_attribute_is_reported(self):
        with pytest.raises(PatchError, match="does not exist"):
            CompilerPatch("asmpython.ir.printer.nope", replace=print).apply()

    def test_an_unimportable_root_is_reported(self):
        with pytest.raises(PatchError, match="could be imported"):
            CompilerPatch("no_such_package.thing", replace=print).apply()

    def test_a_non_callable_is_refused(self):
        with pytest.raises(PatchError, match="not callable"):
            CompilerPatch("asmpython.plugins.MANIFEST_ATTR",
                          replace=print).apply()

    def test_a_method_can_be_patched(self):
        """`pkg.mod.Class.method` needs no special spelling."""
        p = CompilerPatch("asmpython.ir.module.Module.statistics",
                          replace=lambda self: {"patched": 1})
        p.apply()
        from asmpython.ir import Module
        assert Module().statistics() == {"patched": 1}


class TestConflicts:
    def test_a_replace_after_anything_is_a_conflict(self):
        CompilerPatch(HERE, wrap=lambda o, x: o(x)).apply()
        with pytest.raises(PatchError, match="already patched"):
            patch_module.apply_all([CompilerPatch(HERE, replace=print)])

    def test_anything_after_a_replace_is_a_conflict(self):
        CompilerPatch(HERE, replace=lambda x: 1).apply()
        with pytest.raises(PatchError, match="already patched"):
            patch_module.apply_all([CompilerPatch(HERE, wrap=lambda o, x: o(x))])

    def test_a_failing_batch_leaves_nothing_applied(self):
        """Half a plugin is harder to diagnose than none of it."""
        good = CompilerPatch(HERE, wrap=lambda o, x: o(x) + 1)
        bad = CompilerPatch("asmpython.plugins.store.write", replace=print)
        with pytest.raises(PatchError):
            patch_module.apply_all([good, bad])
        assert not good.applied
        assert target_function(1) == 2
