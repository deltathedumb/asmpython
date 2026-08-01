# tier: spec
# ref: library/stdtypes.html#str.upper
# expect:
# 'MiXeD'
# 'MiXeD  ' '  MiXeD'
# MIXED mixed
# Abc A B
# a
s = "  MiXeD  "
print(repr(s.strip()))
print(repr(s.lstrip()), repr(s.rstrip()))
print(s.strip().upper(), s.strip().lower())
print("abc".capitalize(), "a b".title())
print("xxaxx".strip("x"))
