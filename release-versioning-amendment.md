# ASMPython release-versioning amendment

**Status:** Normative amendment to `release-requirements.md`  
**Effective version:** `3.14-1`

ASMPython public versions use:

```text
<python-language-version>-<asmpython-public-build>
```

The ASMPython public-build number is a single monotonically increasing release
counter for the project. It does not reset when the implemented Python language
version changes.

The planned release previously called `3.14.0`, `3.14.0-preview`, or simply the
`3.14` release is now called **`3.14-1`**. Those older names in existing branch
names, historical commits, status documents, and the current release contract
refer to the same planned release and do not identify additional releases.

This amendment changes release identity only. It does not remove, weaken, or
otherwise alter any technical requirement in `release-requirements.md`.

Canonical release surfaces are:

```text
Public version: 3.14-1
Git tag:        v3.14-1
Release branch: beta/3.14-1
PyPI spelling:  3.14-1 (commonly normalized to 3.14.post1)
```

After this amendment merges, new documentation and release automation must use
`3.14-1`. Existing `beta/3.14.0` history may remain as a compatibility alias,
but active release work should move to `beta/3.14-1`.
