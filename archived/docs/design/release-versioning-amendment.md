# ASMPython release-versioning amendment

**Status:** Normative amendment to `release-requirements.md`  
**Effective full release:** `3.14-2.0.0`

ASMPython public release identities use:

```text
<python-language-version>-<asmpython-semver>
```

The prefix identifies the implemented Python language version. The suffix is
ASMPython's project-wide semantic version in `major.minor.patch` form.

The planned release previously called `3.14.0`, `3.14.0-preview`, `3.14-1`, or
simply the `3.14` release is now called **`3.14-2.0.0`**. Those older names in
existing branch names, historical commits, status documents, and the current
release contract refer to the same planned release and do not identify additional
public releases.

This amendment changes release identity only. It does not remove, weaken, or
otherwise alter any technical requirement in `release-requirements.md`.

Canonical release surfaces are:

```text
Full release:    3.14-2.0.0
ASMPython:       2.0.0
Python language: 3.14
Git tag:         v3.14-2.0.0
Release branch:  beta/3.14-2.0.0
PyPI version:    2.0.0
```

The full release identity is canonical for GitHub releases, tags, branches,
artifacts, manifests, checksums, provenance, SBOMs, compatibility reports,
benchmark reports, and release documentation. Python package metadata uses the
ASMPython semantic version and carries the Python language target separately.

After this amendment merges, new documentation and release automation must use
`3.14-2.0.0`. Existing `beta/3.14.0` history may remain as a compatibility alias,
but active release work should move to `beta/3.14-2.0.0`.
