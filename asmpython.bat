@echo off
REM Wrapper for asmpython. Requires python, nasm, and gcc on PATH.

setlocal

set "ROOT=%~dp0"

REM The legacy compiler now lives under legacy/; this wrapper still drives it.
REM The new compiler is src/asmpython, installed as the `asmpython` command.
set "PYTHONPATH=%ROOT%legacy;%ROOT%;%PYTHONPATH%"
python -m asmpython %* --nasm nasm --gcc gcc
