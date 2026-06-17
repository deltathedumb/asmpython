# expect:
# Windows
# x86_64
# x86_64
# 64bit

import platform

print(platform.system())
print(platform.machine())
print(platform.processor())
arch: list[str] = platform.architecture()
a0: str = arch[0]
print(a0)
