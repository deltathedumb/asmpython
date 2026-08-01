# tier: spec
# ref: library/stdtypes.html#str.index
# expect:
# 2 5
# 2 5
# -1
# ValueError
# 4
s = "abcabc"
print(s.find("c"), s.rfind("c"))
print(s.index("c"), s.rindex("c"))
print(s.find("z"))
try:
    s.index("z")
except ValueError:
    print("ValueError")
print(s.find("b", 2))
