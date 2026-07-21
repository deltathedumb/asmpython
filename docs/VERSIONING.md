# ASMPython versioning

ASMPython public releases use:

```text
<python-language-version>-<asmpython-build>
```

For example:

```text
3.14-1
3.14-2
3.15-3
```

## Components

- `python-language-version` identifies the Python language version implemented by
  the release. It is not the bootstrap interpreter version and does not identify
  the host Python used to build ASMPython.
- `asmpython-build` is one monotonically increasing public-release counter for
  ASMPython as a whole. It does not reset when the Python language version
  changes.

The first public release in this scheme is `3.14-1`.

## Release rules

1. Increment the build number exactly once for every public ASMPython release.
2. Do not consume a build number for an ordinary development commit or CI run.
3. Keep the target public version constant during development of that release.
4. Put development state, commit hashes, CI run numbers, and platform details in
   separate build metadata and manifests rather than adding semantic-version
   fields to the public version.
5. Use tags in the form `v<python-version>-<build>`, such as `v3.14-1`.
6. Release branches should use `beta/<python-version>-<build>` when tied to one
   planned public release.

## Python package metadata

The canonical ASMPython spelling is `3.14-1`. PEP 440 accepts this spelling but
normalizes it to `3.14.post1` in some Python packaging tools and indexes. These
refer to the same ASMPython public release:

```text
ASMPython public version: 3.14-1
Normalized package version: 3.14.post1
```

The CLI, runtime API, release tags, build manifests, checksums, and documentation
must use the canonical `3.14-1` spelling. Package-index normalization must not be
mistaken for a different release.

## Sources of truth

- `VERSION` contains the canonical public spelling.
- `asmpython/_version.py` exposes the structured version components and
  `asmpython.__version__`.
- `pyproject.toml` declares the matching package release.

Release automation must reject a build when these surfaces disagree after PEP
440 normalization is taken into account.
