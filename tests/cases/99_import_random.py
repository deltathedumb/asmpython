# expect:
# 32767
# 1
# 1
import random

print(random.RAND_MAX)
random.seed(42)
r1 = random.rand()
r2 = random.rand()
# values must be non-negative and within RAND_MAX
print(int(r1 >= 0 and r1 <= random.RAND_MAX))
# two successive values aren't always different, but with seed 42 they are
print(int(r1 != r2))
