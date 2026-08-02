from __future__ import annotations

from asmpython._compiler.cli import cli
from asmpython._compiler import driver
from asmpython._compiler.build.build_options import SharedBuildOptions, shared_build_options
from asmpython._compiler import fastcomp_bridge


def test_fastcomp_bridge_is_installed_by_cli_facade() -> None:
    assert cli.main is not None
    assert driver._run_backend is fastcomp_bridge._run_backend_fastcomp


def test_fragment_stitching_policy() -> None:
    with shared_build_options(SharedBuildOptions(fastcomp=True)):
        allowed, reason = fastcomp_bridge._can_stitch(
            target="linux",
            backend="legacy",
            linker="gcc",
            emit_asm_only=False,
            keep_intermediates=False,
            bundle_mode="onefile",
        )
    assert allowed is True
    assert reason is None

    with shared_build_options(
        SharedBuildOptions(fastcomp=True, debug=True, debug_format="dwarf")
    ):
        allowed, reason = fastcomp_bridge._can_stitch(
            target="linux",
            backend="legacy",
            linker="gcc",
            emit_asm_only=False,
            keep_intermediates=False,
            bundle_mode="onefile",
        )
    assert allowed is False
    assert "debug" in str(reason)


def test_nonlegacy_backend_keeps_its_own_fastcomp_contract() -> None:
    with shared_build_options(SharedBuildOptions(fastcomp=True)):
        allowed, reason = fastcomp_bridge._can_stitch(
            target="linux",
            backend="jvm",
            linker=None,
            emit_asm_only=False,
            keep_intermediates=False,
            bundle_mode="onefile",
        )
    assert allowed is False
    assert "backend owns" in str(reason)
