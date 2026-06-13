@echo off
REM build.bat -- Self-compile asmpython with itself for Windows and Linux.
REM
REM Usage:
REM   build.bat              build\asmpython.exe  +  build\asmpython-linux

setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "SRC=%ROOT%asmpython\__main__.py"
set "OUT_WIN=%ROOT%build\asmpython.exe"
set "OUT_LIN=%ROOT%build\asmpython-linux"
set "EXTRA_FLAGS="

REM ---- Pass through any extra compiler flags ----------------------------------
:parse
if "%~1"=="" goto parsed
set "EXTRA_FLAGS=!EXTRA_FLAGS! %~1"
shift & goto parse
:parsed

if not exist "%ROOT%build" mkdir "%ROOT%build"

REM ---- Windows build ----------------------------------------------------------
echo Self-hosting ^(windows^): compiling asmpython -^> %OUT_WIN%
call "%ROOT%asmpython.bat" "%SRC%" -o "%OUT_WIN%" --target windows%EXTRA_FLAGS%
if errorlevel 1 (
    echo.
    echo Self-host build FAILED ^(windows^).
    exit /b 1
)
echo Self-host build OK: %OUT_WIN%

REM ---- Linux build ------------------------------------------------------------
echo.
echo Self-hosting ^(linux^): compiling asmpython -^> %OUT_LIN%
call "%ROOT%asmpython.bat" "%SRC%" -o "%OUT_LIN%" --target linux%EXTRA_FLAGS%
if errorlevel 1 (
    echo.
    echo Self-host build FAILED ^(linux^).
    exit /b 1
)
echo Self-host build OK: %OUT_LIN%
