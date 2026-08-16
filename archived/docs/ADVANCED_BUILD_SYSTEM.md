# ASMPython advanced build system

These features configure and inspect compilation without changing Python syntax
or Python runtime semantics.

## `build.config.toml`

A project may place `build.config.toml` in its root. `asmpython build` searches
upward from the working directory. Explicit CLI options override config values.

```toml
[build]
entry = "app.py"
output = "dist/app.exe"
type = "executable"
backend = "x86-64"
linker = "builtin"
target = ["pc", "windows", "msvc"]
fastcomp = true
debug = true
debug_format = "pdb"
locked = true
report = "dist/build-report.json"

sanitize = ["address", "undefined"]

[embed]
include = ["LICENSE", "assets/**", "config/*.json"]
exclude = ["assets/source/**"]
```

Use another file with `--config PATH`, or disable discovery with `--no-config`.

## Structured targets

```bash
asmpython build app.py --target pc windows msvc
asmpython build app.py --target pc linux gnu
asmpython build app.py --target embedded none eabi
```

The three fields are **platform**, **system**, and **ABI**. They normalize to a
canonical string such as `pc-windows-msvc`. The full triple is passed to
backends and linkers as `target_triple`; compatibility adapters may lower it to
the older OS-only target expected by legacy code.

## Build plans

```bash
asmpython build --graphonly
asmpython build app.py --graphonly --graph-format json
asmpython build app.py --graphonly --graph-format dot --graph-output build.dot
```

Graph-only mode performs configuration, dependency discovery, capability and
toolchain negotiation, and FastComp cache inspection, then stops before code
generation. Plans include module imports, native/PyinBin partition stages,
backend/linker dependencies, resources, bundling, verification, and output.

## FastComp state

`--fastcomp` persists more than final object fragments. Its shared state stores:

- parsed project modules,
- dependency hashes and the import graph,
- optimized IR supplied by the frontend,
- serializable backend state supplied by the selected backend,
- compiler release, target triple, and backend identity.

Every backend receives:

```python
args["fastcomp"]
args["fastcomp_state_path"]
args["target_triple"]
```

A source or dependency hash change invalidates the state. Backends may use the
state path to retain register-allocation tables, symbol indexes, object
fragments, or other deterministic incremental data.

## Embedded files

```bash
asmpython build app.py --embed LICENSE --embed assets/logo.png
```

Resources are appended after the native binary in a deterministic, hashed
container. PE, ELF, and Mach-O loaders ignore trailing data. ASMPython locates
the footer from the running executable and exposes a dictionary-shaped tree:

```python
from asmpython import embedded

LICENSE = embedded["LICENSE"]                 # bytes
LOGO = embedded["assets"]["logo.png"]        # bytes
TEXT = embedded.read_text("LICENSE")          # str
```

Appending resources does not rewrite executable headers. Re-embedding replaces
the previous ASMPython resource suffix rather than stacking duplicates.

## Debugger support

```bash
asmpython build app.py --debug
asmpython build app.py --debug --debug-format dwarf
asmpython build app.py --debug --debug-format pdb
```

The selected backend and effective linker must advertise the requested debug
format. GCC/MinGW builds receive DWARF or CodeView flags. Each debug build also
receives an `.asmpdebug.json` sidecar recording artifact identity, source,
target, backend/linker, native format, and mixed native/PyinBin frame support.
Backends fill concrete source and variable locations as their debug emitters are
implemented.

## Artifact verification

```bash
asmpython verify app.exe
asmpython verify library.so package.apext --json
```

Verification recognizes PE, ELF, Mach-O, WebAssembly, static archives, JAR/ZIP,
Python bytecode, and `.apext`. It checks structural headers, ZIP CRCs, extension
manifests, appended resource hashes, detached certificate signatures, and ABI
sidecars where present. Successful builds run artifact verification before the
final result is accepted.

## Build locks, signatures, and ABI tools

```bash
asmpython lock create app.py --target pc windows msvc
asmpython build app.py --locked

asmpython sign package extension.apext \
  --certificate publisher.crt --key publisher.key
asmpython sign verify extension.apext

asmpython abi dump library.dll
asmpython abi diff previous.abi.json current.abi.json
asmpython abi check library.dll --against released.abi.json
```

Lockfiles include effective component contracts, dependency probes, extension
hashes, sources, FastComp/debug modes, and embedded-resource hashes. Signatures
are detached `.apsig` files backed by X.509 certificates. Certificate-authority
policy is intentionally caller-managed for now; cryptographic verification does
not imply that ASMPython trusts the publisher.
