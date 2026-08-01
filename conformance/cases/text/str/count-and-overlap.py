# tier: spec
# ref: library/stdtypes.html#str.count
# expect:
# 2
# 4
# 2
# 0
# 1
print("aaaa".count("aa"))
print("abc".count(""))
print("abcabc".count("abc"))
print("abc".count("z"))
print("abcabc".count("a", 1))
