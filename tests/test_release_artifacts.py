"""Tests for the PyPI tag and artifact consistency gate."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_release_artifacts import VerificationError, verify_release


def metadata(name: str, version: str) -> bytes:
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        "\n"
    ).encode("utf-8")


def write_wheel(path: Path, name: str, version: str) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata(name, version))


def write_sdist(path: Path, name: str, version: str) -> None:
    data = metadata(name, version)
    info = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
    info.size = len(data)
    with tarfile.open(path, mode="w:gz") as archive:
        archive.addfile(info, io.BytesIO(data))


class ReleaseArtifactTests(unittest.TestCase):
    def artifacts(self, root: Path, version: str) -> tuple[Path, Path]:
        wheel = root / f"asmpython-{version}-py3-none-any.whl"
        sdist = root / f"asmpython-{version}.tar.gz"
        write_wheel(wheel, "asmpython", version)
        write_sdist(sdist, "asmpython", version)
        return wheel, sdist

    def test_matching_wheel_sdist_and_tag_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = self.artifacts(Path(temporary), "1.2.0")
            verified = verify_release("v1.2.0", "asmpython", artifacts)
            self.assertEqual({item.kind for item in verified}, {"wheel", "sdist"})

    def test_pep440_equivalent_preview_tag_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = self.artifacts(Path(temporary), "2.0.0rc0")
            verify_release("v2.0.0-preview", "asmpython", artifacts)

    def test_stale_artifact_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = self.artifacts(Path(temporary), "1.1.0")
            with self.assertRaisesRegex(VerificationError, "does not match release tag"):
                verify_release("v1.2.0", "asmpython", artifacts)

    def test_wheel_and_sdist_are_both_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel, _sdist = self.artifacts(Path(temporary), "1.2.0")
            with self.assertRaisesRegex(VerificationError, "wheel and one sdist"):
                verify_release("v1.2.0", "asmpython", [wheel])

    def test_workflow_avoids_local_build_module_shadowing(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pyproject-build --outdir dist .", workflow)
        self.assertNotIn("run: python -m build", workflow)

    def test_workflow_only_accepts_the_tagged_pypi_tip(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ref: ${{ env.RELEASE_TAG }}", workflow)
        self.assertIn('show-ref --verify --quiet "refs/tags/$RELEASE_TAG"', workflow)
        self.assertIn("refs/remotes/origin/pypi^{commit}", workflow)
        self.assertIn('verify_release_artifacts.py --tag "$RELEASE_TAG"', workflow)

    def test_pyproject_has_only_the_runtime_version_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('version = { attr = "asmpython.__version__" }', pyproject)
        self.assertNotIn('version = "1.1.0"', pyproject)
        self.assertIn('license = "MIT"', pyproject)
        self.assertNotIn('license = { text = "MIT" }', pyproject)


if __name__ == "__main__":
    unittest.main()
