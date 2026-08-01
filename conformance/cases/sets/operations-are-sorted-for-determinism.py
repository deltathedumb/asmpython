# tier: spec
# ref: library/stdtypes.html#set
# expect:
# [1, 2, 3, 4]
# [3]
# [1, 2]
# [1, 2, 4]
a = {1, 2, 3}
b = {3, 4}
print(sorted(a | b))
print(sorted(a & b))
print(sorted(a - b))
print(sorted(a ^ b))
