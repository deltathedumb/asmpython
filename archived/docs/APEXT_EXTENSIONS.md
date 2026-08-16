# ASMPython `.apext` extensions

ASMPython extensions are deterministic ZIP archives containing Python code and
an `apext.json` manifest. They can register backends, targets, linkers, mlang
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
and `--output` chooses the archive path. The `module:object` target identifies
the exact exported `Extension` descriptor, while other package modules may
register backends, linkers, or lifecycle behavior during loading.

## Registering a target

A *target* is the platform code is emitted for -- object format, entry point,
syscall convention, runtime startup. A *backend* is the code generator that
produces it. They are separate registries because they vary independently: one
code generator serves several platforms, and one platform can be reached by
several code generators.

```python
from asmpython import target
from asmpython._targets.target_linux import LinuxCodegen

class MyOSCodegen(LinuxCodegen):
    SYSCALL_WRITE = 4          # usually this is most of the difference

target.Target(name="my_os", codegen=MyOSCodegen, aliases=("myos",))
```

Then `asmpython build myfile.py --target my_os`.

The compiler holds no list of platforms: `driver.py` asks the registry for a
target by name, so adding one is a registration rather than an edit to the
compiler. The built-in targets go through exactly the same path, and load
lazily -- a Windows build never imports the 16-bit freestanding lowering.

Registering an existing name replaces it, so an extension may override a
built-in platform. `target.available()` lists what is registered and
`target.aliases()` maps the short names.

## Installation scopes

```bash
asmpython extension install my_extension.apext --system
asmpython extension install my_extension.apext --user
asmpython extension install my_extension.apext --local
```

If no scope is passed, installation defaults to `--user`. Discovery examines
system, then user, then local extensions. A higher-precedence local package with
the same id replaces a user package, and a user package replaces a system one;
only the selected package is executed.

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

- format and extension API version,
- extension id and version,
- entry module and object name,
- production suitability,
- SHA-256 for every packaged file.

Archive members are path-validated, every non-manifest file must be hashed,
hashes are checked during installation, listing, and loading, writes are atomic,
and packaging uses sorted files plus fixed ZIP timestamps for reproducible
output. Unsupported extension API versions and corrupted installed packages fail
loudly rather than being silently skipped.

## Build integration

Installed extensions are loaded before backend discovery, frozen-IR compilation,
differential tests, and normal builds. Build reports record the selected
extension id, version, scope, path, and production-suitability status.

Backends and linkers registered by extensions receive the same shared build
context as built-in implementations, including `speedy_lossy`, `bleach`, and
the normalized `sanitizers` tuple.

## Trust model

Extensions execute inside the compiler's host Python process and are therefore
fully trusted code. Hash verification detects corruption or unexpected archive
changes; it does not sandbox malicious extension code. Install only extensions
you trust.
