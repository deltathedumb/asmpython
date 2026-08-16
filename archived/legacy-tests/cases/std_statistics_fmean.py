# probes: statistics.fmean exists and returns float
# expect:
# 2.5
import statistics

print(statistics.fmean([1, 2, 3, 4]))
