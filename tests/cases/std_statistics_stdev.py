# probes: statistics.stdev computes the sample deviation
# expect:
# 2.138089935299395
import statistics

print(statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9]))
