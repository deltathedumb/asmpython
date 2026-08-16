# probes: random.shuffle permutes in place
# expect:
# 5
# [1, 2, 3, 4, 5]
import random

random.seed(4)
items = [1, 2, 3, 4, 5]
random.shuffle(items)
print(len(items))
print(sorted(items))
