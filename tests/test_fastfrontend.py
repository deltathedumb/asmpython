from pathlib import Path

from asmpython._compiler.fastfrontend import dependency_snapshot


def test_dependency_snapshot_tracks_relative_imports(tmp_path: Path) -> None:
    entry = tmp_path / "app.py"
    dependency = tmp_path / "helper.py"
    entry.write_text("from . import helper\n", encoding="utf-8")
    dependency.write_text("VALUE = 1\n", encoding="utf-8")

    snapshot = dependency_snapshot(entry)
    assert str(entry.resolve()) in snapshot
    assert str(dependency.resolve()) in snapshot
