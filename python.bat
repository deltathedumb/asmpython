@echo off

REM Wrapper for Python
arguments=%*
python_path="%~dp0tools\python\python.exe"

%python_path% %arguments%