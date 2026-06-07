#!/usr/bin/env bash

# Wrapper for serpent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_PATH="$SCRIPT_DIR/tools/python/python"
GCC_PATH="$SCRIPT_DIR/tools/mingw64/bin/gcc"
NASM_PATH="$SCRIPT_DIR/tools/nasm/nasm"

"$PYTHON_PATH" -m serpent "$@" \
    --nasm "$NASM_PATH" \
    --gcc "$GCC_PATH"