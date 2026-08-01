# probes: statistics.mode returns the most common value
# expect:
# 2
import statistics

print(statistics.mode([1, 2, 2, 3]))
