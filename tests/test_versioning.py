from __future__ import annotations

import re
import tomllib
from pathlib import Path

import asmpython
from asmpython._version import (
    ASMPYTHON_VERSION,
    FULL_VERSION,
    FULL_VERSION_INFO,
    PACKAGING_VERSION,
    PYTHON_LANGUAGE_VERSION,
    PYTHON_VERSION_INFO,
    RELEASE_VERSION,
    VERSION_INFO,
    asmpython_version,
    full_version,
    python_version,
)


ROOT = Path(__file__).resolve().parents[1]


def test_public_version_shape() -> None:
    assert re.fullmatch(r"\d+\.\d+-\d+\.\d+\.\d+", FULL_VERSION)
    assert FULL_VERSION == f"{PYTHON_LANGUAGE_VERSION}-{ASMPYTHON_VERSION}"
    assert RELEASE_VERSION == FULL_VERSION
    assert PYTHON_VERSION_INFO == (3, 14)
    assert VERSION_INFO == (2, 0, 0)
    assert FULL_VERSION_INFO == (3, 14, 2, 0, 0)


def test_runtime_version_surfaces() -> None:
    assert asmpython.__version__ == "2.0.0"
    assert asmpython.ASMPYTHON_VERSION == "2.0.0"
    assert asmpython.PYTHON_LANGUAGE_VERSION == "3.14"
    assert asmpython.FULL_VERSION == "3.14-2.0.0"
    assert asmpython.RELEASE_VERSION == "3.14-2.0.0"
    assert asmpython.asmpython_version == "2.0.0"
    assert asmpython.python_version == "3.14"
    assert asmpython.full_version == "3.14-2.0.0"
    assert asmpython_version == ASMPYTHON_VERSION
    assert python_version == PYTHON_LANGUAGE_VERSION
    assert full_version == FULL_VERSION


def test_all_version_sources_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert PACKAGING_VERSION == ASMPYTHON_VERSION
    assert project["project"]["version"] == ASMPYTHON_VERSION
    assert version_file == FULL_VERSION
