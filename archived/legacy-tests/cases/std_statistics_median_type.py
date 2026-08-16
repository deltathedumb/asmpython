# probes: median of odd ints returns the int element
# expect:
# 3
# int
import statistics

m = statistics.median([1, 3, 5])
print(m)
print(type(m).__name__)
