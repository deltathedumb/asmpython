@echo off
REM Wrapper for asmpython. Requires python, nasm, and gcc on PATH.

setlocal

set "ROOT=%~dp0"

set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
python -m asmpython %* --nasm nasm --gcc gcc
