@echo off
REM ============================================================================
REM  _download-deps.bat - Fetch any combination of NASM, GCC (MinGW-w64),
REM                      and the embeddable Python distribution into a temp
REM                      directory so asmpython has a complete toolchain.
REM
REM  Usage:
REM     _download-deps.bat [--nasm] [--gcc] [--python] [--all]
REM                        [--dest <dir>]
REM
REM  Examples:
REM     _download-deps.bat --nasm --gcc
REM     _download-deps.bat --all
REM     _download-deps.bat --gcc --dest C:\tools\mingw-cache
REM
REM  Notes:
REM   - Requires Windows 10+ (uses built-in curl.exe and tar.exe).
REM   - Default destination is %TEMP%\asmpython-deps\.
REM   - Existing installs are removed and re-fetched.
REM   - URLs / versions live at the top of this file - update if newer
REM     releases are needed.
REM ============================================================================

setlocal enabledelayedexpansion

REM ---- Pinned versions / URLs ------------------------------------------------
set "NASM_VER=2.16.03"
set "NASM_URL=https://www.nasm.us/pub/nasm/releasebuilds/%NASM_VER%/win64/nasm-%NASM_VER%-win64.zip"

REM WinLibs MinGW-w64 - a full Win32 sysroot (kernel32, msvcrt, ucrt, ld, etc.).
set "GCC_TAG=16.1.0posix-14.0.0-ucrt-r2"
set "GCC_FILE=winlibs-x86_64-posix-seh-gcc-16.1.0-mingw-w64ucrt-14.0.0-r2.zip"
set "GCC_URL=https://github.com/brechtsanders/winlibs_mingw/releases/download/%GCC_TAG%/%GCC_FILE%"

set "PY_VER=3.12.7"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"

REM ---- Arg parsing -----------------------------------------------------------
set "DO_PYTHON=0"
set "DO_NASM=0"
set "DO_GCC=0"
set "DEST=%TEMP%\asmpython-deps"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--python" (set "DO_PYTHON=1" & shift & goto parse)
if /i "%~1"=="--nasm"   (set "DO_NASM=1"   & shift & goto parse)
if /i "%~1"=="--gcc"    (set "DO_GCC=1"    & shift & goto parse)
if /i "%~1"=="--all"    (set "DO_PYTHON=1" & set "DO_NASM=1" & set "DO_GCC=1" & shift & goto parse)
if /i "%~1"=="--dest"   (set "DEST=%~2" & shift & shift & goto parse)
if /i "%~1"=="-h"       goto usage
if /i "%~1"=="--help"   goto usage
echo Unknown argument: %~1
echo.
goto usage
:parsed

if "%DO_PYTHON%%DO_NASM%%DO_GCC%"=="000" goto usage

if not exist "%DEST%" mkdir "%DEST%"

REM Resolve %DEST% to a normalized absolute path (no trailing slash).
for %%i in ("%DEST%") do set "DEST=%%~fi"

REM Confirm tools are available.
where curl.exe >nul 2>&1
if errorlevel 1 (
    echo Error: curl.exe not found on PATH. Windows 10+ ships with curl in System32.
    exit /b 1
)
where tar.exe >nul 2>&1
if errorlevel 1 (
    echo Error: tar.exe not found on PATH. Windows 10+ ships with tar in System32.
    exit /b 1
)

echo Destination: %DEST%
echo.

if "%DO_NASM%"=="1"   call :get_nasm   || goto fail
if "%DO_GCC%"=="1"    call :get_gcc    || goto fail
if "%DO_PYTHON%"=="1" call :get_python || goto fail

echo.
echo === All requested dependencies installed ===
if "%DO_NASM%"=="1"   echo   nasm:   %DEST%\nasm\nasm.exe
if "%DO_GCC%"=="1"    echo   gcc:    %DEST%\mingw64\bin\gcc.exe
if "%DO_PYTHON%"=="1" echo   python: %DEST%\python\python.exe
echo.
echo To use with asmpython, pass them explicitly:
if "%DO_NASM%"=="1"   echo   --nasm "%DEST%\nasm\nasm.exe"
if "%DO_GCC%"=="1"    echo   --gcc  "%DEST%\mingw64\bin\gcc.exe"
echo.
echo Or set environment variables:
if "%DO_NASM%"=="1"   echo   set ASMPYTHON_NASM=%DEST%\nasm\nasm.exe
if "%DO_GCC%"=="1"    echo   set ASMPYTHON_GCC=%DEST%\mingw64\bin\gcc.exe
exit /b 0

REM ---- Subroutines -----------------------------------------------------------

:get_nasm
echo === NASM %NASM_VER% ===
set "ZIP=%DEST%\_nasm.zip"
echo Downloading %NASM_URL%
curl.exe -fL --progress-bar -o "%ZIP%" "%NASM_URL%" || exit /b 1
if exist "%DEST%\nasm" rmdir /s /q "%DEST%\nasm"
echo Extracting...
tar.exe -xf "%ZIP%" -C "%DEST%" || exit /b 1
if exist "%DEST%\nasm-%NASM_VER%" (
    move "%DEST%\nasm-%NASM_VER%" "%DEST%\nasm" >nul
)
del "%ZIP%"
if not exist "%DEST%\nasm\nasm.exe" (
    echo Error: nasm.exe not found after extraction.
    exit /b 1
)
echo OK: %DEST%\nasm\nasm.exe
echo.
exit /b 0

:get_gcc
echo === GCC ^(MinGW-w64, %GCC_TAG%^) ===
set "ZIP=%DEST%\_mingw64.zip"
echo Downloading %GCC_URL%
echo This is a large file (~275 MB); be patient.
curl.exe -fL --progress-bar -o "%ZIP%" "%GCC_URL%" || exit /b 1
if exist "%DEST%\mingw64" rmdir /s /q "%DEST%\mingw64"
echo Extracting...
tar.exe -xf "%ZIP%" -C "%DEST%" || exit /b 1
del "%ZIP%"
if not exist "%DEST%\mingw64\bin\gcc.exe" (
    echo Error: gcc.exe not found after extraction. Archive layout may have changed.
    exit /b 1
)
echo OK: %DEST%\mingw64\bin\gcc.exe
echo.
exit /b 0

:get_python
echo === Python %PY_VER% ^(embeddable^) ===
set "ZIP=%DEST%\_python.zip"
echo Downloading %PY_URL%
curl.exe -fL --progress-bar -o "%ZIP%" "%PY_URL%" || exit /b 1
if exist "%DEST%\python" rmdir /s /q "%DEST%\python"
mkdir "%DEST%\python"
echo Extracting...
tar.exe -xf "%ZIP%" -C "%DEST%\python" || exit /b 1
del "%ZIP%"
if not exist "%DEST%\python\python.exe" (
    echo Error: python.exe not found after extraction.
    exit /b 1
)
echo OK: %DEST%\python\python.exe
echo.
exit /b 0

:usage
echo Usage:
echo     _download-deps.bat [TOOLS] [--dest ^<dir^>]
echo.
echo Fetch portable copies of the build toolchain so `asmpython.bat` can
echo compile without anything installed on the host.
echo.
echo Tools (pick any combination):
echo     --nasm        NASM assembler %NASM_VER%               (~ 0.6 MB)
echo     --gcc         MinGW-w64 GCC %GCC_TAG% (full Win32 sysroot) (~275 MB)
echo     --python      Embeddable Python %PY_VER%               (~ 11 MB)
echo     --all         shorthand for --nasm --gcc --python
echo.
echo Other options:
echo     --dest ^<dir^>  destination directory (default: %%TEMP%%\asmpython-deps)
echo     -h, --help    show this message and exit
echo.
echo Examples:
echo     _download-deps.bat --nasm --gcc
echo     _download-deps.bat --all --dest C:\asmpython-toolchain
echo.
echo After install, plug the paths into asmpython with:
echo     --nasm "%%DEST%%\nasm\nasm.exe"
echo     --gcc  "%%DEST%%\mingw64\bin\gcc.exe"
echo or simply rerun asmpython.bat - it auto-detects .\tools\ and bootstraps
echo any missing tools.
exit /b 1

:fail
echo.
echo Aborted with errors.
exit /b 1
