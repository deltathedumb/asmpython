# expect:
# 65
# 90

from __future__ import annotations
import sys
from asmpython import import_binary

if sys.platform == "win32":
    libc = import_binary("msvcrt.dll")
else:
    libc = import_binary("libc.so.6")

@libc.imported
def toupper(x: int) -> int:
    pass

print(libc.toupper(97))
print(libc.toupper(122))
