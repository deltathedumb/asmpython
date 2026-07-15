"""Verify that release tags, wheel metadata, and sdist metadata agree."""

from __future__ import annotations

import argparse
import email
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


class VerificationError(RuntimeError):
    """A distribution artifact is missing or inconsistent."""


@dataclass(frozen=True)
class ArtifactMetadata:
    path: Path
    kind: str
    name: str
    version: str


def _metadata_fields(data: bytes, path: Path, kind: str) -> ArtifactMetadata:
    message = email.message_from_bytes(data)
    name = message.get("Name", "").strip()
    version = message.get("Version", "").strip()
    if not name or not version:
        raise VerificationError(f"{path}: metadata is missing Name or Version")
    return ArtifactMetadata(path=path, kind=kind, name=name, version=version)


def _wheel_metadata(path: Path) -> ArtifactMetadata:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(members) != 1:
                raise VerificationError(
                    f"{path}: expected one .dist-info/METADATA entry, found {len(members)}"
                )
            return _metadata_fields(archive.read(members[0]), path, "wheel")
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{path}: cannot read wheel: {exc}") from exc


def _sdist_tar_metadata(path: Path) -> ArtifactMetadata:
    try:
        with tarfile.open(path, mode="r:*") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile() and PurePosixPath(member.name).name == "PKG-INFO"
            ]
            if not members:
                raise VerificationError(f"{path}: no PKG-INFO found")
            # The canonical sdist record is <name-version>/PKG-INFO. Some
            # backends also include a nested *.egg-info/PKG-INFO copy.
            member = min(members, key=lambda item: len(PurePosixPath(item.name).parts))
            extracted = archive.extractfile(member)
            if extracted is None:
                raise VerificationError(f"{path}: cannot read {member.name}")
            return _metadata_fields(extracted.read(), path, "sdist")
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError(f"{path}: cannot read sdist: {exc}") from exc


def _sdist_zip_metadata(path: Path) -> ArtifactMetadata:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                name
                for name in archive.namelist()
                if PurePosixPath(name).name == "PKG-INFO"
            ]
            if not members:
                raise VerificationError(f"{path}: no PKG-INFO found")
            member = min(members, key=lambda item: len(PurePosixPath(item).parts))
            return _metadata_fields(archive.read(member), path, "sdist")
    except (OSError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"{path}: cannot read sdist: {exc}") from exc


def inspect_artifact(path: Path) -> ArtifactMetadata:
    if path.suffix == ".whl":
        return _wheel_metadata(path)
    if path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return _sdist_tar_metadata(path)
    if path.suffix == ".zip":
        return _sdist_zip_metadata(path)
    raise VerificationError(f"{path}: unsupported distribution artifact type")


def verify_release(tag: str, project: str, paths: Sequence[Path]) -> list[ArtifactMetadata]:
    if not tag.startswith("v") or len(tag) == 1:
        raise VerificationError(f"release tag must have the form v<version>, got {tag!r}")
    try:
        expected_version = Version(tag[1:])
    except InvalidVersion as exc:
        raise VerificationError(f"release tag {tag!r} has an invalid version") from exc

    if not paths:
        raise VerificationError("no distribution artifacts were supplied")
    artifacts = [inspect_artifact(path) for path in paths]
    kinds = {artifact.kind for artifact in artifacts}
    if "wheel" not in kinds or "sdist" not in kinds:
        raise VerificationError("release must contain at least one wheel and one sdist")

    expected_name = canonicalize_name(project)
    for artifact in artifacts:
        if canonicalize_name(artifact.name) != expected_name:
            raise VerificationError(
                f"{artifact.path}: project is {artifact.name!r}, expected {project!r}"
            )
        try:
            artifact_version = Version(artifact.version)
        except InvalidVersion as exc:
            raise VerificationError(
                f"{artifact.path}: invalid metadata version {artifact.version!r}"
            ) from exc
        if artifact_version != expected_version:
            raise VerificationError(
                f"{artifact.path}: metadata version {artifact.version!r} does not "
                f"match release tag {tag!r}"
            )
    return artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, e.g. v1.2.0")
    parser.add_argument("--project", required=True, help="canonical PyPI project name")
    parser.add_argument("artifacts", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifacts = verify_release(args.tag, args.project, args.artifacts)
    except VerificationError as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1
    version = artifacts[0].version
    descriptions = ", ".join(f"{item.kind}:{item.path.name}" for item in artifacts)
    print(f"verified {args.project} {version} for {args.tag}: {descriptions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

