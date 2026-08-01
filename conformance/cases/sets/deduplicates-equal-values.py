# tier: spec
# ref: library/stdtypes.html#set
# expect:
# 1
# [1, 2, 3]
print(len({1, 1.0, True}))
print(sorted({1, 2, 2, 3}))
