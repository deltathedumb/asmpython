# probes: random.sample draws k distinct elements
# expect:
# 3
# 3
# True
import random

random.seed(3)
picked = random.sample([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 3)
print(len(picked))
print(len(set(picked)))
print(sorted(picked) == sorted(set(picked)))
