# tier: spec
# ref: library/stdtypes.html#str.find
# expect:
# 1 4 -1
# 2 7
# XbcXbc
# Xbcabc
# True True
s = "abcabc"
print(s.find("b"), s.rfind("b"), s.find("z"))
print(s.count("a"), s.count(""))
print(s.replace("a", "X"))
print(s.replace("a", "X", 1))
print(s.startswith("ab"), s.endswith("bc"))
