# probes: set supports union/intersection/difference
# expect:
# [1, 2, 3, 4]
# [2, 3]
# [1]
# [1, 4]
a = {1, 2, 3}
b = {2, 3, 4}
print(sorted(a | b))
print(sorted(a & b))
print(sorted(a - b))
print(sorted(a ^ b))
