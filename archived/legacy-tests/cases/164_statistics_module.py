# expect:
# 3.0
# 3.0
# 1
# 3
# 0.5

# Note: CPython's mean()/median() return an exact int (Fraction-derived) for
# integral data ("3" not "3.0"); asmpython's statistics module is statically
# typed `-> float`, so it always prints "3.0". mode()/variance() match CPython.

from statistics import mean, median, mode, variance, stdev

data = [1, 2, 3, 4, 5]
print(mean(data))
print(median(data))
print(mode([1, 1, 2, 3]))
print(mode([1, 2, 3, 3]))

print(variance([2, 3]))
