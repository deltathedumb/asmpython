@echo off
REM Wrapper for asmpython. If any required tool (Python, NASM, GCC) is missing
REM from ./tools/, _download-deps.bat fetches it before we run the compiler.

setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%tools\python\python.exe"
set "GCC=%ROOT%tools\mingw64\bin\gcc.exe"
set "NASM=%ROOT%tools\nasm\nasm.exe"
REM kernel32.a is the canary file for "this MinGW has a full Win32 sysroot".
REM Some stripped distributions ship gcc.exe but no libkernel32.a, which
REM breaks linking with "cannot find -lkernel32".
set "GCC_SYSROOT=%ROOT%tools\mingw64\x86_64-w64-mingw32\lib\libkernel32.a"

set "MISSING="
if not exist "%PYTHON%" set "MISSING=%MISSING% --python"
if not exist "%NASM%"   set "MISSING=%MISSING% --nasm"
if not exist "%GCC%"    set "MISSING=%MISSING% --gcc"
if exist "%GCC%" if not exist "%GCC_SYSROOT%" set "MISSING=%MISSING% --gcc"

if defined MISSING (
    echo asmpython: bootstrapping missing dependencies via _download-deps.bat:%MISSING%
    call "%ROOT%_download-deps.bat" %MISSING% --dest "%ROOT%tools"
    if errorlevel 1 (
        echo asmpython: dependency download failed.
        exit /b 1
    )
)

REM Embeddable Python ignores PYTHONPATH and uses python3XX._pth instead.
REM Make sure the repo root is on sys.path so `-m asmpython` resolves the
REM local package. Idempotent: rewriting the file with the same content
REM each run is cheap.
for %%P in ("%ROOT%tools\python\python3*._pth") do (
    > "%%P" echo python312.zip
    >> "%%P" echo .
    >> "%%P" echo %ROOT%
)

"%PYTHON%" -m asmpython %* --nasm "%NASM%" --gcc "%GCC%"
