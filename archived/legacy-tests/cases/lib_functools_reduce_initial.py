# expect:
# 100
from functools import reduce
print(reduce(lambda a, b: a + b, [], 100))
# asmpython (beta/3.14.0) MISMATCH: prints '9213664\n' (wrong).
