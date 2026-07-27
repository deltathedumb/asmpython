from __future__ import annotations

import json
from pathlib import Path

import pytest

from asmpython._compiler import cli
from asmpython._compiler.cache_manager import clear_cache, scan_cache
from asmpython._compiler.irfreeze import FrozenIR, dump_ir, load_ir
from asmpython._compiler.management_commands import apply_build_profiles
from asmpython._compiler.profiles import resolve_profile, save_profile
from asmpython._compiler.ir import IRFunc, IRModule, IRValue, I64


def test_backends_list_and_info(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["backends", "list"]) == 0
    output = capsys.readouterr().out
    assert "x86-64" in output
    assert "jvm" in output
    assert "scaffold" in output

    # jvm is implemented now, so it reports as experimental rather than as a
    # scaffold. Its options are real arguments, not planned ones.
    assert cli.main(["backends", "jvm", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["name"] == "jvm"
    assert document["status"] == "experimental"
    assert any(arg["name"] == "--java-version" for arg in document["requested_args"])

    # A backend still waiting to be written reports the other way round.
    assert cli.main(["backends", "riscv", "--json"]) == 0
    scaffold = json.loads(capsys.readouterr().out)
    assert scaffold["status"] == "scaffold"
    assert scaffold["planned_parameters"]


def test_removed_commands_have_replacements(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["invalidate"]) == 2
    assert "cache clear" in capsys.readouterr().err
    assert cli.main(["irbuild", "app.apir"]) == 2
    assert "asmpython ir" in capsys.readouterr().err


def test_scoped_profiles_overlay_and_inject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    system = tmp_path / "system.json"
    user = tmp_path / "user.json"
    monkeypatch.setenv("ASMPYTHON_SYSTEM_PROFILES", str(system))
    monkeypatch.setenv("ASMPYTHON_USER_PROFILES", str(user))
    monkeypatch.chdir(tmp_path)

    save_profile("release", {"target": "linux", "backend": "x86-64"}, scope="system")
    save_profile("release", {"output_type": "library"}, scope="user")
    save_profile("release", {"backend": "jvm"}, scope="directory")

    resolved = resolve_profile("release")
    assert resolved["target"] == "linux"
    assert resolved["output_type"] == "library"
    assert resolved["backend"] == "jvm"

    argv = apply_build_profiles(["build", "app.py", "--profile", "release", "--backend", "x86-64"])
    assert argv[0] == "build"
    assert argv.index("--backend") < len(argv) - 2
    assert argv[-2:] == ["--backend", "x86-64"]


def test_cache_scanning_and_clear(tmp_path: Path) -> None:
    entry = tmp_path / "one"
    entry.mkdir()
    (entry / "artifact.bin").write_bytes(b"hello")
    (entry / "manifest.json").write_text(
        json.dumps({"kind": "test", "source_path": "/example.py"}),
        encoding="utf-8",
    )
    entries = scan_cache(tmp_path)
    assert len(entries) == 1
    assert entries[0].valid
    assert clear_cache(tmp_path, key="one") == 1
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_ir_binary_and_json_roundtrip(tmp_path: Path) -> None:
    module = IRModule(funcs=[IRFunc("main", [IRValue("x", I64)], I64)], data=[])
    frozen = FrozenIR(module=module, metadata={
        "format": "asmpython-ir",
        "format_version": 1,
        "compiler_version": "test",
        "stage": "optimized",
        "components": {},
    })
    binary = tmp_path / "module.apir"
    json_path = tmp_path / "module.apir.json"
    dump_ir(frozen, binary, output="bin")
    dump_ir(frozen, json_path, output="json")
    assert load_ir(binary).module.funcs[0].name == "main"
    assert load_ir(json_path).module.funcs[0].params[0].name == "x"
