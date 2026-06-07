@echo off

REM Wrapper for GCC
arguments=%*
gcc_path="%~dp0tools\mingw64\bin\gcc.exe"

%gcc_path% %arguments%