# expect:
# Windows
# x86_64
# asmpython
# asmpython

import platform

s: str = platform.system()
print(s)
m: str = platform.machine()
print(m)
impl: str = platform.python_implementation()
print(impl)
ver: str = platform.python_implementation()
print(ver)
