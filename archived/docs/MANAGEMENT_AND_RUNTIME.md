# ASMPython management and runtime features

This document describes the unified command surface introduced for the
Python 3.14 / ASMPython 2.x release line.

## Backend discovery

```console
asmpython backends list
asmpython backends jvm
asmpython backends jvm --json
```

`list` reports production, experimental, legacy, registered, and scaffold
backends. Selecting a backend directly shows its aliases, category, status,
default linker, implementation module, and requested/planned parameters.

A scaffold remains discoverable but is explicitly `implemented: no`; attempting
to build with it still raises `NotImplementedError`.

## Frozen IR

Create target-independent IR during a normal build:

```console
asmpython build app.py --ir-only --ir-stage optimized --ir-output bin
asmpython build app.py --ir-only --ir-stage typed --ir-output json
```

Consume it later:

```console
asmpython ir build/app.apir
asmpython ir build/app.apir --target linux --backend x86-64
asmpython ir build/app.apir --info
asmpython ir build/app.apir --info --json
```

The old `asmpython irbuild` name is removed. Binary `.apir` files use a
versioned, integrity-checked container optimized for loading. `.apir.json` is a
structured development/interchange representation. Both preserve shared object
references and cycles in the IR graph.

## Cache management

```console
asmpython cache
asmpython cache path
asmpython cache verify
asmpython cache verify --repair
asmpython cache prune --max-age 30
asmpython cache prune --max-bytes 1073741824
asmpython cache clear
asmpython cache clear app.py
asmpython cache clear --key <entry-key>
```

The old `asmpython invalidate` command is removed. `cache` now owns status,
validation, repair, pruning, and invalidation. Cache writes and profile writes
use temporary files plus atomic replacement.

## Scoped profiles

Profiles can be stored at three scopes:

1. `system`
2. `user`
3. `directory`

Resolution uses that order, so the directory profile has the final override.

```console
asmpython profile
asmpython profile show release
asmpython profile create release --scope user \
  --set target="linux" \
  --set backend="x86-64" \
  --set output_type="executable"
asmpython profile modify release --scope user --set keep=true
asmpython profile delete release --scope user
asmpython profile path --scope directory
```

Profiles may use `extends` and are ordinary schema-versioned JSON. Apply one or
more profiles to a build:

```console
asmpython build app.py --profile release
asmpython build app.py --profile common --profile windows --backend x86-64
```

Profile arguments are injected before explicit command-line options, so the
explicit command line wins.

Default profile locations are:

- Windows system: `%PROGRAMDATA%/ASMPython/profiles.json`
- Windows user: `%APPDATA%/ASMPython/profiles.json`
- Unix system: `/etc/asmpython/profiles.json`
- Unix user: `$XDG_CONFIG_HOME/asmpython/profiles.json`
- Directory: `.asmpython/profiles.json`

Environment variables `ASMPYTHON_SYSTEM_PROFILES` and
`ASMPYTHON_USER_PROFILES` override the first two locations.

## Differential test command

```console
asmpython test tests
asmpython test tests --engine all
asmpython test test_app.py --engine cpython --engine pyinbin
asmpython test tests --target linux --backend x86-64 --json
```

The engines are:

- `cpython`
- `native`
- `pyinbin`
- `hybrid`

For each script, the command captures exit status, stdout, stderr, duration,
build phase, and artifact path. Unless `--no-compare` is passed, every selected
engine is compared to CPython and exact unified output diffs are reported.

## Mixed tracebacks

PyinBin frame execution is instrumented when `asmpython.pyinbin` is imported.
As exceptions unwind, interpreted frames are attached without altering handled
exception semantics. Native code and embedding layers can contribute frames
through:

```python
from asmpython.runtime import native_frame

with native_frame("app.py", "native_callback", 42):
    call_interpreted_code()
```

Uncaught exceptions can be rendered with:

```python
from asmpython.runtime import format_mixed_exception

try:
    run()
except BaseException as error:
    print(format_mixed_exception(error))
```

The CLI enables a carrier mode so existing build/PyinBin reporting prints the
coherent mixed traceback. Library callers continue to receive the original
exception type.

## Ownership and memory management

`asmpython.runtime` exposes the executable ownership reference model:

```python
from asmpython.runtime import MemoryManager, Ownership

manager = MemoryManager("plugin")
handle = manager.track(value, ownership=Ownership.OWNED)
borrowed = handle.borrow()
weak = handle.weak()
handle.release()
manager.collect_cycles()
manager.teardown()
```

Supported ownership states:

- owned
- borrowed
- transferred
- pinned
- host-owned
- runtime-owned

The manager provides explicit retain/release operations, weak handles, pinning,
object-edge tracing, mark-and-sweep cycle collection, child-before-parent
finalizers, double-release detection, statistics, and deterministic teardown.

PyinBin installs hooks for virtual machines, frames, functions, classes,
instances, generators, coroutines, and asynchronous generators. Each VM owns a
manager, refreshes graph edges after execution, collects unreachable cycles,
and exposes deterministic `destroy()` teardown. This Python implementation is
the behavioral reference for native runtime and PortaPy ownership APIs.
