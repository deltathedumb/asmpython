# expect:
# empty data
# 5

from statistics import StatisticsError, mean

e = StatisticsError("empty data")
print(str(e))
print(int(mean([4, 5, 6])))
