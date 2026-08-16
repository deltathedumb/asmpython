# expect:
# 5
# 1
# 5
# 2
# 3
# 3
# 7
# 10

import itertools

r: list = itertools.chain([1, 2], [3, 4], [5])
print(len(r))
print(r[0])
print(r[4])

s: list = itertools.islice([0, 1, 2, 3, 4], 2, 5)
print(s[0])
print(len(s))

rep: list = itertools.repeat(7, 3)
print(len(rep))
print(rep[1])

acc: list = itertools.accumulate([1, 2, 3, 4])
print(acc[3])
