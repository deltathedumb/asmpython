@echo off

REM Wrapper for NASM
arguments=%*
nasm_path="%~dp0tools\nasm\bin\nasm.exe"

%nasm_path% %arguments%