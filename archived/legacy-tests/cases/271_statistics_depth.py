# expect:
# 3.0
# 3.0
# 3.0
# 3.0
# 2.5

import statistics

data: list = [1, 2, 3, 4, 5]
print(statistics.mean(data))
print(statistics.fmean(data))
print(statistics.median(data))
print(statistics.median_low(data))
print(statistics.variance(data))
