# Change Log

All notable changes to the "asmpython" extension will be documented in this file.

## [0.2.0]

Rewritten from a syntax-highlighting-only extension into a real tooling
integration, since asmpython source is just restricted Python (`.py`
files) rather than a distinct language:

- Live diagnostics: runs `asmpython --check --json` on open/change/save
  and republishes results as native VS Code diagnostics (Problems panel,
  squiggles), debounced while typing.
- `# asmpython: ignore` (suppress diagnostics on one line) and
  `# asmpython: ignoreall` (suppress diagnostics for the whole file).
- Commands: Compile, Compile and Run, Check, Emit Assembly, Explain Error
  Code, Show Output. Bound to `Ctrl+F5` (run) and `Ctrl+Shift+B` (compile)
  by default.
- Status bar item showing the current file's check status; click to open
  the output channel.
- `asmpython` task provider (`"type": "asmpython"` in tasks.json) plus a
  problem matcher for parsing build-task output.
- `asmpython.executablePath` setting for explicit toolchain paths;
  auto-detects `asmpython` on PATH or falls back to `py -m asmpython` /
  `python3 -m asmpython` / `python -m asmpython` otherwise.

Dropped the standalone `.asmpy`/`.asmpython`/`.apy` language ID and
TextMate grammar from 0.1.0 — no real source in the repo uses those
extensions, and a separate grammar fights VS Code's built-in Python
highlighting on the `.py` files everyone actually writes.

## [0.1.0]

- Initial release: syntax highlighting only.