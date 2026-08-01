# tier: spec
# ref: library/statistics.html
# expect:
# 2.5
# 2.5
# 2
# 1
# 2.1381
import statistics

data = [1, 2, 3, 4]
print(statistics.mean(data))
print(statistics.median(data))
print(statistics.median([1, 2, 3]))
print(statistics.mode([1, 1, 2]))
print(round(statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9]), 4))
