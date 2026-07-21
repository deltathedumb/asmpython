# ASMPython versioning

ASMPython public releases use:

```text
<python-language-version>-<asmpython-semver>
```

For example:

```text
3.14-2.0.0
3.14-2.1.0
3.14-2.1.1
3.15-3.0.0
```

The current planned release is **`3.14-2.0.0`**.

## Components

### Python language version

The prefix identifies the Python language version implemented by the compiler and
runtime. It is not the bootstrap interpreter version and does not identify the
host Python used to build ASMPython.

For `3.14-2.0.0`, the implemented Python language version is `3.14`.

### ASMPython semantic version

The suffix is ASMPython's ordinary semantic version:

```text
major.minor.patch
```

- `major` changes for incompatible compiler, runtime, public API, or ABI changes.
- `minor` changes for backwards-compatible capabilities and substantial features.
- `patch` changes for backwards-compatible fixes and maintenance releases.

The ASMPython semantic version is project-wide and does not reset when the Python
language target changes.

## Canonical release surfaces

For ASMPython `2.0.0` implementing Python `3.14`:

```text
Full release:    3.14-2.0.0
Git tag:         v3.14-2.0.0
Release branch:  beta/3.14-2.0.0
Artifact prefix: asmpython-3.14-2.0.0
PyPI version:    2.0.0
```

The full release identity must be used by GitHub releases, tags, release branches,
downloadable artifacts, build manifests, checksums, provenance, SBOMs,
compatibility reports, benchmark reports, and user-facing release documentation.

## Python package metadata

Python package metadata uses only the ASMPython semantic version:

```toml
[project]
version = "2.0.0"
```

The Python compatibility target is separate metadata. At runtime:

```python
asmpython.__version__                 # "2.0.0"
asmpython.ASMPYTHON_VERSION           # "2.0.0"
asmpython.PYTHON_LANGUAGE_VERSION     # "3.14"
asmpython.FULL_VERSION                # "3.14-2.0.0"
asmpython.RELEASE_VERSION             # "3.14-2.0.0"
```

This keeps PyPI, wheel tooling, dependency resolvers, and semantic-version users
on a conventional package version while preserving the complete compatibility
identity everywhere releases are presented.

## Release rules

1. Every public release must have one full release identity.
2. The Python prefix must match the language behavior claimed by that release.
3. The ASMPython suffix must follow semantic-versioning rules.
4. Development commits and CI run numbers must not be inserted into the stable
   release identity; record them in build manifests or development metadata.
5. Tags use `v<python-version>-<asmpython-semver>`.
6. Release branches tied to one planned release use
   `beta/<python-version>-<asmpython-semver>`.
7. Artifact names must include the full release identity and target, for example:

   ```text
   asmpython-3.14-2.0.0-windows-x86_64.exe
   asmpython-3.14-2.0.0-linux-aarch64.tar.zst
   ```

## Sources of truth

- `VERSION` contains the canonical full release identity.
- `asmpython/_version.py` exposes structured version components.
- `pyproject.toml` contains the ASMPython package semantic version.

Release automation must reject a build when these surfaces disagree.
