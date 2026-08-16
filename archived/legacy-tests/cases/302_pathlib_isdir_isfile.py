# expect:
# 1
# 0
# 0
# 1
# 0
# 0

from __future__ import annotations
from pathlib import Path

print(Path(".").is_dir())
print(Path(".").is_file())
print(Path("tests/cases/247_pathlib_module.py").is_dir())
print(Path("tests/cases/247_pathlib_module.py").is_file())
print(Path("does_not_exist_xyz").is_dir())
print(Path("does_not_exist_xyz").is_file())
