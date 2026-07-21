# Frozen IR and fast compilation

ASMPython can stop after its target-independent compiler state and store that IR
for caches, tooling, or later backend compilation.

```console
asmpython build app.py --ir-only --ir-stage optimized --ir-output bin
asmpython build app.py --ir-only --ir-stage typed --ir-output json
```

Binary output uses the versioned `.apir` container and is optimized for loading.
JSON output defaults to `.apir.json` and exposes object kinds, fields, references,
compiler metadata, and independently hashed functions/classes.

Frozen IR can be compiled later:

```console
asmpython build build/app.apir --target windows
```

## Fastcomp

```console
asmpython build app.py --fastcomp
```

For the Windows and Linux NASM targets, fastcomp splits generated assembly into
a base/runtime/data fragment plus one fragment per function or method. It hashes
each generated fragment, reassembles only changed fragments, and links the cached
`.o`/`.obj` files together. Changes that affect labels or shared data naturally
invalidate every dependent fragment because hashes are calculated from final
assembly rather than source text guesses.

Other targets and `--onedir` currently fall back safely to normal compilation.

Caches use the platform cache directory, or `ASMPYTHON_CACHE_DIR`/`--cache-dir`.
Clear them with:

```console
asmpython invalidate
asmpython invalidate path/to/app.py
```
