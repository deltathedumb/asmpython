@echo off
REM build.bat -- Self-compile asmpython with itself -> build\asmpython.exe
REM
REM Usage:
REM   build.bat              compile asmpython -> build\asmpython.exe
REM   build.bat -o out.exe   compile asmpython to a named output

setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "OUT="

REM ---- Parse arguments --------------------------------------------------------
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="-o" (
    set "OUT=%~2"
    shift & shift & goto parse
)
REM Everything else goes to the compiler verbatim.
set "EXTRA_FLAGS=!EXTRA_FLAGS! %~1"
shift & goto parse
:parsed

if "%OUT%"=="" set "OUT=%ROOT%build\asmpython.exe"
if not exist "%ROOT%build" mkdir "%ROOT%build"

echo Self-hosting: compiling asmpython -^> %OUT%
call "%ROOT%asmpython.bat" "%ROOT%asmpython\__main__.py" -o "%OUT%"%EXTRA_FLAGS%
if errorlevel 1 (
    echo.
    echo Self-host build FAILED.
    exit /b 1
)
echo Self-host build OK: %OUT%
