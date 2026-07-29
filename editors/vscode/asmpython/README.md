# ASMPython for VS Code

Tooling integration for [asmpython](https://github.com/), the Python-to-native
compiler. Works directly on `.py` files — asmpython source is a restricted
subset of Python, not a separate language, so this extension layers on top
of VS Code's built-in Python support instead of replacing it.

## Features

- **Live diagnostics.** Runs `asmpython --check --json` as you type (debounced)
  and on save, and shows errors as native Problems-panel entries / red
  squiggles in the editor.
- **Suppressing diagnostics.** Add `# asmpython: ignore` at the end of a line
  to suppress diagnostics on that line, or `# asmpython: ignoreall` anywhere
  in the file to suppress all of them for that file.
- **Compile / Run / Check / Emit Assembly** commands, available from the
  command palette, the editor title bar (▶ / ⚙ icons), and keybindings
  (`Ctrl+F5` to compile-and-run, `Ctrl+Shift+B` to compile).
- **Explain Error Code** — looks up `asmpython --explain <CODE>` for any
  diagnostic code (e.g. `E001`).
- **Status bar item** showing the active file's check status; click it to
  open the output channel.
- **Task provider** (`"type": "asmpython"` in `tasks.json`) for projects that
  want to pin a specific entry file or `--target` as their build task.

## Requirements

An asmpython installation reachable one of these ways:

1. `asmpython.executablePath` set explicitly in settings, or
2. a bare `asmpython` on your `PATH`, or
3. a Python installation with the `asmpython` package installed, invoked as
   `py -m asmpython` / `python3 -m asmpython` / `python -m asmpython`.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `asmpython.executablePath` | `""` | Explicit path to the asmpython executable. |
| `asmpython.checkOnType` | `true` | Re-check as you type, not just on save. |
| `asmpython.checkDebounceMs` | `400` | Debounce delay for check-on-type. |
| `asmpython.target` | `""` | Default `--target` for Compile/Run (empty = host platform). |
| `asmpython.extraCompileArgs` | `[]` | Extra CLI args appended to every compile. |
| `asmpython.outputDirectory` | `"build"` | Where Compile/Run write the output executable. |

## Suppressing diagnostics

```python
import legacy_module  # asmpython: ignore

# asmpython: ignoreall
# (rest of this file is excluded from diagnostics entirely)
```

## Building from source

```sh
cd editors/vscode
npm install
npm run compile
```

Press `F5` in this folder to launch an Extension Development Host with the
extension loaded, or run `npx vsce package` to produce a `.vsix`.
