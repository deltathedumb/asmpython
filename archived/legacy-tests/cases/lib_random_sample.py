# expect:
# [0, 6, 9]
import random
random.seed(0)
print(sorted(random.sample(range(10), 3)))
# asmpython (beta/3.14.0) MISMATCH: prints '[6, 8, 9]\n' (wrong).
