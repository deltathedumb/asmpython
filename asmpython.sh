#!/usr/bin/env bash

# Wrapper for asmpython

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The legacy compiler now lives under legacy/; this wrapper still drives it.
# The new compiler is `src/asmpython`, installed as the `asmpython` command.
export PYTHONPATH="$SCRIPT_DIR/legacy:$PYTHONPATH"

PYTHON_PATH="$SCRIPT_DIR/tools/python/python"
GCC_PATH="$SCRIPT_DIR/tools/mingw64/bin/gcc"
NASM_PATH="$SCRIPT_DIR/tools/nasm/nasm"

"$PYTHON_PATH" -m asmpython "$@" \
    --nasm "$NASM_PATH" \
    --gcc "$GCC_PATH"