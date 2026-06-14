# expect:
# 3.0
# 3.0
# 0
# 3
# 2.5

from statistics import mean, median, mode, variance, stdev

data = [1, 2, 3, 4, 5]
print(mean(data))
print(median(data))
print(mode([1, 1, 2, 3]))
print(mode([1, 2, 3, 3]))

print(variance([2, 3]))
