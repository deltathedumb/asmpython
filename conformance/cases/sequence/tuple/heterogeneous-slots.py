# tier: spec
# ref: library/stdtypes.html#tuple
# expect:
# (1, 'two', 3.5, None)
# 1
# two
# 3.5
# None
# 4
t = (1, 'two', 3.5, None)
print(t)
print(t[0])
print(t[1])
print(t[2])
print(t[3])
print(len(t))
