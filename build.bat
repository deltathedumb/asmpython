@echo off

set ASMPYTHON_BUILD_BATCH=1

python "%~dp0build_x86_win_linux.py" %*

pause