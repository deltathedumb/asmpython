from __future__ import annotations

import re
import tomllib
from pathlib import Path

import asmpython
from asmpython._version import (
    ASMPYTHON_BUILD,
    PACKAGING_VERSION,
    PYTHON_LANGUAGE_VERSION,
    VERSION_INFO,
)


ROOT = Path(__file__).resolve().parents[1]


def test_public_version_shape() -> None:
    assert re.fullmatch(r"\d+\.\d+-[1-9]\d*", asmpython.__version__)
    assert asmpython.__version__ == (
        f"{PYTHON_LANGUAGE_VERSION}-{ASMPYTHON_BUILD}"
    )
    assert VERSION_INFO == (3, 14, ASMPYTHON_BUILD)


def test_all_version_sources_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert PACKAGING_VERSION == asmpython.__version__
    assert project["project"]["version"] == asmpython.__version__
    assert version_file == asmpython.__version__
