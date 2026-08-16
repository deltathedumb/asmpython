# probes: a starred target absorbs the middle
# expect:
# 1
# [2, 3, 4]
# [2, 3]
# 4
first, *rest = [1, 2, 3, 4]
print(first)
print(rest)
head, *middle, tail = [1, 2, 3, 4]
print(middle)
print(tail)
