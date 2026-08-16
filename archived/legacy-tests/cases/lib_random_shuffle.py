# expect:
# [3, 4, 5, 1, 2]
import random
random.seed(1)
x = [1, 2, 3, 4, 5]
random.shuffle(x)
print(x)
# asmpython (beta/3.14.0) MISMATCH: prints '[3, 1, 5, 4, 2]\n' (wrong).
