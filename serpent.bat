@echo off

REM Wrapper for mamba
set arguments=%*
set python_path="%~dp0tools\python\python.exe"
set gcc_path="%~dp0tools\mingw64\bin\gcc.exe"
set nasm_path="%~dp0tools\nasm\nasm.exe"

%python_path% -m mamba %arguments% --nasm "%nasm_path%" --gcc "%gcc_path%"