from __future__ import annotations

import json
from pathlib import Path

from asmpython._compiler.build_options import (
    extract_shared_build_options,
    inject_build_options,
    shared_build_options,
)
from asmpython._compiler.build_report import event, report_session, stage
from asmpython._compiler.extension_packages import (
    install_extension,
    list_installed,
    load_installed_extensions,
    package_extension,
    read_manifest,
    uninstall_extension,
)


def test_apext_package_install_discover_and_load(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text(
        "from asmpython import Extension\n"
        "extension = Extension(id='test_extension', version='1.2.3')\n",
        encoding="utf-8",
    )

    package = package_extension(
        "main:extension", root=tmp_path, output=tmp_path / "test_extension.apext"
    )
    manifest = read_manifest(package)
    assert manifest["id"] == "test_extension"
    assert manifest["entry"] == "main.py"
    assert manifest["object"] == "extension"
    assert manifest["files"]["main.py"]

    installed = install_extension(package, scope="local", directory=tmp_path)
    assert installed.id == "test_extension"
    assert installed.version == "1.2.3"
    assert installed.path.is_file()

    records = list_installed(tmp_path)
    assert [(record.id, record.scope) for record in records] == [
        ("test_extension", "local")
    ]
    loaded = load_installed_extensions(tmp_path)
    assert [(record.id, record.version) for record in loaded] == [
        ("test_extension", "1.2.3")
    ]

    removed = uninstall_extension("test_extension", directory=tmp_path)
    assert removed == [installed.path]
    assert not installed.path.exists()


def test_bleach_and_sanitizers_are_normalized_and_injected() -> None:
    remaining, options = extract_shared_build_options([
        "build",
        "app.py",
        "--bleach",
        "--sanitize",
        "bounds,integer",
        "--report",
        "out/report.json",
    ])
    assert remaining == ["build", "app.py"]
    assert options.bleach is True
    assert options.sanitizers == (
        "address", "bounds", "integer", "leak", "undefined"
    )
    assert options.report_path == Path("out/report.json")

    with shared_build_options(options):
        injected = inject_build_options({"target_os": "linux"})
    assert injected["speedy_lossy"] is False
    assert injected["bleach"] is True
    assert injected["sanitizers"] == options.sanitizers


def test_thread_sanitizer_conflicts_are_rejected() -> None:
    try:
        extract_shared_build_options([
            "build", "app.py", "--sanitize", "thread,address"
        ])
    except ValueError as exc:
        assert "cannot be combined" in str(exc)
    else:
        raise AssertionError("expected incompatible sanitizer selection to fail")


def test_build_report_records_events_and_failure_state(tmp_path: Path) -> None:
    path = tmp_path / "build-report.json"
    with report_session(path, ["build", "app.py"], {"bleach": False}) as report:
        assert report is not None
        with stage("backend.compile", backend="test"):
            event("backend.outputs", outputs={"output.o": 4})
        report.write(exit_code=0)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == "asmpython.build-report"
    assert payload["success"] is True
    assert payload["exit_code"] == 0
    assert any(item["kind"] == "backend.compile.finish" for item in payload["events"])
    assert any(item["kind"] == "backend.outputs" for item in payload["events"])
