# ASMPython `.apext` extensions

ASMPython extensions are deterministic ZIP archives containing Python code and
an `apext.json` manifest. They can register backends, linkers, mlang
configurations, lifecycle hooks, or other host-side compiler integrations.

## Minimal extension

```text
my_extension/
└── main.py
```

```python
from asmpython import Extension

extension = Extension(
    id="my_extension",
    version="1.0.0",
    description="Example compiler extension",
)
```

Package the named object:

```bash
asmpython extension package main:extension
```

The default output is `<extension-id>.apext`. `--root` chooses the source tree
and `--output` chooses the archive path.

## Installation scopes

```bash
asmpython extension install my_extension.apext --system
asmpython extension install my_extension.apext --user
asmpython extension install my_extension.apext --local
```

If no scope is passed, installation defaults to `--user`. Build-time discovery
loads system extensions first, then user extensions, then local extensions. A
higher-precedence package with the same id replaces the lower-precedence one.

```bash
asmpython extension list
asmpython extension list --json
asmpython extension path local
asmpython extension uninstall my_extension
```

Uninstall without a scope removes every installed copy of that id. Pass one of
the scope flags to remove only that scope.

## Download and install

```bash
asmpython extension get \
  "https://example.com/my_extension/download/my_extension.apext"
```

Downloads require HTTPS by default. `--sha256` pins the complete archive digest.
`--allow-http` exists only for explicitly trusted development servers.

## Archive guarantees

`apext.json` records:

- format and API version,
- extension id and version,
- entry module and object name,
- production suitability,
- SHA-256 for every packaged file.

Archive members are path-validated, hashes are checked during installation and
loading, writes are atomic, and packaging uses sorted files plus fixed ZIP
timestamps for reproducible output.

## Trust model

Extensions execute inside the compiler's host Python process and are therefore
fully trusted code. Hash verification detects corruption or unexpected archive
changes; it does not sandbox malicious extension code. Install only extensions
you trust.
