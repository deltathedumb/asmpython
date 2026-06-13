@echo off
REM build.bat -- Compile a Python source file with asmpython.
REM
REM Usage:
REM   build.bat <source.py>              compile to source.exe
REM   build.bat <source.py> -o out.exe   compile to a named output
REM   build.bat <source.py> --run        compile and run
REM   build.bat <source.py> --emit-asm   also write the .asm file
REM   build.bat --test                   run the full test suite
REM   build.bat --selfhost               compile asmpython with itself -> build\asmpython.exe
REM
REM Any extra flags are forwarded to asmpython.

setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "SRC="
set "OUT="
set "RUN=0"
set "TEST=0"
set "SELFHOST=0"
set "EXTRA_FLAGS="

REM ---- Parse arguments --------------------------------------------------------
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--test" (
    set "TEST=1"
    shift & goto parse
)
if /i "%~1"=="--selfhost" (
    set "SELFHOST=1"
    shift & goto parse
)
if /i "%~1"=="--run" (
    set "RUN=1"
    shift & goto parse
)
if /i "%~1"=="-o" (
    set "OUT=%~2"
    shift & shift & goto parse
)
REM If the argument ends in .py, treat it as the source file.
set "_arg=%~1"
if "!_arg:~-3!"==".py" (
    set "SRC=%~1"
    shift & goto parse
)
REM Everything else goes to the compiler verbatim.
set "EXTRA_FLAGS=!EXTRA_FLAGS! %~1"
shift & goto parse
:parsed

REM ---- Test mode --------------------------------------------------------------
if "%TEST%"=="1" (
    echo Running asmpython test suite...
    py tests\runner.py
    exit /b %errorlevel%
)

REM ---- Selfhost mode: compile asmpython with itself ---------------------------
if "%SELFHOST%"=="1" (
    set "SELFHOST_SRC=%ROOT%asmpython\__main__.py"
    if "%OUT%"=="" set "OUT=%ROOT%build\asmpython.exe"
    if not exist "%ROOT%build" mkdir "%ROOT%build"
    echo Self-hosting: compiling asmpython -^> !OUT!
    call "%ROOT%asmpython.bat" "!SELFHOST_SRC!" -o "!OUT!"%EXTRA_FLAGS%
    if errorlevel 1 (
        echo.
        echo Self-host build FAILED.
        exit /b 1
    )
    echo Self-host build OK: !OUT!
    exit /b 0
)

REM ---- Require a source file --------------------------------------------------
if "%SRC%"=="" (
    echo.
    echo  build.bat — compile Python with asmpython
    echo.
    echo  Usage:
    echo    build.bat ^<source.py^>              compile to ^<source^>.exe
    echo    build.bat ^<source.py^> -o out.exe   compile to a named output
    echo    build.bat ^<source.py^> --run        compile and run
    echo    build.bat ^<source.py^> --emit-asm   also write the .asm alongside
    echo    build.bat --test                    run the full test suite
    echo    build.bat --selfhost               compile asmpython with itself
    echo.
    exit /b 1
)

if not exist "%SRC%" (
    echo Error: source file not found: %SRC%
    exit /b 1
)

REM ---- Derive output path if not given ----------------------------------------
if "%OUT%"=="" (
    REM Strip directory and .py extension, append .exe.
    for %%F in ("%SRC%") do set "OUT=%%~dpnF.exe"
)

REM ---- Compile ----------------------------------------------------------------
echo Compiling %SRC% -> %OUT%
call "%ROOT%asmpython.bat" "%SRC%" -o "%OUT%"%EXTRA_FLAGS%
if errorlevel 1 (
    echo.
    echo Build FAILED.
    exit /b 1
)
echo Build OK.

REM ---- Run (optional) ---------------------------------------------------------
if "%RUN%"=="1" (
    echo.
    echo Running %OUT%...
    echo ----------------------------------------
    "%OUT%"
    echo ----------------------------------------
    echo Exit code: %errorlevel%
)
