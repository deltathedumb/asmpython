# expect:
# 3.0
# 3.0
# 2
# 2.13809

import statistics

data: list = [1, 2, 3, 4, 5]
print(statistics.mean(data))
print(statistics.median(data))
print(statistics.mode([1, 2, 2, 3]))
print(statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9]))
