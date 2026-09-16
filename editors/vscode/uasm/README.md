# uasm for VS Code

Tooling integration for [uasm](https://github.com/), the Python-to-native
compiler. Works directly on `.py` files — uasm source is a restricted
subset of Python, not a separate language, so this extension layers on top
of VS Code's built-in Python support instead of replacing it.

## Features

- **Live diagnostics.** Runs `uasm --check --json` as you type (debounced)
  and on save, and shows errors as native Problems-panel entries / red
  squiggles in the editor.
- **Suppressing diagnostics.** Add `# uasm: ignore` at the end of a line
  to suppress diagnostics on that line, or `# uasm: ignoreall` anywhere
  in the file to suppress all of them for that file.
- **Compile / Run / Check / Emit Assembly** commands, available from the
  command palette, the editor title bar (▶ / ⚙ icons), and keybindings
  (`Ctrl+F5` to compile-and-run, `Ctrl+Shift+B` to compile).
- **Explain Error Code** — looks up `uasm --explain <CODE>` for any
  diagnostic code (e.g. `E001`).
- **Status bar item** showing the active file's check status; click it to
  open the output channel.
- **Task provider** (`"type": "uasm"` in `tasks.json`) for projects that
  want to pin a specific entry file or `--target` as their build task.

## Requirements

An uasm installation reachable one of these ways:

1. `uasm.executablePath` set explicitly in settings, or
2. a bare `uasm` on your `PATH`, or
3. a Python installation with the `uasm` package installed, invoked as
   `py -m uasm` / `python3 -m uasm` / `python -m uasm`.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `uasm.executablePath` | `""` | Explicit path to the uasm executable. |
| `uasm.checkOnType` | `true` | Re-check as you type, not just on save. |
| `uasm.checkDebounceMs` | `400` | Debounce delay for check-on-type. |
| `uasm.target` | `""` | Default `--target` for Compile/Run (empty = host platform). |
| `uasm.extraCompileArgs` | `[]` | Extra CLI args appended to every compile. |
| `uasm.outputDirectory` | `"build"` | Where Compile/Run write the output executable. |

## Suppressing diagnostics

```python
import legacy_module  # uasm: ignore

# uasm: ignoreall
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
