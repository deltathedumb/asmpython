from pathlib import Path

from asmpython._compiler.irfreeze import dump_ir, load_ir, compile_ir


def test_binary_and_json_ir_round_trip(tmp_path: Path) -> None:
    source_path = tmp_path / "sample.py"
    source = "x = 1\nprint(x)\n"
    source_path.write_text(source, encoding="utf-8")

    frozen = compile_ir(source, source_path, stage="typed", whole_program=False)
    binary = tmp_path / "sample.apir"
    json_path = tmp_path / "sample.apir.json"
    dump_ir(frozen, binary, output="bin")
    dump_ir(frozen, json_path, output="json")

    binary_loaded = load_ir(binary)
    json_loaded = load_ir(json_path)
    assert binary_loaded.metadata["source_sha256"] == json_loaded.metadata["source_sha256"]
    assert type(binary_loaded.module).__name__ == "Module"
    assert type(json_loaded.module).__name__ == "Module"
