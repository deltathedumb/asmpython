# expect:
# 1
# 0
# 0
# 1
# 0
# 0
# 1
# 0

from __future__ import annotations
import os
import ospath

print(ospath.isdir("."))
print(ospath.isfile("."))
print(ospath.isdir("tests/cases/301_ospath_isdir_isfile.py"))
print(ospath.isfile("tests/cases/301_ospath_isdir_isfile.py"))
print(ospath.isdir("does_not_exist_xyz"))
print(ospath.isfile("does_not_exist_xyz"))

d: str = "_tmp_301_isdir_dir"
if ospath.isdir(d) == 0:
    os.mkdir(d, 511)
print(ospath.isdir(d))
print(ospath.isfile(d))
os.rmdir(d)
