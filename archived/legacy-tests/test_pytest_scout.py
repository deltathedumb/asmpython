"""Regression tests for the external pytest differential scout."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from asmpython._compiler.__main__ import main as compiler_main
from asmpython.tools.pytest_scout import (
    CommandResult,
    build_import_overlay,
    command_transcript,
    compare_commands,
    discover_pytest_evidence,
    main as scout_main,
    parse_repository_spec,
    render_pytest_launcher,
)


class RepositoryParsingTests(unittest.TestCase):
    def test_owner_repo_and_ref(self) -> None:
        candidate = parse_repository_spec("pallets/click@8.1.8")
        self.assertEqual(candidate.name, "pallets/click")
        self.assertEqual(candidate.ref, "8.1.8")
        self.assertEqual(candidate.clone_url, "https://github.com/pallets/click.git")

    def test_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = parse_repository_spec(temporary)
            self.assertEqual(candidate.local_path, str(Path(temporary).resolve()))
            self.assertIsNone(candidate.clone_url)

    def test_invalid_spec_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_repository_spec("not-a-repository")


class PytestDiscoveryTests(unittest.TestCase):
    def test_finds_config_conftest_and_importing_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8"
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "conftest.py").write_text("VALUE = 1\n", encoding="utf-8")
            (tests / "test_sample.py").write_text(
                "import pytest\n\ndef test_value():\n    assert 1 == 1\n",
                encoding="utf-8",
            )

            evidence = discover_pytest_evidence(root)

            self.assertTrue(evidence.is_pytest_repository)
            self.assertEqual(evidence.config_files, ("pyproject.toml",))
            self.assertEqual(evidence.conftest_files, ("tests/conftest.py",))
            self.assertEqual(evidence.importing_tests, ("tests/test_sample.py",))

    def test_plain_unittest_style_file_is_not_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "test_sample.py").write_text(
                "def test_value():\n    assert 1 == 1\n", encoding="utf-8"
            )
            evidence = discover_pytest_evidence(root)
            self.assertFalse(evidence.is_pytest_repository)


class OverlayAndLauncherTests(unittest.TestCase):
    def test_overlay_uses_first_import_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            overlay = root / "overlay"
            first.mkdir()
            second.mkdir()
            (first / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
            (second / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")

            collisions = build_import_overlay(overlay, [first, second], force_copy=True)

            self.assertEqual((overlay / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertEqual(len(collisions), 1)

    def test_launcher_passes_the_same_arguments_to_pytest(self) -> None:
        source = render_pytest_launcher(["-q", "tests/test one.py"])
        self.assertIn("pytest.main(['-q', 'tests/test one.py'])", source)
        self.assertIn("raise SystemExit(_result)", source)


class TranscriptTests(unittest.TestCase):
    def result(self, stdout: str, *, code: int = 0) -> CommandResult:
        return CommandResult(("python",), "/repo", code, stdout, "", 0.1)

    def test_transcript_normalizes_paths_addresses_and_timings(self) -> None:
        transcript = command_transcript(
            self.result(
                "/tmp/work/repo/test.py object at 0x1234567890abcdef\n"
                "1 passed in 0.42s\n"
            ),
            replacements=(("/tmp/work/repo", "<repo>"),),
        )
        self.assertIn("<repo>/test.py object at <address>", transcript)
        self.assertIn("1 passed in <time>s", transcript)

    def test_comparison_produces_unified_diff(self) -> None:
        compared = compare_commands(
            self.result("expected\n"),
            self.result("actual\n"),
            actual_name="native",
            replacements=(),
        )
        self.assertEqual(compared.status, "diff")
        self.assertIn("--- cpython", compared.diff)
        self.assertIn("+++ native", compared.diff)
        self.assertIn("-expected", compared.diff)
        self.assertIn("+actual", compared.diff)


class ScoutCliTests(unittest.TestCase):
    def test_discover_only_writes_json_without_execution_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            (repository / "test_sample.py").write_text(
                "def test_sample():\n    assert True\n", encoding="utf-8"
            )
            workspace = root / "work"
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(StringIO()):
                code = scout_main(
                    [
                        "--repo", str(repository),
                        "--discover-only",
                        "--workspace", str(workspace),
                        "--limit", "1",
                    ]
                )

            self.assertEqual(code, 0)
            report = json.loads((workspace / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["discovery"][0]["pytest"])
            self.assertEqual(report["repositories"], [])


class NativeOnlyCliTests(unittest.TestCase):
    def test_no_pyinbin_fallback_reports_native_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unsupported.py"
            source.write_text('print(eval("1 + 1"))\n', encoding="utf-8")
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = compiler_main(
                    [
                        "build",
                        str(source),
                        "--emit-asm",
                        "--no-pyinbin-fallback",
                        "-o",
                        str(root / "unsupported"),
                    ]
                )

            self.assertEqual(code, 1)
            self.assertIn("eval() is not supported", stderr.getvalue())
            self.assertNotIn("pyinbin fallback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
