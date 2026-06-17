# expect:
# 95
# 760
# 0
# 255
# 0
# 1
# 1
# 0
from _font8x8 import _FONT

print(len(_FONT) // 8)
print(len(_FONT))
print(_FONT[0])
print(_FONT[(ord("_") - 32) * 8 + 7])

base = (ord("A") - 32) * 8
bits = _FONT[base]
print((bits >> (7 - 0)) & 1)
print((bits >> (7 - 4)) & 1)
print((bits >> (7 - 5)) & 1)
print((bits >> (7 - 7)) & 1)
