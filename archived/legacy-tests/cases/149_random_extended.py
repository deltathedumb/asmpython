# expect:
# 6
# 1
# True
# True

import random

random.seed(42)
print(random.randint(1, 10))
print(random.randint(1, 10))
u = random.uniform(0.0, 10.0)
print(u >= 0.0 and u <= 10.0)
r = random.random()
print(r >= 0.0 and r < 1.0)
