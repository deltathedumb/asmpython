from __future__ import annotations

import json
from pathlib import Path

import pytest

from asmpython._compiler.test_runner import command_main


def test_cpython_only_test_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("print('ok')\n", encoding="utf-8")
    assert command_main([str(test_file), "--engine", "cpython"]) == 0
    output = capsys.readouterr().out
    assert "PASS" in output
    assert "1 test(s), 0 failure(s)" in output


def test_json_test_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    test_file = tmp_path / "test_json.py"
    test_file.write_text("print('json')\n", encoding="utf-8")
    assert command_main([str(test_file), "--engine", "cpython", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["failures"] == 0
    assert report["tests"][0]["results"]["cpython"]["stdout"] == "json\n"
