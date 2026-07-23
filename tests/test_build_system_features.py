from __future__ import annotations

import json
import struct
from pathlib import Path

import asmpython
from asmpython import embedded
from asmpython._compiler.abi_tool import diff_abi
from asmpython._compiler.artifact_verify import verify_artifact
from asmpython._compiler.build_config import apply_build_config
from asmpython._compiler.build_options import SharedBuildOptions, shared_build_options
from asmpython._compiler.build_plan import create_build_plan
from asmpython._compiler.debug_support import write_debug_sidecar
from asmpython._compiler.embedded_data import append_resources, decode_resources, encode_resources
from asmpython._compiler.fast_state import prepare_state, store_backend_state, store_ir
from asmpython._compiler.negotiation_ext import negotiate_build
from asmpython._compiler.target_triple import TargetTriple, normalize_target_argv


def _minimal_elf() -> bytes:
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4] = 2  # ELF64
    data[5] = 1  # little endian
    struct.pack_into("<H", data, 18, 62)  # x86-64
    return bytes(data)


def test_target_triple_three_token_cli() -> None:
    argv, triple = normalize_target_argv([
        "build", "app.py", "--target", "pc", "windows", "msvc"
    ])
    assert argv == ["build", "app.py", "--target", "pc-windows-msvc"]
    assert triple == TargetTriple("pc", "windows", "msvc")
    assert triple.canonical == "pc-windows-msvc"


def test_build_config_expands_before_explicit_cli(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    license_file = tmp_path / "LICENSE"
    license_file.write_text("example\n", encoding="utf-8")
    config = tmp_path / "build.config.toml"
    config.write_text(
        "[build]\n"
        "entry = 'app.py'\n"
        "backend = 'legacy'\n"
        "target = ['pc', 'windows', 'msvc']\n"
        "fastcomp = true\n"
        "debug = true\n"
        "\n[embed]\n"
        "include = ['LICENSE']\n",
        encoding="utf-8",
    )
    argv, selected = apply_build_config(
        ["build", "--config", str(config), "--backend", "x86-64"],
        is_build=True,
    )
    assert selected == config.resolve()
    assert str(source.resolve()) in argv
    assert argv.index("legacy") < argv.index("x86-64")
    assert ["--target", "pc", "windows", "msvc"] == argv[
        argv.index("--target") : argv.index("--target") + 4
    ]
    assert "--fastcomp" in argv
    assert "--debug" in argv
    assert str(license_file.resolve()) in argv


def test_embedded_container_and_mapping_module(tmp_path: Path, monkeypatch) -> None:
    rendered = encode_resources({
        "LICENSE": b"license text",
        "assets/config.json": b"{}",
    })
    assert decode_resources(rendered) == {
        "LICENSE": b"license text",
        "assets/config.json": b"{}",
    }

    binary = tmp_path / "app.bin"
    binary.write_bytes(_minimal_elf())
    append_resources(binary, {
        "LICENSE": b"license text",
        "assets/config.json": b"{}",
    })
    monkeypatch.setenv("ASMPYTHON_EMBEDDED_FILE", str(binary))
    embedded.reload()
    assert embedded["LICENSE"] == b"license text"
    assert embedded["assets"]["config.json"] == b"{}"
    assert embedded.read_text("LICENSE") == "license text"


def test_artifact_verification_reads_appended_resources(tmp_path: Path) -> None:
    artifact = tmp_path / "app"
    artifact.write_bytes(_minimal_elf())
    append_resources(artifact, {"LICENSE": b"text"})
    result = verify_artifact(artifact)
    assert result.valid is True
    assert result.format == "elf"
    assert result.architecture == "x86_64"
    assert result.details["embedded"]["names"] == ["LICENSE"]


def test_fast_state_caches_ast_graph_ir_and_backend_state(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 4\n", encoding="utf-8")
    source = tmp_path / "app.py"
    source.write_text("import helper\nprint(helper.VALUE)\n", encoding="utf-8")
    cache = tmp_path / "cache"

    first = prepare_state(source, backend="legacy", target="pc-linux-gnu", cache_dir=cache)
    assert first.hit is False
    assert len(first.parsed_modules) == 2
    assert str(helper.resolve()) in first.graph[str(source.resolve())]
    store_ir(first, {"functions": ["main"]})
    store_backend_state(first, {"register_allocator": "warm"})

    second = prepare_state(source, backend="legacy", target="pc-linux-gnu", cache_dir=cache)
    assert second.hit is True
    assert second.ir == {"functions": ["main"]}
    assert second.backend_state == {"register_allocator": "warm"}

    helper.write_text("VALUE = 5\n", encoding="utf-8")
    third = prepare_state(source, backend="legacy", target="pc-linux-gnu", cache_dir=cache)
    assert third.hit is False


def test_graph_plan_contains_modules_resources_and_pipeline(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    source = tmp_path / "app.py"
    source.write_text("import helper\n", encoding="utf-8")
    resource = tmp_path / "LICENSE"
    resource.write_bytes(b"license")

    options = SharedBuildOptions(fastcomp=True, embed_paths=(resource,))
    with shared_build_options(options):
        plan = create_build_plan([
            "build", str(source), "--backend", "legacy", "--target", "pc-linux-gnu"
        ])
    kinds = {node.kind for node in plan.nodes}
    assert {"source", "module", "resource", "backend", "linker", "artifact"} <= kinds
    assert any(edge.relation == "imports" for edge in plan.edges)
    assert plan.fastcomp is not None
    assert plan.embedded == [{"name": "LICENSE", "bytes": 7}]


def test_debug_negotiation_delegates_builtin_to_gcc(monkeypatch) -> None:
    monkeypatch.setenv("ASMPYTHON_TARGET_TRIPLE", "pc-linux-gnu")
    with shared_build_options(SharedBuildOptions(debug=True, debug_format="dwarf")):
        result = negotiate_build([
            "build", "app.py", "--backend", "x86-64", "--target", "pc-linux-gnu"
        ])
    assert result.linker is not None
    assert result.linker.name == "gcc"
    assert not result.errors


def test_debug_sidecar_and_abi_diff(tmp_path: Path) -> None:
    artifact = tmp_path / "app"
    artifact.write_bytes(_minimal_elf())
    source = tmp_path / "app.py"
    source.write_text("print('hi')\n", encoding="utf-8")
    sidecar = write_debug_sidecar(
        artifact,
        source=source,
        target="pc-linux-gnu",
        backend="legacy",
        linker="gcc",
        debug_format="dwarf",
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["native_debug_format"] == "dwarf"
    assert payload["mixed_native_pyinbin_frames"] is True

    old = {
        "format": "asmpython.abi",
        "format_version": 1,
        "abi_version": "1",
        "symbols": [{"name": "old", "kind": "function"}],
    }
    new = {
        "format": "asmpython.abi",
        "format_version": 1,
        "abi_version": "2",
        "symbols": [{"name": "new", "kind": "function"}],
    }
    result = diff_abi(old, new)
    assert result["compatible"] is False
    assert result["removed"] == ["old"]
    assert result["added"] == ["new"]


def test_embedded_is_public_top_level_module() -> None:
    assert asmpython.embedded is embedded
